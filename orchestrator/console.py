"""WardRail 极简控制台 — 纯服务端渲染 FastAPI 应用 (端口 8000).

页面: / Agent 列表 | /runs 运行列表 | /runs/{id} 审计时间线 | /approvals 待审批.
审批 registry 为模块级全局, 跨请求共享; demo.py 会把 WardRail 实例注册进来.
"""
from __future__ import annotations

import html
import logging
import os
from typing import Any, Callable

from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from wardrail.auth import (
    ApiKeyStore,
    InMemoryUserStore,
    create_token,
    decode_token,
    load_secret,
    TOKEN_TTL,
)

app = FastAPI(title="WardRail 控制台")

# ---- 模块级全局 registry (跨请求共享) ----
REGISTRY: dict[str, Any] = {"af": None, "runs": {}}  # af: WardRail 实例, runs: run_id -> Run

# ---- 鉴权组件 ----
SECRET = load_secret()  # JWT 签名密钥: 环境变量 WARDRAIL_SECRET, 缺省随机生成(load_secret 内部会告警)
USERS = InMemoryUserStore()
# 默认管理员: 生产环境必须通过 WARDRAIL_ADMIN_USER / WARDRAIL_ADMIN_PASSWORD 覆盖默认口令!
USERS.add_user(os.environ.get("WARDRAIL_ADMIN_USER", "admin"),
               os.environ.get("WARDRAIL_ADMIN_PASSWORD", "changeme"), role="admin")
API_KEYS = ApiKeyStore()  # 控制台侧 API Key 管理 (供网关等机器调用扩展)
AUTH_COOKIE = "wr_session"
_AUTH_DISABLED = os.environ.get("WARDRAIL_AUTH_DISABLED") == "1"  # 仅测试用


def register(af: Any) -> None:
    """demo 启动时把 WardRail 实例注入控制台."""
    REGISTRY["af"] = af


# ------------------- 鉴权 -------------------
class NotAuthenticated(Exception):
    """未登录异常: 全局 handler 转换为 303 跳 /login?next=原路径."""


@app.exception_handler(NotAuthenticated)
def _handle_not_auth(request: Any, exc: NotAuthenticated) -> Any:
    return RedirectResponse(f"/login?next={request.url.path}", status_code=303)


def _require_login(request: Any) -> dict[str, Any]:
    """页面鉴权依赖: 无有效会话 cookie 则抛 NotAuthenticated.

    WARDRAIL_AUTH_DISABLED=1 (仅 pytest) 时放行.
    """
    if _AUTH_DISABLED:
        return {"sub": "test", "role": "admin"}
    token = request.cookies.get(AUTH_COOKIE, "")
    if token:
        try:
            payload = decode_token(token, SECRET)
            if payload.get("sub"):
                return payload
        except Exception:  # noqa: BLE001 — 无效/过期一律视为未登录
            pass
    raise NotAuthenticated(request.url.path)


_LOGIN_PAGE = """<!doctype html><html lang="zh"><head><meta charset="utf-8">
<title>WardRail 登录</title><style>
body{{font-family:system-ui,sans-serif;background:#f5f6f8;display:flex;justify-content:center;padding-top:12vh}}
.card{{background:#fff;padding:32px 40px;border-radius:8px;box-shadow:0 2px 8px rgba(0,0,0,.12);width:320px}}
h1{{font-size:20px;color:#1a2b4a;margin-top:0}}
input{{width:100%;padding:9px;margin:6px 0 14px;border:1px solid #d1d5db;border-radius:4px;box-sizing:border-box}}
button{{width:100%;padding:10px;background:#1a2b4a;color:#fff;border:0;border-radius:4px;cursor:pointer;font-size:15px}}
.err{{color:#dc2626;font-size:13px}}
</style></head><body><div class="card"><h1>WardRail 控制台登录</h1>
{error}<form method="post" action="/login">
<input name="username" placeholder="用户名" required>
<input name="password" type="password" placeholder="密码" required>
<input type="hidden" name="next" value="{next_path}">
<button>登录</button></form></div></body></html>"""


@app.get("/login", response_class=HTMLResponse)
def login_page(next_path: str = "/") -> str:
    """登录页: 内联 CSS 表单, next 隐藏字段记录登录后跳回目标."""
    return _LOGIN_PAGE.format(error="", next_path=html.escape(next_path, quote=True))


