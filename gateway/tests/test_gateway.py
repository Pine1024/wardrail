"""wd_gateway 测试：mock 模式下覆盖正常请求/流式/降级/DLP/429。"""
from __future__ import annotations

import importlib
import json
import os
import sys
from pathlib import Path

import pytest
from httpx import Response
from starlette.testclient import TestClient

# 保证能导入 wd_gateway 包
sys.path.insert(0, str(Path(__file__).parent.parent))

os.environ.setdefault("GATEWAY_MOCK", "1")

import wd_gateway.main as gw  # noqa: E402


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """每个测试用独立实例：独立审计文件、清空配额与映射。"""
    monkeypatch.setenv("AUDIT_FILE", str(tmp_path / "audit.jsonl"))
    monkeypatch.setenv("GATEWAY_MOCK", "1")
    importlib.reload(gw)
    gw._quota.clear()
    gw.dlp.reset()
    gw._metrics.update({"requests": 0, "degraded": 0, "rate_limited": 0})
    with TestClient(gw.app) as c:
        yield c


def _chat_body(model: str = "mock/local", **kw) -> dict:
    return {
        "model": model,
        "messages": [{"role": "user", "content": kw.pop("content", "你好网关")}],
        **kw,
    }


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_normal_request(client):
    resp = client.post("/v1/chat/completions", json=_chat_body())
    assert resp.status_code == 200
    data = resp.json()
    assert data["object"] == "chat.completion"
    content = data["choices"][0]["message"]["content"]
    assert content.startswith("这是来自 wd-gateway 的模拟回复: ")
    assert "你好网关" in content


def test_stream_request(client):
    body = _chat_body(stream=True, content="流式测试消息内容")
    with client.stream("POST", "/v1/chat/completions", json=body) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        lines = [l for l in resp.iter_text() if l.strip()]
    text = "".join(lines)
    assert "[DONE]" in text
    chunks = [json.loads(l.removeprefix("data: ")) for l in text.splitlines()
              if l.startswith("data: ") and l != "data: [DONE]" and "chunk" in l]
    # mock 分 5 片
    content_chunks = [c for c in chunks if c["choices"][0]["delta"].get("content")]
    assert len(content_chunks) == 5
    assert "".join(c["choices"][0]["delta"]["content"] for c in content_chunks).count("流式测试") == 1


def test_dlp_mask_and_restore(client):
    # 测试号码用「9」开头的全零序列 + X 校验位：格式与真实身份证号一致（能命中
    # 脱敏正则），但不对应任何真实公民，避免公开仓库被隐私扫描工具误报。
    secret_id = "9" + "0" * 16 + "X"
    phone = "13812345678"
    resp = client.post(
        "/v1/chat/completions",
        json=_chat_body(content=f"我的身份证是{secret_id}，手机号{phone}"),
    )
    assert resp.status_code == 200
    content = resp.json()["choices"][0]["message"]["content"]
    # 回填后响应包含原始敏感值，且不含占位符
    assert secret_id in content and phone in content
    assert "[ID_" not in content and "[PHONE_" not in content


def test_dlp_hits_in_audit(client, tmp_path):
    audit_file = Path(os.environ["AUDIT_FILE"])
    fake_id = "9" + "0" * 16 + "X"
    client.post("/v1/chat/completions", json=_chat_body(content=f"身份证{fake_id}"))
    lines = audit_file.read_text(encoding="utf-8").strip().splitlines()
    rec = json.loads(lines[-1])
    assert rec["dlp_hits"] == 1
    assert rec["model"] == "mock/local"


def _fresh_client() -> TestClient:
    """reload 后生成绑定新 app 的客户端，并清空内存状态。"""
    gw._quota.clear()
    gw.dlp.reset()
    gw._metrics.update({"requests": 0, "degraded": 0, "rate_limited": 0})
    return TestClient(gw.app)


def test_degradation(tmp_path, monkeypatch):
    """非 mock 模式下首选模型缺 key -> 降级到 mock://local。"""
    monkeypatch.setenv("AUDIT_FILE", str(tmp_path / "audit.jsonl"))
    monkeypatch.delenv("GATEWAY_MOCK", raising=False)
    importlib.reload(gw)
    with _fresh_client() as client:
        resp = client.post("/v1/chat/completions", json=_chat_body(model="private/qwen2.5-14b"))
    assert resp.status_code == 200
    data = resp.json()
    assert data["degraded"] is True  # 降级发生
    assert "模拟回复" in data["choices"][0]["message"]["content"]


def test_quota_429(tmp_path, monkeypatch):
    monkeypatch.setenv("AUDIT_FILE", str(tmp_path / "audit.jsonl"))
    monkeypatch.setenv("GATEWAY_RPM", "3")
    importlib.reload(gw)
    with _fresh_client() as client:
        codes = []
        for _ in range(5):
            r = client.post("/v1/chat/completions", json=_chat_body())
            codes.append(r.status_code)
        m = client.get("/metrics").json()
    assert codes == [200, 200, 200, 429, 429]
    assert m["rate_limited"] == 2


def test_metrics(client):
    client.post("/v1/chat/completions", json=_chat_body())
    m = client.get("/metrics").json()
    assert m["requests"] >= 1
    assert "degraded" in m and "rate_limited" in m


def test_audit_written(client, tmp_path):
    client.post("/v1/chat/completions", json=_chat_body())
    audit_file = Path(os.environ["AUDIT_FILE"])
    assert audit_file.exists()
    rec = json.loads(audit_file.read_text(encoding="utf-8").strip().splitlines()[-1])
    for key in ("request_id", "ts", "api_key", "model", "degraded",
                "prompt_tokens", "completion_tokens", "dlp_hits", "latency_ms"):
        assert key in rec
