"""auth.py 测试 — 密码哈希 / JWT / 用户存储 / API Key 存储, 零网络依赖."""
from __future__ import annotations

import time

import pytest

from wardrail.auth import (
    ApiKeyStore,
    AuthError,
    InMemoryUserStore,
    create_token,
    decode_token,
    hash_password,
    verify_password,
)


# ------------------- 密码哈希 -------------------
def test_password_hash_roundtrip() -> None:
    """正确密码可通过校验, 且格式为 pbkdf2$iter$salt$hash 四段."""
    stored = hash_password("s3cret!")
    parts = stored.split("$")
    assert len(parts) == 4 and parts[0] == "pbkdf2"
    assert verify_password("s3cret!", stored) is True


def test_password_wrong_rejected() -> None:
    """错误密码必须被拒绝, 且每次哈希盐不同."""
    stored = hash_password("right-password")
    assert verify_password("wrong-password", stored) is False
    assert hash_password("right-password") != stored  # 随机盐


def test_password_malformed_stored() -> None:
    """非法存储格式返回 False 而非抛异常."""
    assert verify_password("x", "not-a-valid-hash") is False


# ------------------- JWT -------------------
def test_jwt_create_decode_roundtrip() -> None:
    """签发 -> 解码还原 payload, 自动包含 iat/exp."""
    token = create_token({"sub": "alice"}, "unit-secret", ttl=60)
    assert token.count(".") == 2
    payload = decode_token(token, "unit-secret")
    assert payload["sub"] == "alice"
    assert 0 < payload["exp"] - payload["iat"] <= 60


def test_jwt_tampered_signature_rejected() -> None:
    """篡改签名/载荷必须抛 AuthError."""
    token = create_token({"sub": "alice"}, "unit-secret")
    head, body, _sig = token.split(".")
    with pytest.raises(AuthError):
        decode_token(f"{head}.{body}.AAAA", "unit-secret")
    # 篡改 payload 但保留原签名
    import base64, json
    forged = base64.urlsafe_b64encode(json.dumps({"sub": "bob", "exp": time.time() + 60}).encode()).rstrip(b"=").decode()
    with pytest.raises(AuthError):
        decode_token(f"{head}.{forged}.{_sig}", "unit-secret")
    # 换 secret
    with pytest.raises(AuthError):
        decode_token(token, "another-secret")


def test_jwt_expired_rejected() -> None:
    """过期 token 必须抛 AuthError."""
    token = create_token({"sub": "alice"}, "unit-secret", ttl=-1)  # 立即过期
    with pytest.raises(AuthError):
        decode_token(token, "unit-secret")


def test_jwt_malformed_rejected() -> None:
    """格式非法 (段数错误) 抛 AuthError."""
    with pytest.raises(AuthError):
        decode_token("not-a-jwt", "unit-secret")


# ------------------- 用户存储 -------------------
def test_user_store_authenticate() -> None:
    """用户注册/认证/查询, 密码哈希存储, 错误密码返回 None."""
    store = InMemoryUserStore()
    store.add_user("admin", "p@ss", role="admin")
    assert store.authenticate("admin", "p@ss") == {"username": "admin", "role": "admin"}
    assert store.authenticate("admin", "bad") is None
    assert store.authenticate("nobody", "p@ss") is None
    info = store.get("admin")
    assert info is not None and info["role"] == "admin"


# ------------------- API Key -------------------
def test_apikey_plaintext_once_and_hashed() -> None:
    """create 返回一次明文 key, 存储内部只保存 sha256 哈希 (明文不可回查)."""
    store = ApiKeyStore()
    plaintext, meta = store.create("ci-bot", scopes=["gateway:chat"], owner="sec-team")
    assert plaintext.startswith("wr_live_")
    assert meta["name"] == "ci-bot" and meta["scopes"] == ["gateway:chat"]
    # 明文不在任何存储值中
    import hashlib
    h = hashlib.sha256(plaintext.encode()).hexdigest()
    assert h in store._keys and plaintext not in str(store._keys.values())


def test_apikey_verify() -> None:
    """有效 key 校验通过, 错误 key 拒绝."""
    store = ApiKeyStore()
    plaintext, _ = store.create("svc")
    ok, meta = store.verify(plaintext)
    assert ok and meta is not None and meta["name"] == "svc"
    ok, meta = store.verify("wr_live_wrong_key")
    assert not ok and meta is None


def test_apikey_revoke() -> None:
    """吊销后 key 立即失效, list 不含明文."""
    store = ApiKeyStore()
    plaintext, meta = store.create("tmp")
    assert store.revoke(meta["id"]) is True
    assert store.verify(plaintext) == (False, None)
    assert store.revoke("nonexistent") is False
    keys = store.list()
    assert keys and keys[0]["id"] == meta["id"] and plaintext not in str(keys)
