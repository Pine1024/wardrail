"""审计日志：每次请求追加一条 JSON 到 audit.jsonl。"""
from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TextIO


@dataclass
class AuditRecord:
    request_id: str
    ts: float
    api_key: str
    model: str
    degraded: bool
    prompt_tokens: int
    completion_tokens: int
    dlp_hits: int
    latency_ms: int


class AuditLogger:
    """追加写 audit.jsonl，每行一个 JSON 对象。"""

    def __init__(self, path: str | Path = "audit.jsonl") -> None:
        self.path = Path(os.environ.get("AUDIT_FILE", path))
        self._fh: TextIO | None = None

    def _ensure_open(self) -> TextIO:
        if self._fh is None or self._fh.closed:
            self._fh = open(self.path, "a", encoding="utf-8")
        return self._fh

    def log(
        self,
        request_id: str,
        api_key: str,
        model: str,
        degraded: bool,
        prompt_tokens: int,
        completion_tokens: int,
        dlp_hits: int,
        latency_ms: int,
    ) -> None:
        record = AuditRecord(
            request_id=request_id,
            ts=time.time(),
            api_key=api_key,
            model=model,
            degraded=degraded,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            dlp_hits=dlp_hits,
            latency_ms=latency_ms,
        )
        fh = self._ensure_open()
        fh.write(json.dumps(asdict(record), ensure_ascii=False) + "\n")
        fh.flush()  # 立即落盘，避免崩溃丢日志

    def close(self) -> None:
        if self._fh and not self._fh.closed:
            self._fh.close()
