"""kitloader 测试 — YAML 解析 / 工具映射 / HITL / finance 兼容."""
from __future__ import annotations

import json
from pathlib import Path

from wardrail.core import MockLLM, RunStatus
from wardrail.kitloader import BUILTIN_TOOLS, load_kit, parse_simple_yaml

KITS = Path(__file__).resolve().parents[2] / "industry-kits"
GOV = KITS / "government/agents/hotline-worker.yaml"
FIN = KITS / "finance/agents/investment-assistant.yaml"


def test_yaml_parse_correctness():
    """解析器还原 government YAML: 嵌套 dict/行内 dict/行内 list/注释."""
    data = parse_simple_yaml(GOV.read_text(encoding="utf-8"))
    agent = data["agent"]
    assert agent["name"] == "hotline-worker"
    assert agent["display_name"] == "12345热线工单助手"
    assert agent["knowledge"] == ["policies", "faq", "case-history"]
    assert agent["tools"][0] == {"ref": "kb.search", "risk": "low"}
    assert agent["tools"][1]["connector"] == "hotline-oms"
    assert agent["graph"][2]["branch"]["if"] == "intent=='complaint'"
    assert agent["graph"][2]["branch"]["else"] == "output"
    assert agent["graph"][3] == {"output": {"cite": "required"}}


def test_tool_risk_mapping():
    """工具按 ref 映射到内置表的 risk/scopes, 并实际注册到 WardRail."""
    af, meta = load_kit(GOV)
    tools = {t["ref"]: t for t in meta["tools"]}
    assert tools["kb.search"]["risk"] == "low"
    assert tools["ticket.create"]["risk"] == "medium"
    assert tools["ticket.create"]["scopes"] == BUILTIN_TOOLS["ticket.create"]["scopes"] == ["connectors"]
    assert tools["ticket.escalate"]["risk"] == "high"
    for t in meta["tools"]:
        py = BUILTIN_TOOLS[t["ref"]]["py"]
        assert py in af._tools and af._tools[py]["risk"] == t["risk"]
    assert meta["name"] in af._agents  # agent 已注册
    # mock 工具可调用
    out = af._tools["kb_search"]["fn"](query="x")
    assert "kb_search mock" in out


def test_high_tool_triggers_wait_then_approve():
    """hotline-worker: 无审批人 → WAITING_APPROVAL; approve(True) → SUCCEEDED."""
    af, meta = load_kit(GOV)
    af.llm = MockLLM(scripts={"wd-route://gov/onboard": [
        json.dumps({"thought": "查库", "action": "kb_search",
                    "action_input": {"query": "政策"}}, ensure_ascii=False),
        json.dumps({"thought": "升级", "action": "ticket_escalate",
                    "action_input": {"ticket": "T-9"}}, ensure_ascii=False),
        json.dumps({"final": "ok"}), json.dumps({"final": "ok"}),
    ]})
    handle = meta["handle"]
    run = handle.run("投诉升级")
    assert run.status is RunStatus.WAITING_APPROVAL
    assert run.pending_action == {"tool": "ticket_escalate", "input": {"ticket": "T-9"},
                                  "risk": "high"}
    af.set_approver(meta["name"], lambda tool, ctx: True)
    run = handle.approve(run, decision=True)
    assert run.status is RunStatus.SUCCEEDED and run.answer == "ok"
    kinds = [s["kind"] for s in run.steps]
    assert "hitl" in kinds and "hitl_decision" in kinds


def test_finance_kit_compatible():
    """finance YAML (含 approval 节点 / memory 字段) 能解析并注册成功."""
    af, meta = load_kit(FIN)
    assert meta["name"] == "investment-assistant"
    assert meta["model"] == "wd-route://finance/research"
    refs = [t["ref"] for t in meta["tools"]]
    assert refs == ["kb.search", "market.quote", "report.export_pdf"]
    assert {list(n)[0] for n in meta["graph"]} <= {"retrieve", "llm", "approval", "output"}
    assert set(refs) <= set(BUILTIN_TOOLS)
    assert meta["name"] in af._agents
    # 两个套件可加载到同一实例
    af2, meta2 = load_kit(GOV, af=af)
    assert {"investment-assistant", "hotline-worker"} <= set(af2._agents)  # type: ignore[operator]
    assert meta2["name"] == "hotline-worker"