@app.post("/login")
def login(username: str = Form(...), password: str = Form(...), next_path: str = Form("/")) -> Any:
    """校验用户名密码, 签发 JWT 写入 HttpOnly cookie 后 303 跳回 next."""
    user = USERS.authenticate(username, password)
    if not user:
        return HTMLResponse(_LOGIN_PAGE.format(
            error='<p class="err">用户名或密码错误</p>',
            next_path=html.escape(next_path, quote=True)), status_code=200)
    token = create_token({"sub": user["username"], "role": user["role"]}, SECRET, ttl=TOKEN_TTL)
    resp = RedirectResponse(next_path if next_path.startswith("/") else "/", status_code=303)
    resp.set_cookie(AUTH_COOKIE, token, max_age=TOKEN_TTL, httponly=True,
                    samesite="lax", path="/")
    return resp


@app.post("/logout")
def logout() -> Any:
    """退出: 清除会话 cookie 并回登录页."""
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(AUTH_COOKIE, path="/")
    return resp


def _page(title: str, body: str, user: dict[str, Any] | None = None) -> str:
    """统一页面骨架 (内联 CSS, 无前端框架); user 存在时页头显示用户名 + 退出按钮."""
    header_extra = (f'<span style="float:right;font-size:14px;font-weight:400">'
                    f'👤 {html.escape(str(user.get("sub", "")))}'
                    f'<form method="post" action="/logout" style="display:inline">'
                    f'<button style="background:transparent;color:#bcd0f5;font-size:13px;'
                    f'padding:0 0 0 8px;border:0;cursor:pointer">退出</button></form></span>'
                    if user else "")
    return f"""<!doctype html><html lang="zh"><head><meta charset="utf-8">
<title>{title}</title><style>
body{{font-family:system-ui,sans-serif;margin:0;background:#f5f6f8}}
header{{background:#1a2b4a;color:#fff;padding:14px 24px;font-size:20px;font-weight:600}}
nav a{{color:#bcd0f5;margin-right:18px;text-decoration:none;font-size:14px}}
main{{max-width:960px;margin:24px auto;padding:0 16px}}
table{{width:100%;border-collapse:collapse;background:#fff;box-shadow:0 1px 3px rgba(0,0,0,.1)}}
th,td{{padding:10px 12px;border-bottom:1px solid #e5e7eb;text-align:left;font-size:14px}}
th{{background:#f0f2f5}} .tag{{padding:2px 8px;border-radius:4px;font-size:12px}}
.low{{background:#d1fae5;color:#065f46}} .high,.critical{{background:#fee2e2;color:#991b1b}}
.RUNNING{{background:#dbeafe;color:#1e40af}} .WAITING_APPROVAL{{background:#fef3c7;color:#92400e}}
.SUCCEEDED{{background:#d1fae5;color:#065f46}} .REJECTED,.FAILED{{background:#fee2e2;color:#991b1b}}
details{{background:#fff;border:1px solid #e5e7eb;margin:6px 0;padding:6px 10px}}
button{{padding:6px 14px;border:0;border-radius:4px;cursor:pointer;font-size:14px}}
.approve{{background:#16a34a;color:#fff}} .reject{{background:#dc2626;color:#fff}}
pre{{white-space:pre-wrap;font-size:12px}}
</style></head><body><header>WardRail 控制台{header_extra}
<nav><a href="/">Agent 列表</a><a href="/runs">运行记录</a><a href="/approvals">待审批</a></nav>
</header><main>{body}</main></body></html>"""


def _table(headers: list[str], rows: list[list[str]]) -> str:
    th = "".join(f"<th>{h}</th>" for h in headers)
    trs = "".join(f"<tr>{''.join(f'<td>{c}</td>' for c in r)}</tr>" for r in rows)
    return f"<table><tr>{th}</tr>{trs}</table>"


def _pre(v: Any) -> str:
    """JSON 折叠展示任意值."""
    import json
    return f"<pre>{html.escape(json.dumps(v, ensure_ascii=False, indent=2))}</pre>"


