"""wd_gateway 独立 API Key 校验实现 — 不依赖 orchestrator 包, 避免包间耦合.

逻辑与 orchestrator/wardrail/auth.py 的 ApiKeyStore 一致 (复制轻量实现):
明文 key 仅在 issue() 时返回一次, 存储层只保存 sha256 哈希.
"""
from __future__ import annotations

import hashlib
import secrets
import time
from typing import Any


class ApiKeyStore:
    """内存 API Key 存储: sha256 哈希落存, 支持创建/校验/吊销/列表."""

    def __init__(self) -> None:
        # key_hash(sha256 hex) -> 元数据
        self._keys: dict[str, dict[str, Any]] = {}

    @staticmethod
    def _hash(key: str) -> str:
        return hashlib.sha256(key.encode()).hexdigest()

    def issue(self, name: str, scopes: list[str] | None = None, owner: str = "") -> tuple[str, dict[str, Any]]:
        """签发形如 "wr_live_xxxx" 的明文 key, 返回 (明文, 元数据); 明文不再可查."""
        plaintext = f"wr_live_{secrets.token_urlsafe(24)}"
        meta = {
            "id": secrets.token_hex(8),
            "name": name,
            "scopes": scopes or [],
            "owner": owner,
            "created_at": int(time.time()),
            "revoked": False,
        }
        self._keys[self._hash(plaintext)] = meta
        return plaintext, dict(meta)

    def verify(self, key: str) -> tuple[bool, dict[str, Any] | None]:
        """校验明文 key: 命中且未吊销返回 (True, meta), 否则 (False, None)."""
        meta = self._keys.get(self._hash(key))
        if meta and not meta["revoked"]:
            return True, dict(meta)
        return False, None

    def revoke(self, key_id: str) -> bool:
        """按 key id 吊销."""
        for meta in self._keys.values():
            if meta["id"] == key_id:
                meta["revoked"] = True
                return True
        return False

    def list(self) -> list[dict[str, Any]]:
        """列出全部 key 元数据 (不返回明文)."""
        return [dict(m) for m in self._keys.values()]


def verify_bearer(authorization: str, store: ApiKeyStore) -> tuple[bool, dict[str, Any] | None]:
    """解析 "Authorization: Bearer wr_live_xxx" 并校验."""
    key = authorization.removeprefix("Bearer ").strip()
    if not key:
        return False, None
    return store.verify(key)
