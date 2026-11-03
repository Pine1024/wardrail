"""core 集成 SandboxRunner 的可选行为测试（保持 API 兼容）."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "sandbox"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from orchestrator.wardrail.core import WardRail, MockLLM  # noqa: E402
from sandbox.wd_sandbox import SandboxRunner  # noqa: E402


def test_sandbox_tool_executes_via_runner():
    """sandbox=True + sandbox_command + runner 时走沙箱执行."""
    af = WardRail("p", llm=MockLLM({"m": ['{"thought":"t","action":"sh_step","action_input":{}}', '{"final":"done"}']}), sandbox_runner=SandboxRunner(mode="local"))

    @af.tool(sandbox=True)
    @WardRail.sandboxed(["echo", "hello-sandbox"])
    def sh_step() -> str:  # 原 python 实现不会被调用
        return "should-not-run"

    af.agent("a", "m", [sh_step])

    r = af._handles["a"].run("go")
    assert r.status.value == "succeeded"
    rec = [s for s in r.steps if s["kind"] == "tool_result"][0]
    assert rec["output"]["exit_code"] == 0 and "hello-sandbox" in rec["output"]["stdout"]


def test_no_runner_keeps_old_behavior():
    """未配置 sandbox_runner 时行为不变（向后兼容）."""
    af = WardRail("p", llm=MockLLM({"m": ['{"thought":"t","action":"plain","action_input":{}}', '{"final":"done"}']}))

    @af.tool(sandbox=True)
    def plain() -> str:
        return "plain-ok"

    af.agent("a", "m", [plain])

    r = af._handles["a"].run("go")
    assert r.answer == "done"
    rec = [s for s in r.steps if s["kind"] == "tool_result"][0]
    assert rec["output"] == "plain-ok"
