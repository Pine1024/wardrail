"""WardRail 鉴权原语 — 密码哈希(PBKDF2) / JWT(HS256) / 用户与 API Key 存储.

设计要点:
- 零重依赖: 仅用 hashlib / hmac / base64 / secrets 标准库实现, 不引入 pyjwt / passlib.
- PBKDF2-HMAC-SHA256 存储格式: "pbkdf2$iterations$salt_hex$hash_hex".
- JWT: 手写 HS256 (header.payload.signature, base64url, 无填充).
- API Key: 明文 key 仅在 create() 返回一次, 存储层只保存 sha256 哈希.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from typing import Any

# PBKDF2 迭代次数 (OWASP 2023 建议 >= 600k, 演示环境取 10w 平衡性能)
PBKDF2_ITERATIONS = 100_000

# JWT 默认有效期 (秒), 15 分钟
TOKEN_TTL = 900


class AuthError(Exception):
    """鉴权失败统一异常: 签名无效 / token 过期 / 格式错误."""


# ------------------- 密码哈希 -------------------
def hash_password(pw: str, salt: bytes | None = None) -> str:
    """PBKDF2-HMAC-SHA256 哈希, 返回 "pbkdf2$iterations$salt_hex$hash_hex"."""
    if salt is None:
        salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, PBKDF2_ITERATIONS)
    return f"pbkdf2${PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(pw: str, stored: str) -> bool:
    """校验密码; hmac.compare_digest 防时序攻击."""
    try:
        _, iters, salt_hex, hash_hex = stored.split("$")
        digest = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt_hex), int(iters))
        return hmac.compare_digest(digest.hex(), hash_hex)
    except (ValueError, TypeError):
        return False


# ------------------- JWT (HS256) -------------------
def _b64url(data: bytes) -> str:
    """base64url 无填充编码."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64url_decode(s: str) -> bytes:
    """base64url 解码, 兼容缺省填充."""
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def create_token(payload: dict[str, Any], secret: str, ttl: int = TOKEN_TTL) -> str:
    """签发 HS256 JWT: header.payload.signature, 自动补充 iat/exp."""
    now = int(time.time())
    body = {**payload, "iat": now, "exp": now + ttl}
    header = {"alg": "HS256", "typ": "JWT"}
    signing_input = (_b64url(json.dumps(header, separators=(",", ":")).encode()) + "."
                     + _b64url(json.dumps(body, separators=(",", ":")).encode()))
    sig = hmac.new(secret.encode(), signing_input.encode(), hashlib.sha256).digest()
    return f"{signing_input}.{_b64url(sig)}"


def decode_token(token: str, secret: str) -> dict[str, Any]:
    """校验签名与过期, 返回 payload; 任何失败抛 AuthError."""
    parts = token.split(".")
    if len(parts) != 3:
        raise AuthError("token 格式非法")
    signing_input = f"{parts[0]}.{parts[1]}"
    expected = hmac.new(secret.encode(), signing_input.encode(), hashlib.sha256).digest()
    try:
        got = _b64url_decode(parts[2])
    except (ValueError, TypeError) as e:
        raise AuthError("签名 base64 解码失败") from e
    if not hmac.compare_digest(expected, got):
        raise AuthError("签名校验失败")
    try:
        payload: dict[str, Any] = json.loads(_b64url_decode(parts[1]))
    except (ValueError, TypeError) as e:
        raise AuthError("payload 解析失败") from e
    if not isinstance(payload.get("exp"), (int, float)):
        raise AuthError("缺少 exp 字段")
    if time.time() > payload["exp"]:
        raise AuthError("token 已过期")
    return payload


# ------------------- 用户存储 (内存) -------------------
class InMemoryUserStore:
    """内存用户表: 仅演示/单实例用, 生产应替换为持久化后端."""

    def __init__(self) -> None:
        self._users: dict[str, dict[str, str]] = {}  # username -> {"password": stored, "role": role}

    def add_user(self, username: str, password: str, role: str = "user") -> None:
        """新增用户, 密码立即哈希存储."""
        self._users[username] = {"password": hash_password(password), "role": role}

    def authenticate(self, username: str, password: str) -> dict[str, str] | None:
        """校验用户名密码, 成功返回 {"username","role"}, 失败返回 None."""
        u = self._users.get(username)
        if u and verify_password(password, u["password"]):
            return {"username": username, "role": u["role"]}
        return None

    def get(self, username: str) -> dict[str, str] | None:
        """按用户名取用户信息 (含角色, 不含明文密码)."""
        u = self._users.get(username)
        return {"username": username, "role": u["role"]} if u else None


# ------------------- API Key 存储 -------------------
class ApiKeyStore:
    """API Key 管理: 明文仅 create 时返回一次, 存储只保留 sha256 哈希."""

    def __init__(self) -> None:
        # key_hash(sha256 hex) -> 元数据 {"id","name","scopes","owner","created_at","revoked"}
        self._keys: dict[str, dict[str, Any]] = {}

    def create(self, name: str, scopes: list[str] | None = None, owner: str = "") -> tuple[str, dict[str, Any]]:
        """生成形如 "wr_live_xxxx" 的明文 key, 返回 (明文, 元数据); 明文不再可查."""
        key_id = secrets.token_hex(8)
        plaintext = f"wr_live_{secrets.token_urlsafe(24)}"
        meta = {
            "id": key_id,
            "name": name,
            "scopes": scopes or [],
            "owner": owner,
            "created_at": int(time.time()),
            "revoked": False,
        }
        self._keys[self._hash(plaintext)] = meta
        return plaintext, dict(meta)

    @staticmethod
    def _hash(key: str) -> str:
        return hashlib.sha256(key.encode()).hexdigest()

    def verify(self, key: str) -> tuple[bool, dict[str, Any] | None]:
        """校验明文 key: 命中且未吊销返回 (True, meta), 否则 (False, None)."""
        meta = self._keys.get(self._hash(key))
        if meta and not meta["revoked"]:
            return True, dict(meta)
        return False, None

    def revoke(self, key_id: str) -> bool:
        """按 key id 吊销, 返回是否成功."""
        for meta in self._keys.values():
            if meta["id"] == key_id:
                meta["revoked"] = True
                return True
        return False

    def list(self) -> list[dict[str, Any]]:
        """列出全部 key 元数据 (永不包含明文或哈希)."""
        return [dict(m) for m in self._keys.values()]


def load_secret() -> str:
    """读取 WARDRAIL_SECRET; 缺省生成随机 secret 并打警告日志."""
    s = os.environ.get("WARDRAIL_SECRET")
    if s:
        return s
    import logging
    logging.getLogger("wardrail.auth").warning(
        "未设置 WARDRAIL_SECRET 环境变量, 已生成随机 secret — 重启后所有会话失效, 生产环境必须显式配置!")
    return secrets.token_urlsafe(32)
