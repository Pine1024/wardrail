"""行业套件加载器 — 把 industry-kits 里的 Agent YAML 变成可运行的 WardRail 注册.

- 极简 YAML 解析器: 只支持本项目 YAML 子集 (嵌套 dict/list/行内 dict/注释/行内 list),
  不引入 pyyaml 依赖, 保持 orchestrator 零第三方依赖 (除 httpx).
- load_kit(yaml_path) -> 解析 + 校验 + 向 WardRail 注册 agent 与工具.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable

from wardrail.core import WardRail

# ---- 内置工具表: ref -> (python 名, 风险, scopes, mock 实现) ----
def _mock(name: str) -> Callable[..., str]:
    def fn(**kwargs: Any) -> str:
        return f"[{name} mock] 演示输出, 参数: {kwargs}"
    fn.__name__ = name
    return fn


BUILTIN_TOOLS: dict[str, dict[str, Any]] = {
    "kb.search":         {"py": "kb_search",         "risk": "low",    "scopes": []},
    "ticket.create":     {"py": "ticket_create",     "risk": "medium", "scopes": ["connectors"]},
    "ticket.escalate":   {"py": "ticket_escalate",   "risk": "high",   "scopes": ["connectors"]},
    "market.quote":      {"py": "market_quote",      "risk": "low",    "scopes": []},
    "report.export_pdf": {"py": "report_export_pdf", "risk": "medium", "scopes": []},
    "emr.read":          {"py": "emr_read",          "risk": "low",    "scopes": []},
}

VALID_GRAPH_NODES = {"retrieve", "llm", "branch", "output", "approval"}

# ---------------- 极简 YAML 解析器 (项目子集) ----------------


def _scalar(s: str) -> Any:
    """把标量字符串转成 bool/int/float/str, 支持单双引号."""
    s = s.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        return s[1:-1]
    if s in ("true", "True"):
        return True
    if s in ("false", "False"):
        return False
    if s in ("null", "~"):
        return None
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        pass
    return s


def _split_top(s: str) -> list[str]:
    """按逗号切分, 跳过引号与花括号内的逗号."""
    parts, depth, quote, cur = [], 0, "", ""
    for ch in s:
        if quote:
            cur += ch
            if ch == quote:
                quote = ""
        elif ch in "\"'":
            quote = ch
            cur += ch
        elif ch == "{":
            depth += 1
            cur += ch
        elif ch == "}":
            depth -= 1
            cur += ch
        elif ch == "," and depth == 0:
            parts.append(cur)
            cur = ""
        else:
            cur += ch
    if cur.strip():
        parts.append(cur)
    return parts


def _inline_flow(s: str) -> Any:
    """解析行内 dict {..} 或行内 list [..]."""
    s = s.strip()
    if s.startswith("{") and s.endswith("}"):
        obj: dict[str, Any] = {}
        for item in _split_top(s[1:-1]):
            if ":" not in item:
                continue
            k, v = item.split(":", 1)
            obj[_scalar(k)] = _value(v)
        return obj
    if s.startswith("[") and s.endswith("]"):
        inner = s[1:-1].strip()
        return [] if not inner else [_value(x) for x in _split_top(inner)]
    return _scalar(s)


def _value(s: str) -> Any:
    s = s.strip()
    if s.startswith("{") or s.startswith("["):
        return _inline_flow(s)
    return _scalar(s)


def _strip_comment(line: str) -> str:
    """去掉注释 (忽略引号内的 #)."""
    quote = ""
    for i, ch in enumerate(line):
        if quote:
            if ch == quote:
                quote = ""
        elif ch in "\"'":
            quote = ch
        elif ch == "#" and (i == 0 or line[i - 1] in " \t"):
            return line[:i]
    return line


def parse_simple_yaml(text: str) -> dict[str, Any]:
    """解析本项目 YAML 子集为 dict. 仅支持 2 空格缩进的嵌套 dict/list."""
    raw = [_strip_comment(l).rstrip() for l in text.splitlines()]
    lines = [(len(l) - len(l.lstrip(" ")), l.strip())
             for l in raw]
    lines = [(ind, s) for ind, s in lines if s]

    def parse_block(i: int, indent: int) -> tuple[Any, int]:
        """从第 i 行开始解析一个缩进块, 返回 (值, 下一行号)."""
        if i >= len(lines) or lines[i][0] < indent:
            return None, i
        if lines[i][1].startswith("- "):
            items: list[Any] = []
            while i < len(lines) and lines[i][0] == indent and lines[i][1].startswith("- "):
                content = lines[i][1][2:].strip()
                if ":" in content and not content.startswith(("{", "[")):
                    # 列表项为 dict: "- key: value" 形式, 后续更深缩进行合并
                    k, v = content.split(":", 1)
                    sub: dict[str, Any] = {_scalar(k): _value(v)}
                    i += 1
                    if i < len(lines) and lines[i][0] > indent:
                        val, i = parse_block(i, lines[i][0])
                        sub[_scalar(k)].update(val)  # type: ignore[union-attr]
                    items.append(sub)
                else:
                    items.append(_value(content))
                    i += 1
            return items, i
        # dict 块
        obj: dict[str, Any] = {}
        while i < len(lines) and lines[i][0] == indent and not lines[i][1].startswith("- "):
            key, _, rest = lines[i][1].partition(":")
            rest = rest.strip()
            k = _scalar(key)
            if rest:
                obj[k] = _value(rest)
                i += 1
            else:
                i += 1
                if i < len(lines) and lines[i][0] > indent:
                    obj[k], i = parse_block(i, lines[i][0])
                else:
                    obj[k] = None
        return obj, i

    val, _ = parse_block(0, lines[0][0]) if lines else (None, 0)
    return val  # type: ignore[return-value]


# ---------------- 套件加载 ----------------


def _load_builtin_tools(af: WardRail) -> dict[str, Callable]:
    """把内置工具表注册到 WardRail, 返回 ref -> 函数 映射."""
    ref2fn: dict[str, Callable] = {}
    for ref, spec in BUILTIN_TOOLS.items():
        fn = _mock(spec["py"])
        af.tool(risk=spec["risk"], scopes=list(spec["scopes"]))(fn)
        ref2fn[ref] = fn
    return ref2fn


def _validate_graph(graph: list[Any]) -> None:
    for node in graph:
        if not isinstance(node, dict) or not node:
            raise ValueError(f"非法 graph 节点: {node!r}")
        ntype = next(iter(node))
        if ntype not in VALID_GRAPH_NODES:
            raise ValueError(f"未知 graph 节点类型: {ntype} (允许: {sorted(VALID_GRAPH_NODES)})")


def load_kit(yaml_path: str | Path, af: WardRail | None = None) -> tuple[WardRail, dict[str, Any]]:
    """加载行业套件 Agent YAML: 解析 → 校验 → 注册工具与 Agent.

    返回 (WardRail 实例, 解析后的 agent spec dict).
    """
    yaml_path = Path(yaml_path)
    data = parse_simple_yaml(yaml_path.read_text(encoding="utf-8"))
    spec = data.get("agent") if isinstance(data.get("agent"), dict) else data
    for field in ("name", "model"):
        if not spec.get(field):
            raise ValueError(f"{yaml_path}: 缺少 agent.{field}")

    af = af or WardRail(project=f"kit:{spec['name']}")
    ref2fn = _load_builtin_tools(af)

    tools: list[Callable] = []
    declared: list[dict[str, Any]] = []
    for t in spec.get("tools") or []:
        ref = t.get("ref")
        if ref not in BUILTIN_TOOLS:
            raise ValueError(f"{yaml_path}: 未知工具 ref: {ref} (内置: {sorted(BUILTIN_TOOLS)})")
        b = BUILTIN_TOOLS[ref]
        # 风险/权限以内置表为准, YAML 声明仅作交叉校验提示
        declared.append({"ref": ref, "risk": b["risk"], "scopes": list(b["scopes"]),
                         "connector": t.get("connector")})
        tools.append(ref2fn[ref])

    graph = spec.get("graph") or []
    _validate_graph(graph)

    handle = af.agent(spec["name"], model=spec["model"], tools=tools)
    loaded = {"name": spec["name"], "display_name": spec.get("display_name", spec["name"]),
              "model": spec["model"], "knowledge": spec.get("knowledge", []),
              "tools": declared, "graph": graph, "handle": handle}
    # knowledge 仅记录 (套件元数据), 不参与执行
    af._kit_meta = getattr(af, "_kit_meta", {})
    af._kit_meta[spec["name"]] = loaded
    return af, loaded


if __name__ == "__main__":  # pragma: no cover
    _, meta = load_kit(Path(__file__).resolve().parents[1] / "industry-kits/government/agents/hotline-worker.yaml")
    print(meta["name"], meta["tools"])
