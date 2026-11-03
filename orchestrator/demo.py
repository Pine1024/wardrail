"""WardRail 演示脚本 — 两条路径: 低风险直通成功 / 高危挂起→控制台批准→完成.

启动后访问 http://localhost:8000 查看控制台; /approvals 可批准挂起的工单创建.
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path
from typing import Any

import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parent))  # 保证包导入
from wardrail.core import WardRail, MockLLM, RunStatus
import console  # 本目录的极简控制台 (app/register/REGISTRY)

af = WardRail(project="demo", llm=MockLLM(scripts={
    # "热线工单助手": 第一轮查知识库, 第二轮创建高危工单, 第三轮给最终答复
    "gpt-demo": [
        '{"thought": "先查知识库", "action": "kb_search", "action_input": {"q": "退款政策"}}',
        '{"thought": "需要建单", "action": "ticket_create", "action_input": {"title": "退款咨询", "body": "用户咨询退款"}}',
        '{"thought": "完成", "final": "已为您创建工单 #1001, 客服将在 24h 内联系。"}',
    ],
    # "行情查询助手": 一步查询后直接答复
    "gpt-quote": [
        '{"thought": "查行情", "action": "quote", "action_input": {"symbol": "600519"}}',
        '{"thought": "回答", "final": "贵州茅台(600519) 当前价 1720.00 元。"}',
    ],
}))


@af.tool(risk="low")
def kb_search(q: str) -> str:
    """知识库检索 (低风险)."""
    return f"[KB] 关于「{q}」: 退款政策为 7 天无理由。"


@af.tool(risk="high")
def ticket_create(title: str, body: str) -> dict[str, Any]:
    """创建工单 (高风险, 需人工审批)."""
    return {"ticket_id": 1001, "title": title, "body": body}


@af.tool(risk="low")
def quote(symbol: str) -> dict[str, Any]:
    """行情查询 (低风险)."""
    return {"symbol": symbol, "price": 1720.00}


hotline = af.agent("热线工单助手", model="gpt-demo", tools=[kb_search, ticket_create])
market = af.agent("行情查询助手", model="gpt-quote", tools=[quote])


def demo_paths() -> None:
    """演示两条治理路径, 并把 Run 注入控制台 registry."""
    # 路径 1: 低风险直通 → SUCCEEDED
    r1 = market.run("查一下贵州茅台的股价", user_id="u001")
    print(f"[低风险直通] status={r1.status.value} answer={r1.answer}")
    console.REGISTRY["runs"][r1.run_id] = r1
    # 路径 2: 高危挂起 → 控制台批准 → 完成 (先挂起注入, 批准留给控制台按钮)
    r2 = hotline.run("帮我提交一条退款工单", user_id="u002")
    print(f"[高危挂起] status={r2.status.value} pending={r2.pending_action}")
    console.REGISTRY["runs"][r2.run_id] = r2
    # 演示批准路径 (同时保留挂起态供控制台演示者操作前查看)
    time.sleep(1)
    if r2.status is RunStatus.WAITING_APPROVAL:
        hotline.approve(r2, decision=True)
        print(f"[批准后恢复] status={r2.status.value} answer={r2.answer}")


if __name__ == "__main__":
    console.register(af)  # WardRail 实例注入控制台
    threading.Thread(target=demo_paths, daemon=True).start()
    uvicorn.run(console.app, host="0.0.0.0", port=8000)
