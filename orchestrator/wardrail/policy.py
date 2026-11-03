"""WardRail-Policy (M9) — 策略引擎: YAML DSL 解析 + 决策缓存 + 门禁接入.

- DSL 复用 kitloader.parse_simple_yaml (零第三方依赖), 结构对齐 industry-kits/*/policy/policy.yaml:
      policy: finance-rbac
      default: deny          # 未命中任何规则时的动作: deny / allow
      rules:
        - {subject: "role:researcher", domain: finance,
           resource: "agent:investment-assistant", actions: [run]}
- 匹配语义 (首条命中即生效):
    subject  支持单值或列表, "*" 匹配任意主体
    domain   精确或 "*"
    resource 支持 "tool:*" 式前缀通配
    actions  列表内任意一项命中即通过, 支持 "call:*" 式前缀通配
    effect   可选, 默认 allow, 显式 deny 可写白名单中的黑名单 (排列在前优先生效)
- 决策缓存: (subjects, domain, resource, action) -> Decision, TTL 过期 + reload 失效 + 命中统计.
- 门禁接入: WardRail(policy=engine) 后, agent.run 与工具调用前置策略检查, deny 即 REJECTED 并入审计.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from wardrail.kitloader import parse_simple_yaml


@dataclass
class Decision:
    """一次策略判定的结果, cached 标记是否来自缓存."""
    allowed: bool
    rule: str | None  # 命中的规则标识; 未命中(走 default)为 None
    reason: str
    cached: bool = False


class PolicyError(ValueError):
    """策略文件结构非法."""


def _match(pattern: str, value: str) -> bool:
    """通配匹配: '*' 任意; 'ns:*' 前缀通配; 其余精确."""
    if pattern == "*":
        return True
    if pattern.endswith(":*"):
        return value.startswith(pattern[:-1])
    return pattern == value


class PolicyEngine:
    """策略即代码引擎: 加载 YAML DSL, 带缓存的 allow/deny 判定."""

    def __init__(self, data: dict[str, Any] | None = None, *,
                 ttl: float = 300.0, default: str | None = None):
        """
        data: 内联策略 dict (与 YAML 解析结果同构); 传 None 后用 reload() 加载文件.
        ttl:  决策缓存秒数; 0 表示禁用缓存.
        default: 覆盖 YAML 里的 default 动作; None 则以 YAML 为准 (缺省 deny).
        """
        self.ttl = ttl
        self._default_override = default
        self._cache: dict[tuple, tuple[float, Decision]] = {}
        self._hits = 0
        self._misses = 0
        self.name = "unnamed"
        self._rules: list[dict[str, Any]] = []
        self._default = "deny"
        if data is not None:
            self.load(data)

    # ---- 加载 ----
    @classmethod
    def from_yaml(cls, path: str | Path, *, ttl: float = 300.0,
                  default: str | None = None) -> "PolicyEngine":
        eng = cls(ttl=ttl, default=default)
        eng.reload(path)
        return eng

    def load(self, data: dict[str, Any]) -> None:
        """加载内联策略 dict 并校验结构; 清空缓存."""
        if not isinstance(data, dict):
            raise PolicyError(f"策略必须是 dict, 得到 {type(data).__name__}")
        rules_raw = data.get("rules") or []
        if not isinstance(rules_raw, list):
            raise PolicyError("rules 必须是列表")
        rules: list[dict[str, Any]] = []
        for i, r in enumerate(rules_raw):
            if not isinstance(r, dict):
                raise PolicyError(f"rules[{i}] 必须是 dict")
            for field in ("subject", "resource"):
                if not r.get(field):
                    raise PolicyError(f"rules[{i}] 缺少 {field}")
            actions = r.get("actions") or []
            if isinstance(actions, str):
                actions = [actions]
            if not actions or not all(isinstance(a, str) for a in actions):
                raise PolicyError(f"rules[{i}] actions 必须是非空字符串列表")
            subjects = r["subject"]
            if isinstance(subjects, str):
                subjects = [subjects]
            if not subjects or not all(isinstance(s, str) for s in subjects):
                raise PolicyError(f"rules[{i}] subject 必须是字符串或字符串列表")
            effect = str(r.get("effect", "allow")).lower()
            if effect not in ("allow", "deny"):
                raise PolicyError(f"rules[{i}] effect 只能是 allow/deny, 得到 {effect}")
            rules.append({"subjects": list(subjects),
                          "domain": str(r.get("domain", "*")),
                          "resource": str(r["resource"]),
                          "actions": list(actions),
                          "effect": effect,
                          "name": str(r.get("name", f"rules[{i}]"))})
        d = self._default_override or str(data.get("default", "deny")).lower()
        if d not in ("allow", "deny"):
            raise PolicyError(f"default 只能是 allow/deny, 得到 {d}")
        self.name = str(data.get("policy", "unnamed"))
        self._rules = rules
        self._default = d
        self._cache.clear()

    def reload(self, source: str | Path | dict[str, Any]) -> None:
        """重新加载: 文件路径(每区变更即刷)或内联 dict; 缓存失效."""
        if isinstance(source, dict):
            self.load(source)
            return
        path = Path(source)
        self.load(parse_simple_yaml(path.read_text(encoding="utf-8")))

    # ---- 判定 ----
    def check(self, subject: str | list[str], domain: str,
              resource: str, action: str) -> Decision:
        """判定 (subject, domain, resource, action); 命中缓存直接返回."""
        subjects = (subject,) if isinstance(subject, str) else tuple(subject)
        key = (subjects, domain, resource, action)
        now = time.monotonic()
        hit = self._cache.get(key)
        if hit is not None and now - hit[0] <= self.ttl:
            self._hits += 1
            d = hit[1]
            return Decision(d.allowed, d.rule, d.reason, cached=True)
        self._misses += 1
        d = self._decide(subjects, domain, resource, action)
        self._cache[key] = (now, d)
        return d

    def _decide(self, subjects: tuple[str, ...], domain: str,
                resource: str, action: str) -> Decision:
        for r in self._rules:
            if not any(_match(p, s) for p in r["subjects"] for s in subjects):
                continue
            if not _match(r["domain"], domain):
                continue
            if not _match(r["resource"], resource):
                continue
            if not any(_match(a, action) for a in r["actions"]):
                continue
            allowed = r["effect"] == "allow"
            return Decision(allowed, r["name"],
                            f"{'允许' if allowed else '拒绝'}: 命中规则 {r['name']}")
        allowed = self._default == "allow"
        return Decision(allowed, None,
                        f"{'允许' if allowed else '拒绝'}: 无命中规则, 走 default={self._default}")

    # ---- 缓存运维 ----
    def invalidate(self) -> None:
        """手动清空决策缓存 (策略热更新由 reload 自动完成)."""
        self._cache.clear()

    @property
    def stats(self) -> dict[str, int]:
        return {"hits": self._hits, "misses": self._misses,
                "cached_keys": len(self._cache), "rules": len(self._rules)}

    def __repr__(self) -> str:  # pragma: no cover
        return f"<PolicyEngine {self.name} rules={len(self._rules)} default={self._default}>"
