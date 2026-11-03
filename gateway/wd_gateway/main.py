"""wd_gateway 主入口：OpenAI 兼容网关（路由 / DLP / 配额 / 审计 / mock）。"""
from __future__ import annotations

import json
import os
import time
import uuid
from collections import deque
from collections.abc import AsyncIterator
from typing import Any

import httpx
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from .audit import AuditLogger
from .apikey import ApiKeyStore, verify_bearer
from .dlp import DLPProcessor
from .router import ModelEntry, ModelRouter

MOCK_REPLY_PREFIX = "这是来自 wd-gateway 的模拟回复: "
MOCK_STREAM_CHUNKS = 5  # 流式 mock 固定分 5 片

app = FastAPI(title="wd_gateway", version="0.1.0")
router = ModelRouter(os.environ.get("MODELS_YAML", "models.yaml"))
dlp = DLPProcessor()
audit = AuditLogger()

# 每分钟允许的最大 token 估算（MVP：1 字符 ~ 1 token 的粗略估算不做，按条数计）
RPM_LIMIT = int(os.environ.get("GATEWAY_RPM", "60"))
TPM_LIMIT = int(os.environ.get("GATEWAY_TPM", "10000"))

# ------------------- 配额：内存滑动窗口 -------------------
# {api_key: {"rpm": deque[ts], "tpm": deque[(ts, tokens)]}}
_quota: dict[str, dict[str, deque[Any]]] = {}


def _prune(window: deque[Any], now: float, ttl: float = 60.0) -> None:
    while window and now - (window[0][0] if isinstance(window[0], tuple) else window[0]) > ttl:
        window.popleft()


def check_quota(api_key: str, tokens: int) -> None:
    """滑动窗口检查 rpm/tpm，超限抛 429。"""
    now = time.time()
    entry = _quota.setdefault(api_key, {"rpm": deque(), "tpm": deque()})
    _prune(entry["rpm"], now)
    _prune(entry["tpm"], now)
    if len(entry["rpm"]) >= RPM_LIMIT:
        raise HTTPException(status_code=429, detail="rpm limit exceeded")
    if sum(t for _, t in entry["tpm"]) + tokens > TPM_LIMIT:
        raise HTTPException(status_code=429, detail="tpm limit exceeded")
    entry["rpm"].append(now)
    entry["tpm"].append((now, tokens))


# ------------------- 统计指标 -------------------
_metrics = {"requests": 0, "degraded": 0, "rate_limited": 0}


# ------------------- API Key 鉴权 -------------------
# WARDRAIL_GATEWAY_AUTH=1 时启用 Bearer API Key 校验 (默认关闭, 保持现有测试/演示兼容)
GATEWAY_AUTH_ENABLED = os.environ.get("WARDRAIL_GATEWAY_AUTH") == "1"
api_key_store = ApiKeyStore()


# ------------------- 请求/响应模型 -------------------
class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    model: str
    messages: list[ChatMessage]
    temperature: float | None = Field(default=None, ge=0, le=2)
    stream: bool = False


class MockUpstreamError(Exception):
    """模拟上游不可用，用于触发降级。"""


def _mock_reply(messages: list[ChatMessage]) -> str:
    last = messages[-1].content if messages else ""
    return MOCK_REPLY_PREFIX + last[:50]


def _mock_stream(reply: str) -> list[str]:
    """把回复均分为 5 片。"""
    size = max(1, len(reply) // MOCK_STREAM_CHUNKS)
    return [reply[i : i + size] for i in range(0, len(reply), size)][:MOCK_STREAM_CHUNKS]


async def _call_upstream(entry: ModelEntry, payload: dict[str, Any]) -> str:
    """调用真实上游；mock 端点或 GATEWAY_MOCK=1 时返回固定回复。"""
    if os.environ.get("GATEWAY_MOCK") == "1" or entry.endpoint.startswith("mock://"):
        return _mock_reply([ChatMessage(**m) for m in payload["messages"]])
    api_key = router.get_api_key(entry)
    if not api_key:
        raise MockUpstreamError(f"missing api key env {entry.api_key_env}")
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                entry.endpoint,
                headers={"Authorization": f"Bearer {api_key}"},
                json=payload,
            )
            resp.raise_for_status()
            return str(resp.json()["choices"][0]["message"]["content"])
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        raise MockUpstreamError(str(exc)) from exc


