"""WardRail-Policy 测试 — YAML DSL / 通配 / 缓存 / 门禁接入 (M9)."""
from __future__ import annotations

import time
from pathlib import Path

from wardrail.core import MockLLM, RunStatus, WardRail
from wardrail.policy import Decision, PolicyEngine, PolicyError

ROOT = Path(__file__).resolve().parents[2]
FIN_POLICY = ROOT / "industry-kits/finance/policy/policy.yaml"


# ---- DSL 解析 ----

def test_load_finance_yaml():
    """加载 finance 套件策略: 名称/规则数/默认动作正确."""
    eng = PolicyEngine.from_yaml(FIN_POLICY)
    assert eng.name == "finance-rbac"
    assert eng.stats["rules"] == 3
    d = eng.check("role:researcher", "finance", "agent:investment-assistant", "run")
    assert d.allowed and d.rule == "rules[0]"


def test_default_deny():
    """未命中规则走 default=deny, rule 为 None."""
    eng = PolicyEngine.from_yaml(FIN_POLICY)
    d = eng.check("role:nobody", "finance", "tool:market.quote", "call")
    assert not d.allowed and d.rule is None and "default" in d.reason


def test_wildcard_and_effects():
    """内联策略: subject 列表 / resource 前缀通配 / effect=deny 优先命中."""
    eng = PolicyEngine(ttl=0)
    eng.load({"policy": "t", "default": "deny", "rules": [
        {"subject": ["role:a", "role:b"], "domain": "*", "resource": "tool:*",
         "actions": ["call"], "effect": "deny", "name": "blacklist"},
        {"subject": "role:b", "domain": "d1", "resource": "tool:x",
         "actions": ["call:*"], "name": "allow-x"},
    ]})
    assert not eng.check("role:a", "any", "tool:x", "call").allowed      # 黑名单命中
    assert eng.check("role:b", "d1", "tool:x", "call:read").allowed      # 前缀通配 action
    assert not eng.check("role:b", "d2", "tool:x", "call") .allowed      # 域不匹配
    assert not eng.check("role:c", "d1", "tool:x", "call") .allowed      # 主体不匹配


def test_action_mismatch_and_struct_errors():
    """action 不匹配拒绝; 结构非法抛 PolicyError."""
    eng = PolicyEngine.from_yaml(FIN_POLICY)
    assert not eng.check("role:teller", "finance", "tool:market.quote", "approve").allowed
    for bad in [{"rules": [{"resource": "x", "actions": ["run"]}]},        # 缺 subject
                {"rules": [{"subject": "s", "resource": "x", "actions": []}]},  # 空 actions
                {"rules": [{"subject": "s", "resource": "x",
                            "actions": ["run"], "effect": "maybe"}]}]:   # 非法 effect
        try:
            PolicyEngine().load(bad)
            assert False, f"应抛 PolicyError: {bad}"
        except PolicyError:
            pass


# ---- 决策缓存 ----

def test_cache_hit_and_reload_invalidation():
    """二次判定命中缓存; reload 后缓存清空(计数器累计不清零), 下次判定为新鲜 miss."""
    eng = PolicyEngine.from_yaml(FIN_POLICY, ttl=60)
    assert not eng.check("role:researcher", "finance", "agent:investment-assistant", "run").cached
    d2 = eng.check("role:researcher", "finance", "agent:investment-assistant", "run")
    assert d2.cached and d2.allowed
    assert eng.stats["hits"] == 1 and eng.stats["misses"] == 1
    eng.reload(FIN_POLICY)
    assert eng.stats["cached_keys"] == 0  # 缓存清空
    assert eng.stats["hits"] == 1          # 累计计数器不清零
    assert not eng.check("role:researcher", "finance", "agent:investment-assistant", "run").cached


def test_ttl_expiry():
    """TTL 过期后重新判定 (miss 再 +1)."""
    eng = PolicyEngine.from_yaml(FIN_POLICY, ttl=0.05)
    eng.check("u1", "finance", "agent:investment-assistant", "run")
    time.sleep(0.06)
    eng.check("u1", "finance", "agent:investment-assistant", "run")
    assert eng.stats["misses"] == 2 and eng.stats["hits"] == 0


# ---- 门禁接入 (integration) ----

