"""core.py 测试 — 全部使用 MockLLM, 无网络依赖."""
from __future__ import annotations

import json

import pytest

from wardrail.core import WardRail, MockLLM, Run, RunStatus


def make_af(final_answer: str = "done") -> WardRail:
    """构建带一个低风险工具和一个高危工具的 WardRail, LLM 脚本可扩展."""
    af = WardRail(project="t", llm=MockLLM(scripts={"m": []}))
    af._llm_scripts = {"m": []}  # noqa: 便于测试动态追加响应

    @af.tool(risk="low")
    def add(a: int, b: int) -> int:
        return a + b

    @af.tool(risk="high")
    def publish(msg: str) -> str:
        return f"published:{msg}"

    af._agent_name = "a"  # noqa
    af._agents["a"] = {"model": "m", "fallback_models": [], "tools": ["add", "publish"]}
    af._final = final_answer  # noqa
    af._handles["a"] = af.agent("a", model="m", tools=[add, publish])
    return af


def script(af: WardRail, *responses: dict) -> None:
    """向 MockLLM 追加脚本化响应序列."""
    af.llm._scripts["m"].extend(json.dumps(r, ensure_ascii=False) for r in responses)


def test_low_risk_direct_success():
    """低风险工具直通 → SUCCEEDED, 无审批步骤."""
    af = make_af()
    script(af,
           {"thought": "算一下", "action": "add", "action_input": {"a": 1, "b": 2}},
           {"final": "3"})
    run = af._handles["a"].run("1+2=?")
    assert run.status is RunStatus.SUCCEEDED
    assert run.answer == "3"
    kinds = [s["kind"] for s in run.steps]
    assert "tool_exec" in kinds and "hitl" not in kinds
    assert run.trace_id and all("latency_ms" in s for s in run.steps if s["kind"] == "tool_result")


def test_high_risk_waits_then_approved():
    """高危无审批 → WAITING_APPROVAL; approve(True) → 执行并 SUCCEEDED, 审计含审批记录."""
    af = make_af()
    script(af,
           {"thought": "发布", "action": "publish", "action_input": {"msg": "hi"}},
           {"final": "已发布"})
    run = af._handles["a"].run("发布公告")
    assert run.status is RunStatus.WAITING_APPROVAL
    assert run.pending_action == {"tool": "publish", "input": {"msg": "hi"}, "risk": "high"}

    af._handles["a"].approve(run, decision=True)
    assert run.status is RunStatus.SUCCEEDED
    assert run.answer == "已发布"
    decisions = [s for s in run.steps if s["kind"] == "hitl_decision"]
    assert len(decisions) == 1 and decisions[0]["approved"] is True
    assert any(s["kind"] == "tool_result" and s["output"] == "published:hi" for s in run.steps)


def test_reject_terminates():
    """approve(False) → REJECTED 终止, 不执行工具."""
    af = make_af()
    script(af, {"action": "publish", "action_input": {"msg": "x"}})
    run = af._handles["a"].run("发布")
    af._handles["a"].approve(run, decision=False)
    assert run.status is RunStatus.REJECTED
    assert run.pending_action is None
    assert not any(s["kind"] == "tool_result" for s in run.steps)


def test_from_dict_resume():
    """checkpoint 序列化恢复后, approve 继续执行到完成."""
    af = make_af()
    script(af, {"action": "publish", "action_input": {"msg": "hi"}})
    run = af._handles["a"].run("发布")
    assert run.status is RunStatus.WAITING_APPROVAL

    d = run.to_dict()
    restored = Run.from_dict(d)
    assert restored.run_id == run.run_id and restored.trace_id == run.trace_id

    script(af, {"final": "已发布"})
    af._handles["a"].approve(restored, decision=True)
    assert restored.status is RunStatus.SUCCEEDED
    assert restored.answer == "已发布"


def test_approver_callback_auto_pass():
    """approver 回调返回 True 时高危直通, 不挂起."""
    af = make_af()
    af.set_approver("a", lambda tool, ctx: True)
    script(af,
           {"action": "publish", "action_input": {"msg": "hi"}},
           {"final": "ok"})
    run = af._handles["a"].run("发布")
    assert run.status is RunStatus.SUCCEEDED
