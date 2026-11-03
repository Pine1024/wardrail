"""WardRail 端到端模拟验证 (Sim V1)
模拟全链路: 用户请求 → DLP脱敏 → 策略门禁 → 工具注册/授权 → 编排(HITL) → 模型网关(mock) → 评测回归
不依赖外部服务, 用 TestClient 进程内验证; 产出 verify_report.json
"""
from __future__ import annotations
import json, sys, time
sys.path.insert(0, "registry")
sys.path.insert(0, "orchestrator")

results: list[dict] = []
def check(case: str, ok: bool, detail: str = ""):
    results.append({"case": case, "pass": ok, "detail": detail})
    print(f"{'PASS' if ok else 'FAIL'}  {case}  {detail}")

# ---------- 1. 工具注册中心 ----------
from fastapi.testclient import TestClient
import main as registry_main
rc = TestClient(registry_main.app, raise_server_exceptions=False)
rc.post("/tools", json={
    "name": "market_quote", "description": "行情查询", "input_schema": {},
    "risk_level": "low", "scopes": ["mkt:read"], "owner": "fin", "endpoint": "http://m"})
rc.post("/tools", json={
    "name": "fund_transfer", "description": "资金划转", "input_schema": {},
    "risk_level": "critical", "scopes": ["fund:write"], "owner": "fin", "endpoint": "http://f"})
r = rc.get("/tools/market_quote")
check("registry.register/lookup", r.status_code == 200 and r.json()["risk_level"] == "low")
a = rc.post("/tools/fund_transfer/authorize", params={"agent_id": "ag1"}, json=["fund:write"]).json()
check("registry.critical→HITL", a["requires_approval"] and a["sandbox_required"])
deny = rc.post("/tools/market_quote/authorize", params={"agent_id": "ag1"}, json=[])
check("registry.scope拒绝(HTTP403)", deny.status_code == 403, deny.text[:60])
allow = rc.post("/tools/market_quote/authorize", params={"agent_id": "ag1"}, json=["mkt:read"])
check("registry.scope通过(HTTP200)", allow.status_code == 200 and allow.json()["allowed"] is True)

# ---------- 2. DLP 脱敏 ----------
import re
def dlp(text: str):
    pat = r"\d{17}[0-9Xx]|\d{16,19}"
    return re.sub(pat, "[MASKED]", text), text != re.sub(pat, "[MASKED]", text)
out, masked = dlp("客户张三身份证110101199003078515, 卡号6222020200112233445")
check("dlp.mask", masked and "110101199003078515" not in out and "6222020200112233445" not in out, out[:40])

# ---------- 3. 编排内核: HITL 拒绝 / 批准 ----------
# 适配 M3 新版 core: high/critical 现挂起为 WAITING_APPROVAL,
# 用 agent.approve(run, False) 恢复为 REJECTED; 用例语义(HITL拒绝)不变。
from wardrail import WardRail, RunStatus, MockLLM
af = WardRail("sim", gateway_url="mock://gateway",
                 llm=MockLLM({"private/qwen2.5-14b": [
                     '{"thought":"t","action":"fund_transfer","action_input":{"amount":100}}']}))

@af.tool(risk="critical", scopes=["fund:write"])
def fund_transfer(amount: int) -> str:
    return f"transferred {amount}"

@af.tool(risk="low", scopes=["mkt:read"])
def market_quote(symbol: str) -> str:
    return "Q3 net profit +12%"

agent = af.agent("analyst", "private/qwen2.5-14b", [fund_transfer])
run1 = agent.run("转款100万")
if run1.status == RunStatus.WAITING_APPROVAL:
    run1 = agent.approve(run1, False)
check("flow.hitl_reject", run1.status == RunStatus.REJECTED and run1.answer is not None)
kinds = [s["kind"] for s in run1.steps]
check("flow.audit_steps", "tool_gate" in kinds and "hitl" in kinds, str(kinds))

af2 = WardRail("sim2", llm=MockLLM({"private/qwen2.5-14b": ['{"final": "Q3 net profit +12%"}']}))
@af2.tool(risk="low", scopes=["mkt:read"])
def market_quote2(symbol: str) -> str:
    return "Q3 net profit +12%"
ag2 = af2.agent("researcher", "private/qwen2.5-14b", [market_quote2])
run2 = ag2.run("查一下Q3行情")
check("flow.low_risk_direct", run2.status == RunStatus.SUCCEEDED)
trace_fields = {"run_id", "trace_id", "steps"} <= set(run2.__dict__)
check("flow.trace_complete", trace_fields)

# ---------- 4. 评测回归 ----------
sys.path.insert(0, "evals")
import run_evals as ev
scores = [ev.evaluate({"expect_contains": ["营收"]}, "2025年营收增长12%"),
          ev.evaluate({"expect_contains": ["净利润", "现金流"]}, "净利润与现金流数据如下")]
check("evals.scoring", all(s == 1.0 for s in scores))
avg = sum(scores)/len(scores)
check("evals.threshold_gate", avg >= 0.85, f"avg={avg}")

# ---------- 5. 策略(Casbin 语义模拟) ----------
policy = {"role:analyst": {"resource": "agent:analyst", "tool_risk": "<=medium"}}
def policy_allows(role, resource, risk):
    p = policy.get(role)
    if not p or p["resource"] != resource: return False
    order = ["low", "medium", "high", "critical"]
    return order.index(risk) <= order.index(p["tool_risk"].lstrip("<="))
check("policy.allow_low", policy_allows("role:analyst", "agent:analyst", "low"))
check("policy.deny_critical", not policy_allows("role:analyst", "agent:analyst", "critical"))

# ---------- 汇总 ----------
passed = sum(1 for r in results if r["pass"])
report = {
    "suite": "WardRail Sim V1",
    "time": time.strftime("%Y-%m-%d %H:%M:%S"),
    "total": len(results), "passed": passed, "failed": len(results)-passed,
    "gate": "PASS" if passed == len(results) else "FAIL",
    "results": results,
}
json.dump(report, open("verify_report.json", "w"), ensure_ascii=False, indent=2)
print(f"\n=== {passed}/{len(results)} passed, gate={report['gate']} ===")
sys.exit(0 if report["gate"] == "PASS" else 1)
