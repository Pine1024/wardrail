"""DLP 脱敏管道：正则识别敏感信息，替换占位符，响应时回填原值。

映射表存内存 dict，带 10 分钟 TTL，过期条目惰性清理。
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

# 敏感信息正则（顺序：先长后短，避免手机号被身份证部分吞掉）
_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("ID", re.compile(r"\d{17}[0-9Xx]")),        # 身份证 18 位
    ("CARD", re.compile(r"\d{15,18}[0-9Xx]|\d{15,19}")),          # 银行卡 16-19 位
    ("PHONE", re.compile(r"1\d{10}")),           # 手机号 11 位
]

TTL_SECONDS = 600  # 映射表 TTL：10 分钟


@dataclass
class MappingEntry:
    original: str
    kind: str
    created_at: float


@dataclass
class MaskResult:
    text: str
    hits: int = 0
    mapping: dict[str, MappingEntry] = field(default_factory=dict)


class DLPProcessor:
    """双向脱敏：mask() 出站脱敏，restore() 入站回填。"""

    def __init__(self, ttl_seconds: int = TTL_SECONDS) -> None:
        self.ttl = ttl_seconds
        # 占位符 -> 原文映射，进程内存态（MVP 不落盘）
        self._mapping: dict[str, MappingEntry] = {}

    def _gc(self) -> None:
        """惰性清理过期映射。"""
        now = time.time()
        expired = [k for k, v in self._mapping.items() if now - v.created_at > self.ttl]
        for k in expired:
            del self._mapping[k]

    def _next_placeholder(self, kind: str) -> str:
        """生成不冲突的自增占位符，如 [ID_1]、[CARD_2]。"""
        idx = 1
        while f"[{kind}_{idx}]" in self._mapping:
            idx += 1
        return f"[{kind}_{idx}]"

    def mask(self, text: str) -> MaskResult:
        """把文本中的敏感信息替换为占位符，返回掩码结果与映射。"""
        self._gc()
        result = MaskResult(text=text)

        def _replace(kind: str, match: re.Match[str]) -> str:
            placeholder = self._next_placeholder(kind)
            self._mapping[placeholder] = MappingEntry(
                original=match.group(0), kind=kind, created_at=time.time()
            )
            result.hits += 1
            return placeholder

        for kind, pattern in _PATTERNS:
            # 通过默认参数固化 kind，避免闭包晚绑定问题
            result.text = pattern.sub(lambda m, k=kind: _replace(k, m), result.text)
        result.mapping = dict(self._mapping)
        return result

    def restore(self, text: str) -> str:
        """把响应文本中的占位符回填为原始敏感值。"""
        self._gc()
        for placeholder, entry in self._mapping.items():
            if placeholder in text:
                text = text.replace(placeholder, entry.original)
        return text

    def reset(self) -> None:
        """清空映射（测试用）。"""
        self._mapping.clear()
