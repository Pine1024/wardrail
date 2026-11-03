"""行业套件加载演示: government hotline-worker 完整对话 + finance investment-assistant 注册校验.

运行:  cd orchestrator && python demo_kit.py
"""
from __future__ import annotations

import json
from pathlib import Path

from wardrail.core import MockLLM, RunStatus
from wardrail.kitloader import load_kit

KITS = Path(__file__).resolve().parents[1] / "industry-kits"


def main() -> None:
    # ---- 1. government 套件: hotline-worker 完整对话 (kb_search → ticket_escalate → HITL) ----
    gov_llm = MockLLM(scripts={"wd-route://gov/onboard": [
        json.dumps({"thought": "先查政策知识库", "action": "kb_search",
                    "action_input": {"query": "低保申请条件"}}, ensure_ascii=False),
        json.dumps({"thought": "涉及投诉升级, 需要人工督办", "action": "ticket_escalate",
                    "action_input": {"ticket": "T-001", "reason": "群众投诉升级督办"}},
                   ensure_ascii=False),
        json.dumps({"final": "已按政策答复, 并升级督办工单 T-001 (人工已批准)。"}, ensure_ascii=False),
    ]})
    af, meta = load_kit(KITS / "government/agents/hotline-worker.yaml", af=None)
    # load_kit 已注册 mock 工具, 这里替换 LLM 为脚本序列
    af.llm = gov_llm
    handle = meta["handle"]
    print(f"=== 套件加载: {meta['display_name']} ({meta['name']}) model={meta['model']}")
    print(f"    knowledge(仅记录): {meta['knowledge']}")
    print(f"    tools: {[(t['ref'], t['risk']) for t in meta['tools']]}")

    run = handle.run("我是低保户, 投诉补贴一直没发, 帮我升级督办")
    print(f"\n--- 第一轮: 状态 = {run.status.value} (预期 waiting_approval, 无审批人)")
    assert run.status is RunStatus.WAITING_APPROVAL, run.status
    assert run.pending_action and run.pending_action["tool"] == "ticket_escalate"

    af.set_approver(meta["name"], lambda tool, ctx: True)  # 模拟审批中心同意
    run = handle.approve(run, decision=True)
    print(f"--- 审批通过后: 状态 = {run.status.value}, answer = {run.answer}")
    assert run.status is RunStatus.SUCCEEDED

    print("\n--- 审计轨迹 ---")
    for s in run.steps:
        payload = {k: v for k, v in s.items() if k not in ("ts", "kind")}
        print(f"[{s['kind']:<13}] {json.dumps(payload, ensure_ascii=False)[:160]}")

    # ---- 2. finance 套件: investment-assistant 注册兼容性校验 ----
    fin_af, fin_meta = load_kit(KITS / "finance/agents/investment-assistant.yaml")
    print(f"\n=== finance 套件注册成功: {fin_meta['display_name']} "
          f"tools={[(t['ref'], t['risk']) for t in fin_meta['tools']]} "
          f"graph={[list(n)[0] for n in fin_meta['graph']]}")
    assert fin_meta["name"] == "investment-assistant"
    assert all(list(n)[0] in {"retrieve", "llm", "approval", "output"} for n in fin_meta["graph"])
    print("\n全部演示通过 ✔")


if __name__ == "__main__":
    main()
