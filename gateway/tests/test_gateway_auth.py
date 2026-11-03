"""wd_gateway 鉴权测试: WARDRAIL_GATEWAY_AUTH 开关行为."""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

import pytest
from starlette.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent))

import wd_gateway.main as gw  # noqa: E402


def _chat_body() -> dict:
    return {"model": "mock/local", "messages": [{"role": "user", "content": "鉴权测试"}]}


def _client(tmp_path, monkeypatch, auth: str) -> TestClient:
    """reload 网关并生成客户端; auth="1" 启用 API Key 校验."""
    monkeypatch.setenv("AUDIT_FILE", str(tmp_path / "audit.jsonl"))
    monkeypatch.setenv("GATEWAY_MOCK", "1")
    monkeypatch.setenv("WARDRAIL_GATEWAY_AUTH", auth)
    importlib.reload(gw)
    gw._quota.clear()
    gw.dlp.reset()
    return TestClient(gw.app)


def test_auth_disabled_allows_anonymous(tmp_path, monkeypatch):
    """鉴权关闭 (默认): 不带 key 也能访问."""
    with _client(tmp_path, monkeypatch, "0") as client:
        resp = client.post("/v1/chat/completions", json=_chat_body())
    assert resp.status_code == 200


def test_auth_enabled_rejects_missing_or_bad_key(tmp_path, monkeypatch):
    """鉴权开启: 无 key / 错 key 均 401 JSON."""
    with _client(tmp_path, monkeypatch, "1") as client:
        for headers in ({}, {"Authorization": "Bearer wr_live_forged"}):
            resp = client.post("/v1/chat/completions", json=_chat_body(), headers=headers)
            assert resp.status_code == 401
            assert resp.json()["error"]["type"] == "invalid_api_key"


def test_auth_enabled_valid_key_200(tmp_path, monkeypatch):
    """鉴权开启: 签发 key 后携带 Bearer 正常 200."""
    with _client(tmp_path, monkeypatch, "1") as client:
        plaintext, _meta = gw.api_key_store.issue("it")
        resp = client.post("/v1/chat/completions", json=_chat_body(),
                           headers={"Authorization": f"Bearer {plaintext}"})
        assert resp.status_code == 200
        # 吊销后同一 key 失效
        assert resp.json()["choices"][0]["message"]["content"].startswith("这是来自 wd-gateway 的模拟回复")