@app.get("/health")
def health() -> dict[str, str]:
    """健康检查端点."""
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def agents_page(user: dict[str, Any] = Depends(_require_login)) -> str:
    """Agent 列表: 名称 / 模型 / 工具数 / 风险分布."""
    af = REGISTRY["af"]
    rows = []
    for name, spec in (af._agents.items() if af else []):
        risks: dict[str, int] = {}
        for t in spec["tools"]:
            r = af._tools[t]["risk"]
            risks[r] = risks.get(r, 0) + 1
        dist = " ".join(f'<span class="tag {r}">{r}:{n}</span>' for r, n in risks.items())
        rows.append([html.escape(name), html.escape(spec["model"]), str(len(spec["tools"])), dist])
    body = _table(["Agent", "模型", "工具数", "风险分布"], rows) if rows else "<p>尚未注册 Agent</p>"
    return _page("Agent 列表", body, user)


@app.get("/runs", response_class=HTMLResponse)
def runs_page(user: dict[str, Any] = Depends(_require_login)) -> str:
    """最近 Run 列表."""
    rows = []
    for run in REGISTRY["runs"].values():
        dur = sum(s.get("latency_ms", 0) for s in run.steps)
        rows.append([f'<a href="/runs/{run.run_id}">{run.run_id[:12]}</a>',
                     html.escape(run.agent_name), f'<span class="tag {run.status.value}">{run.status.value}</span>',
                     str(len(run.steps)), f"{dur:.1f} ms"])
    return _page("运行记录", _table(["run_id", "Agent", "状态", "步骤数", "耗时"], rows), user)


@app.get("/runs/{run_id}", response_class=HTMLResponse)
def run_detail(run_id: str, user: dict[str, Any] = Depends(_require_login)) -> str:
    """单个 Run 的完整审计步骤时间线 (出入参 JSON 折叠)."""
    run = REGISTRY["runs"].get(run_id)
    if not run:
        return _page("未找到", f"<p>Run {html.escape(run_id)} 不存在</p>", user)
    items = [f"<details open><summary><b>{i}. {s['kind']}</b> — {s.get('tool', '')} "
             f"(+{s.get('latency_ms', 0)} ms)</summary>{_pre(s)}</details>"
             for i, s in enumerate(run.steps, 1)]
    body = (f"<h3>Run {run.run_id[:12]}</h3><p>Agent: {run.agent_name} | 状态: "
            f'<span class="tag {run.status.value}">{run.status.value}</span> | '
            f"trace_id: {run.trace_id}</p>" + "".join(items)
            + (f"<p>最终回答: {html.escape(run.answer or '')}</p>" if run.answer else ""))
    return _page("Run 详情", body, user)


@app.get("/approvals", response_class=HTMLResponse)
def approvals_page(user: dict[str, Any] = Depends(_require_login)) -> str:
    """待审批列表 (WAITING_APPROVAL 的 Run)."""
    pending = [r for r in REGISTRY["runs"].values() if r.status.value == "WAITING_APPROVAL"]
    if not pending:
        return _page("待审批", "<p>当前没有待审批的高危操作 ✔</p>", user)
    cards = []
    for run in pending:
        pa = run.pending_action or {}
        cards.append(f"""<details open><summary><b>{run.run_id[:12]}</b> —
{html.escape(run.agent_name)} / {pa.get('tool', '')} <span class="tag {pa.get('risk', 'high')}">{pa.get('risk')}</span></summary>
{_pre(pa)}<form method="post" action="/approvals/{run.run_id}/approve" style="display:inline">
<button class="approve">批准</button></form>
<form method="post" action="/approvals/{run.run_id}/reject" style="display:inline">
<button class="reject">拒绝</button></form></details>""")
    return _page("待审批", "".join(cards), user)


@app.post("/approvals/{run_id}/approve")
@app.post("/approvals/{run_id}/reject")
def decide(run_id: str, user: dict[str, Any] = Depends(_require_login),
           decision: str = Form(None)) -> Any:
    """审批动作: 调用 handle.approve 恢复执行, 然后跳回审批页."""
    request = _current_request
    approved = request.url.path.endswith("/approve")
    run = REGISTRY["runs"].get(run_id)
    af = REGISTRY["af"]
    if run and af:
        af._handles[run.agent_name].approve(run, decision=approved)
    return RedirectResponse("/approvals", status_code=303)


# FastAPI 路由函数需要访问请求路径区分 approve/reject, 用中间件暂存
_current_request: Any = None


@app.middleware("http")
async def _stash_request(request: Any, call_next: Callable) -> Any:
    global _current_request
    _current_request = request
    return await call_next(request)
