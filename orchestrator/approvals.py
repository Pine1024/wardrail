"""HITL 审批回调示例: 对接 IM Webhook (企业微信/飞书/钉钉均可)."""
from __future__ import annotations

import os
import requests


def im_approver(action: str, context: dict) -> bool:
    """把审批单推送到 IM, 等待人工点击 (demo 中轮询审批中心 API)."""
    resp = requests.post(
        f"{os.environ.get('ORCHESTRATOR_URL', 'http://localhost:8000')}/approvals",
        json={"action": action, **context, "timeout": 3600},
        timeout=5,
    )
    resp.raise_for_status()
    return resp.json().get("approved", False)
