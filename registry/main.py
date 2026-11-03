"""WardRail Tool Registry — 工具注册中心 + 风险分级 (M4).

每个工具必须声明: name, description, input_schema, risk_level, scopes, owner.
risk_level 决定执行档位:
  low      -> 编排器直调, 全量审计
  medium   -> 白名单 + 参数校验 + 幂等键
  high/critical -> 沙箱执行 + HITL 人工审批
"""
from __future__ import annotations

import enum
import time
import uuid
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

app = FastAPI(title="WardRail Tool Registry", version="0.1.0")


class RiskLevel(str, enum.Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ToolSpec(BaseModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{2,63}$")
    description: str
    input_schema: dict[str, Any]
    risk_level: RiskLevel
    scopes: list[str]
    owner: str
    endpoint: str  # MCP server 或 HTTP endpoint


class ToolRecord(ToolSpec):
    tool_id: str
    created_at: float


_REGISTRY: dict[str, ToolRecord] = {}


@app.post("/tools", response_model=ToolRecord)
def register_tool(spec: ToolSpec) -> ToolRecord:
    if spec.name in _REGISTRY:
        raise HTTPException(409, "tool already registered")
    rec = ToolRecord(tool_id=str(uuid.uuid4()), created_at=time.time(), **spec.model_dump())
    _REGISTRY[spec.name] = rec
    return rec


@app.get("/tools")
def list_tools(risk_level: RiskLevel | None = None) -> list[ToolRecord]:
    tools = list(_REGISTRY.values())
    if risk_level:
        tools = [t for t in tools if t.risk_level == risk_level]
    return tools


@app.get("/tools/{name}")
def get_tool(name: str) -> ToolRecord:
    if name not in _REGISTRY:
        raise HTTPException(404, "tool not found")
    return _REGISTRY[name]


@app.delete("/tools/{name}")
def deregister_tool(name: str) -> dict:
    return {"deleted": _REGISTRY.pop(name, None) is not None}


@app.post("/tools/{name}/authorize")
def authorize_call(name: str, agent_id: str, scopes: list[str]) -> dict:
    """执行前授权检查: 风险等级 + scope 匹配. high/critical 返回审批要求."""
    tool = get_tool(name)
    missing = [s for s in tool.scopes if s not in scopes]
    if missing:
        raise HTTPException(403, f"missing scopes: {missing}")
    requires_approval = tool.risk_level in (RiskLevel.HIGH, RiskLevel.CRITICAL)
    return {
        "allowed": True,
        "requires_approval": requires_approval,
        "sandbox_required": requires_approval,
        "ttl_token_seconds": 60,
    }
