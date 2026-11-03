"""WardRail 编排内核 (M3) — ReAct 循环 / HITL / 审计 / checkpoint 恢复.

设计要点:
- 一切以 Run 为审计单元: trace_id 贯穿 LLM/工具/审批, 每步记录 latency 与出入参快照.
- 真 ReAct 循环: LLM 返回结构化 JSON {"thought","action","action_input"} 或 {"final"}.
- HITL: high/critical 风险工具挂起为 WAITING_APPROVAL, agent.approve() 恢复执行.
- LLM 抽象: LLMClient 接口 + MockLLM (脚本化响应) + OpenAICompatClient (httpx).
"""
from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Protocol

import httpx


class RunStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    REJECTED = "rejected"


HIGH_RISKS = ("high", "critical")


class LLMClient(Protocol):
    """LLM 调用抽象: 传入 system 提示与对话历史, 返回模型文本."""

    def chat(self, model: str, system: str, messages: list[dict[str, str]]) -> str: ...


class MockLLM:
    """脚本化 LLM: 每个 agent 名对应一组预设响应, 依次弹出; 便于无模型演示与测试."""

    def __init__(self, scripts: dict[str, list[str]]):
        self._scripts = {k: list(v) for k, v in scripts.items()}

    def chat(self, model: str, system: str, messages: list[dict[str, str]]) -> str:
        return self._scripts[model].pop(0)


class OpenAICompatClient:
    """OpenAI 兼容网关客户端, 超时 60s, 失败重试 1 次."""

    def __init__(self, base_url: str | None = None, timeout: float = 60.0):
        # 默认读 GATEWAY_URL 环境变量; 本地直跑兼容默认 localhost:8002
        self._base_url = (base_url or os.environ.get(
            "GATEWAY_URL", "http://localhost:8002")).rstrip("/")
        self._timeout = timeout

    def chat(self, model: str, system: str, messages: list[dict[str, str]]) -> str:
        payload = {
            "model": model,
            "messages": [{"role": "system", "content": system}, *messages],
        }
        last_err: Exception | None = None
        for attempt in range(2):  # 失败重试 1 次
            try:
                r = httpx.post(f"{self._base_url}/v1/chat/completions",
                               json=payload, timeout=self._timeout)
                r.raise_for_status()
                return str(r.json()["choices"][0]["message"]["content"])
            except Exception as e:  # noqa: BLE001 — 网关异常统一重试
                last_err = e
        raise RuntimeError(f"LLM 网关调用失败: {last_err}")


@dataclass
class Run:
    """一次运行的完整审计记录, 支持 to_dict/from_dict 序列化恢复."""
    agent_name: str
    user_id: str
    input: str
    run_id: str = ""
    trace_id: str = ""
    status: RunStatus = RunStatus.PENDING
    steps: list[dict[str, Any]] = None  # type: ignore[assignment]
    answer: str | None = None
    pending_action: dict[str, Any] | None = None  # 待审批动作 {"tool", "input", "risk"}

    def __post_init__(self) -> None:
        self.run_id = self.run_id or uuid.uuid4().hex
        self.trace_id = self.trace_id or uuid.uuid4().hex
        if self.steps is None:
            self.steps = []

    def record(self, kind: str, payload: dict[str, Any], latency_ms: float | None = None) -> None:
        entry: dict[str, Any] = {"ts": time.time(), "kind": kind, **payload}
        if latency_ms is not None:
            entry["latency_ms"] = round(latency_ms, 2)
        self.steps.append(entry)

    def to_dict(self) -> dict[str, Any]:
        """完整序列化, 供 checkpoint 落盘与恢复."""
        return {"run_id": self.run_id, "trace_id": self.trace_id,
                "agent_name": self.agent_name, "user_id": self.user_id,
                "input": self.input, "status": self.status.value,
                "steps": self.steps, "answer": self.answer,
                "pending_action": self.pending_action}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Run:
        return cls(agent_name=d["agent_name"], user_id=d["user_id"], input=d["input"],
                   run_id=d["run_id"], trace_id=d["trace_id"],
                   status=RunStatus(d["status"]), steps=list(d["steps"]),
                   answer=d.get("answer"), pending_action=d.get("pending_action"))