def _make_af(policy) -> tuple[WardRail, object]:
    af = WardRail(project="finance", llm=MockLLM({"m": [
        '{"thought":"查行情","action":"market_quote","action_input":{"code":"600000"}}',
        '{"final":"10.52元"}',
    ]}), policy=policy)

    @af.tool(risk="low")
    def market_quote(code: str) -> str:
        return f"{code} -> 10.52"

    af.agent("assistant", model="m", tools=[market_quote])
    return af, None


def _analyst_engine() -> PolicyEngine:
    """两级授权齐全的测试策略: 同一主体 agent:run + tool:call 都要显式给."""
    eng = PolicyEngine(ttl=60)
    eng.load({"policy": "t", "default": "deny", "rules": [
        {"subject": "role:analyst", "domain": "finance",
         "resource": "agent:assistant", "actions": ["run"], "name": "allow-agent"},
        {"subject": "role:analyst", "domain": "finance",
         "resource": "tool:market_quote", "actions": ["call"], "name": "allow-tool"},
    ]})
    return eng


def test_tool_gate_denies_by_policy():
    """无策略命中: 工具调用被 REJECTED, 审计含 policy_gate, 不执行工具."""
    eng = PolicyEngine.from_yaml(FIN_POLICY)  # 只有 role:teller 能调 tool
    af, _ = _make_af(eng)
    run = af._handles["assistant"].run("查一下浦发银行", user_id="u1")
    assert run.status is RunStatus.REJECTED
    assert "策略拒绝" in (run.answer or "")
    kinds = [s["kind"] for s in run.steps]
    assert "policy_gate" in kinds and "tool_exec" not in kinds
    assert run.steps[-1]["kind"] == "done"


def test_tool_gate_allows_and_caches():
    """两级授权齐全: 工具照常执行; 第二次命中缓存标记."""
    af, _ = _make_af(_analyst_engine())
    run = af._handles["assistant"].run("查一下浦发银行", user_id="u1",
                                       subject="role:analyst")
    assert run.status is RunStatus.SUCCEEDED
    gates = [s for s in run.steps if s["kind"] == "policy_gate"]
    assert {g["rule"] for g in gates} == {"allow-agent", "allow-tool"}
    assert all(not g["cached"] for g in gates)
    run2 = af._handles["assistant"].run("再查一次", user_id="u1",
                                        subject="role:analyst")
    gates2 = [s for s in run2.steps if s["kind"] == "policy_gate"]
    assert all(g["cached"] for g in gates2)  # 两次判定全命中缓存


def test_agent_run_denied_before_llm():
    """agent 级门禁: 未授权主体连 LLM 都不调, 直接 REJECTED."""
    eng = PolicyEngine.from_yaml(FIN_POLICY)  # 只有 role:researcher 能 run 该 agent
    af, _ = _make_af(eng)
    run = af._handles["assistant"].run("hello", user_id="u1")
    assert run.status is RunStatus.REJECTED
    assert "策略拒绝运行 Agent" in (run.answer or "")
    assert not any(s["kind"] == "llm_call" for s in run.steps)


def test_agent_run_allowed_with_explicit_subject():
    """subject/domain 可显式传入, 覆盖 user:<id>/项目名缺省; 但工具级授权需另配."""
    eng = PolicyEngine(ttl=0)
    eng.load({"policy": "t", "default": "deny", "rules": [
        {"subject": "role:researcher", "domain": "finance",
         "resource": "agent:assistant", "actions": ["run"]}]})
    af, _ = _make_af(eng)
    run = af._handles["assistant"].run("查行情", user_id="whoever",
                                       subject="role:researcher", domain="finance")
    # agent 级放行, 但该主体无 tool:call 授权 → 工具门禁 REJECTED (两级独立判定)
    assert run.status is RunStatus.REJECTED
    assert "策略拒绝调用工具" in (run.answer or "")


def test_no_policy_backward_compatible():
    """不配 policy: 行为与 v0.1 完全一致, 审计无 policy_gate."""
    af, _ = _make_af(None)
    run = af._handles["assistant"].run("查行情", user_id="u1")
    assert run.status is RunStatus.SUCCEEDED
    assert not any(s["kind"] == "policy_gate" for s in run.steps)