def _sse_chunk(request_id: str, model: str, content: str, finish: bool) -> str:
    """构造 OpenAI 风格 SSE data 行。"""
    data = {
        "id": request_id,
        "object": "chat.completion.chunk",
        "model": model,
        "choices": [
            {"index": 0, "delta": {"content": content} if content else {},
             "finish_reason": "stop" if finish else None}
        ],
    }
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


# ------------------- 路由 -------------------
@app.post("/v1/chat/completions")
async def chat_completions(
    req: ChatRequest, authorization: str = Header(default="")
) -> Any:
    api_key = authorization.removeprefix("Bearer ").strip() or "anonymous"
    request_id = str(uuid.uuid4())
    start = time.time()
    _metrics["requests"] += 1

    # API Key 校验: 启用鉴权时无效 key 直接 401
    if GATEWAY_AUTH_ENABLED:
        ok, meta = verify_bearer(authorization, api_key_store)
        if not ok:
            return JSONResponse({"error": {"message": "invalid or missing API key",
                                           "type": "invalid_api_key"}}, status_code=401)
        api_key = str(meta["id"]) if meta else api_key

    try:
        check_quota(api_key, sum(len(m.content) for m in req.messages))
    except HTTPException:
        _metrics["rate_limited"] += 1
        raise

    # DLP 出站脱敏：逐条消息脱敏，累计命中数
    dlp_hits = 0
    upstream_messages = []
    for m in req.messages:
        res = dlp.mask(m.content)
        dlp_hits += res.hits
        upstream_messages.append(m.model_copy(update={"content": res.text}))

    payload = {
        "model": req.model,
        "messages": [m.model_dump() for m in upstream_messages],
        "temperature": req.temperature,
        "stream": False,  # 上游统一非流式，网关自行切片
    }

    # 路由与降级：首选模型不可用时按 priority 依次降级
    primary, fallbacks = router.resolve(req.model)
    degraded = False
    reply: str | None = None
    for entry in [primary, *fallbacks]:
        try:
            reply = await _call_upstream(entry, payload)
            break
        except MockUpstreamError:
            degraded = True  # 该模型不可用，降级到下一个候选
    if reply is None:
        raise HTTPException(status_code=502, detail="all upstream models unavailable")
    if degraded:
        _metrics["degraded"] += 1

    latency_ms = int((time.time() - start) * 1000)
    completion_tokens = len(reply)
    prompt_tokens = sum(len(m.content) for m in upstream_messages)
    audit.log(
        request_id=request_id, api_key=api_key, model=req.model,
        degraded=degraded, prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens, dlp_hits=dlp_hits, latency_ms=latency_ms,
    )

    # 响应回填
    final_reply = dlp.restore(reply)
    if req.stream:
        chunks = _mock_stream(final_reply)

        async def gen() -> AsyncIterator[str]:
            for i, piece in enumerate(chunks):
                yield _sse_chunk(request_id, req.model, piece, finish=False)
            yield _sse_chunk(request_id, req.model, "", finish=True)
            yield "data: [DONE]\n\n"

        return StreamingResponse(gen(), media_type="text/event-stream")

    return JSONResponse(
        {
            "id": request_id,
            "object": "chat.completion",
            "model": req.model,
            "choices": [
                {"index": 0, "message": {"role": "assistant", "content": final_reply},
                 "finish_reason": "stop"}
            ],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
            },
            "degraded": degraded,
        }
    )


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/metrics")
async def metrics() -> dict[str, int]:
    return dict(_metrics)
