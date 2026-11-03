"""sandbox 沙箱执行器单测（≥6 用例）."""
import os
import sys
from unittest import mock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from sandbox.wd_sandbox import SandboxRunner, ExecResult  # noqa: E402


def test_local_normal_exec():
    """local 模式正常执行，exit_code=0."""
    r = SandboxRunner(mode="local").run(["true"])
    assert isinstance(r, ExecResult)
    assert r.exit_code == 0 and not r.timed_out


def test_local_stdout_capture():
    """stdout 捕获."""
    r = SandboxRunner(mode="local").run(["echo", "wardrail-sandbox"])
    assert r.stdout.strip() == "wardrail-sandbox"


def test_local_nonzero_exit():
    """非零退出码透传."""
    r = SandboxRunner(mode="local").run(["sh", "-c", "echo boom >&2; exit 3"])
    assert r.exit_code == 3 and "boom" in r.stderr


def test_local_timeout_kill():
    """超时强杀并标记 timed_out."""
    r = SandboxRunner(mode="local").run(["sleep", "30"], timeout=1)
    assert r.timed_out is True and r.exit_code == -9


def test_env_whitelist():
    """环境变量白名单：未列入白名单的变量对子进程不可见."""
    os.environ["SECRET_TOKEN_XYZ"] = "s3cret"
    os.environ["WARDRAIL_RUN_ID"] = "run-42"
    try:
        r = SandboxRunner(mode="local").run(
            ["sh", "-c", "echo $SECRET_TOKEN_XYZ,$WARDRAIL_RUN_ID"])
        # 白名单内的可见，白名单外的不透传
        assert "s3cret" not in r.stdout
        assert "run-42" in r.stdout
    finally:
        del os.environ["SECRET_TOKEN_XYZ"]


def test_docker_mode_mock_args():
    """docker 模式：mock subprocess.run，断言关键安全参数存在."""
    runner = SandboxRunner(mode="docker")
    with mock.patch("sandbox.wd_sandbox.subprocess.run") as m:
        m.return_value = mock.Mock(stdout="ok", stderr="", returncode=0)
        r = runner.run(["python3", "-c", "print('hi')"], timeout=60)
    argv = m.call_args.args[0]
    assert argv[0] == "docker" and argv[1] == "run"
    for flag in ("--network=none", "--read-only", "--cap-drop=ALL",
                 "--memory=512m", "--pids-limit=100"):
        assert flag in argv, f"缺少安全参数 {flag}"
    assert "--tmpfs" in argv and "/tmp" in argv[argv.index("--tmpfs") + 1]
    assert "wardrail-sandbox:latest" in argv
    assert r.stdout == "ok" and r.exit_code == 0


def test_docker_mode_network_allowed():
    """network=True 时 docker 出网参数放开（仍非硬隔离，策略层管控）."""
    runner = SandboxRunner(mode="docker")
    with mock.patch("sandbox.wd_sandbox.subprocess.run") as m:
        m.return_value = mock.Mock(stdout="", stderr="", returncode=0)
        runner.run(["curl", "example.com"], network=True)
    argv = m.call_args.args[0]
    assert "--network=none" not in argv


def test_invalid_mode_rejected():
    """非法 SANDBOX_MODE 报 ValueError."""
    with pytest.raises(ValueError):
        SandboxRunner(mode="kata")