class WardRail:
    """SDK 入口: 注册 Agent 与工具, 执行受管 ReAct 运行."""

    def __init__(self, project: str, gateway_url: str = "http://localhost:4000",
                 registry_url: str = "http://localhost:8001",
                 llm: LLMClient | None = None,
                 sandbox_runner: Any | None = None):
        """sandbox_runner: 可选 SandboxRunner (sandbox/wd_sandbox.py).
        仅当工具声明 sandbox=True 且带 sandbox_command 时才会用到, 缺省不改变现有行为.
        注意: SandboxRunner 为容器档进程级隔离, 不是 OS 级硬沙箱.
        """
        self.project = project
        self.gateway_url = gateway_url
        self.registry_url = registry_url
        self.llm: LLMClient = llm or OpenAICompatClient()
        self.sandbox_runner = sandbox_runner
        self._tools: dict[str, dict[str, Any]] = {}
        self._agents: dict[str, dict[str, Any]] = {}
        self._approvals: dict[str, Callable[[str, dict], bool]] = {}
        self._handles: dict[str, AgentHandle] = {}

    def tool(self, risk: str = "low", scopes: list[str] | None = None,
             sandbox: bool = False) -> Callable:
        """装饰器: 声明工具及其风险等级.

        sandbox=True 时, 若 WardRail 实例配置了 sandbox_runner 且工具函数
        带 `sandbox_command` 属性(list[str]), 将改由 SandboxRunner 以进程级
        沙箱执行该命令(容器档, 非硬沙箱); 其余情况保持原行为, API 完全兼容.
        """
        def wrap(fn: Callable):
            self._tools[fn.__name__] = {"fn": fn, "risk": risk,
                                        "scopes": scopes or [], "sandbox": sandbox}
            return fn
        return wrap

    @staticmethod
    def sandboxed(command: list[str]) -> Callable:
        """标记一个函数由沙箱命令实现: fn.sandbox_command = command."""
        def deco(fn: Callable):
            fn.sandbox_command = command  # type: ignore[attr-defined]
            return fn
        return deco

    def agent(self, name: str, model: str, tools: list[Callable],
              fallback_models: list[str] | None = None) -> AgentHandle:
        self._agents[name] = {"model": model, "fallback_models": fallback_models or [],
                              "tools": [t.__name__ for t in tools]}
        handle = AgentHandle(self, name)
        self._handles[name] = handle
        return handle

    def set_approver(self, agent_name: str, approver: Callable[[str, dict], bool]) -> None:
        """注入审批回调 (IM 推送/审批中心). 返回 True=批准."""
        self._approvals[agent_name] = approver

    # ---- ReAct 内核 ----
    def _system_prompt(self, agent_name: str) -> str:
        """生成 ReAct 系统提示: 只输出 JSON, 描述可用工具."""
        tool_desc = {n: {"risk": self._tools[n]["risk"]} for n in self._agents[agent_name]["tools"]}
        return ("你是受治理的企业 Agent。每轮只输出一个 JSON 对象:\n"
                '{"thought": "...", "action": "工具名", "action_input": {...}}\n'
                "或最终回答: {\"final\": \"...\"}\n可用工具: "
                + json.dumps(tool_desc, ensure_ascii=False))

    def _execute_tool(self, run: Run, tool_name: str, tool_input: dict[str, Any]) -> Any:
        """执行工具并记录审计 (入参/出参快照 + 耗时)."""
        t = self._tools[tool_name]
        run.record("tool_exec", {"tool": tool_name, "risk": t["risk"], "input": tool_input})
        t0 = time.perf_counter()
        try:
            cmd = getattr(t["fn"], "sandbox_command", None)
            if t.get("sandbox") and self.sandbox_runner and cmd:
                # 沙箱执行: 命令级容器档隔离(进程级, 非硬沙箱), stdout 为 JSON 快照
                res = self.sandbox_runner.run(cmd)
                output = {"exit_code": res.exit_code, "timed_out": res.timed_out,
                          "stdout": res.stdout[-2000:], "stderr": res.stderr[-2000:]}
            else:
                output = t["fn"](**tool_input)
        except Exception as e:  # noqa: BLE001 — 工具异常记入审计并终止
            run.record("tool_error", {"tool": tool_name, "error": str(e)},
                       (time.perf_counter() - t0) * 1000)
            run.status = RunStatus.FAILED
            run.answer = f"工具 {tool_name} 执行失败: {e}"
            return None
        run.record("tool_result", {"tool": tool_name, "output": _safe(output)},
                   (time.perf_counter() - t0) * 1000)
        return output

    def _llm_step(self, run: Run, agent_name: str, history: list[dict[str, str]]) -> dict[str, Any]:
        """调用 LLM 拿到一轮 ReAct 决策, 记录审计."""
        spec = self._agents[agent_name]
        run.record("llm_call", {"model": spec["model"], "gateway": self.gateway_url})
        t0 = time.perf_counter()
        try:
            raw = self.llm.chat(spec["model"], self._system_prompt(agent_name), history)
        except Exception as e:  # noqa: BLE001
            run.record("llm_error", {"error": str(e)}, (time.perf_counter() - t0) * 1000)
            run.status = RunStatus.FAILED
            run.answer = f"LLM 调用失败: {e}"
            return {}
        run.record("llm_response", {"raw": raw[:2000]}, (time.perf_counter() - t0) * 1000)
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            run.status = RunStatus.FAILED
            run.answer = "LLM 输出非法 JSON"
            return {}

    def _gate(self, run: Run, tool_name: str, tool_input: dict[str, Any]) -> str:
        """风险门禁: 返回 pass / waiting / rejected. 通过后执行工具."""
        risk = self._tools[tool_name]["risk"]
        run.record("tool_gate", {"tool": tool_name, "risk": risk})
        if risk not in HIGH_RISKS:
            return "pass"
        approver = self._approvals.get(run.agent_name)
        decision = approver(tool_name, {"run_id": run.run_id, "input": tool_input}) \
            if approver else "pending"
        if decision is True:
            run.record("hitl", {"tool": tool_name, "approved": True, "via": "approver_callback"})
            return "pass"
        # 挂起等待外部审批 (控制台/回调恢复)
        run.status = RunStatus.WAITING_APPROVAL
        run.pending_action = {"tool": tool_name, "input": tool_input, "risk": risk}
        run.record("hitl", {"tool": tool_name, "approved": False,
                            "via": type(approver).__name__ if approver else "none",
                            "pending": True})
        return "waiting"

    def _resume(self, run: Run, agent_name: str) -> Run:
        """从挂起点继续 ReAct 循环 (新 run 或审批恢复共用)."""
        spec = self._agents[agent_name]
        max_steps = 8
        history: list[dict[str, str]] = [{"role": "user", "content": run.input}]
        # 已有步骤产生的工具结果回放到对话中, 保证恢复后上下文连续
        for s in run.steps:
            if s["kind"] == "tool_result":
                history.append({"role": "assistant",
                                "content": json.dumps({"action": s["tool"]}, ensure_ascii=False)})
                history.append({"role": "user",
                                "content": f"工具 {s['tool']} 结果: {s['output']}"})

        while run.status is RunStatus.RUNNING and len(
                [s for s in run.steps if s["kind"] == "llm_call"]) < max_steps:
            step = self._llm_step(run, agent_name, history)
            if not step:  # FAILED, 已记录
                break
            if "final" in step:
                run.answer = str(step["final"])
                run.status = RunStatus.SUCCEEDED
                run.record("done", {"status": run.status.value})
                break
            tool_name, tool_input = step.get("action"), step.get("action_input") or {}
            run.record("thought", {"thought": step.get("thought", "")})
            if tool_name not in self._tools:
                run.status = RunStatus.FAILED
                run.answer = f"未知工具: {tool_name}"
                break
            gate = self._gate(run, tool_name, tool_input)
            if gate == "waiting":
                break  # 挂起, 等 approve() 恢复
            out = self._execute_tool(run, tool_name, tool_input)
            if out is None and run.status is not RunStatus.RUNNING:
                break  # 工具失败
            history.append({"role": "assistant", "content": json.dumps(step, ensure_ascii=False)})
            history.append({"role": "user", "content": f"工具 {tool_name} 结果: {_safe(out)}"})
        else:
            if run.status is RunStatus.RUNNING:  # 超过 max_steps
                run.status = RunStatus.FAILED
                run.answer = "超过最大步数限制"
                run.record("done", {"status": run.status.value})
        return run


def _safe(v: Any) -> Any:
    """把任意返回值转为可 JSON 序列化快照."""
    try:
        json.dumps(v)
        return v
    except TypeError:
        return repr(v)


class AgentHandle:
    def __init__(self, af: WardRail, name: str):
        self._af, self.name = af, name

    def run(self, user_input: str, user_id: str = "anonymous") -> Run:
        """启动一次受管 ReAct 运行; 遇高危工具可能挂起为 WAITING_APPROVAL."""
        run = Run(agent_name=self.name, user_id=user_id, input=user_input)
        run.status = RunStatus.RUNNING
        run.record("start", {"agent": self.name, "model": self._af._agents[self.name]["model"]})
        return self._af._resume(run, self.name)

    def approve(self, run: Run, decision: bool, modified_input: dict[str, Any] | None = None) -> Run:
        """恢复挂起的运行: 批准则执行待审批工具并继续; 拒绝则 REJECTED 终止."""
        if run.status is not RunStatus.WAITING_APPROVAL or not run.pending_action:
            raise ValueError(f"Run {run.run_id} 不处于等待审批状态")
        pa = run.pending_action
        run.record("hitl_decision", {"tool": pa["tool"], "approved": decision,
                                     "modified_input": modified_input, "trace_id": run.trace_id})
        if not decision:
            run.status = RunStatus.REJECTED
            run.answer = f"高危操作 {pa['tool']} 被人工拒绝"
            run.pending_action = None
            run.record("done", {"status": run.status.value})
            return run
        tool_input = modified_input or pa["input"]
        out = self._af._execute_tool(run, pa["tool"], tool_input)
        run.pending_action = None
        if out is None:  # 工具执行失败, _execute_tool 已置 FAILED
            return run
        # 恢复执行: 继续后续 ReAct 循环 (工具结果已入审计, _resume 会回放上下文)
        run.status = RunStatus.RUNNING
        return self._af._resume(run, self.name)
