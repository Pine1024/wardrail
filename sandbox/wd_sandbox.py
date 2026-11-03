"""WardRail 沙箱执行器 — 容器档进程级隔离（**非 OS 级硬沙箱**）.

⚠️ 隔离级别声明：
    local / docker 两种模式均为 **进程级隔离**，不是内核级硬沙箱；
    需要强隔离（多租户不可信代码、内核攻击面）请使用 Firecracker、
    gVisor 或云厂商函数沙箱等专业方案。本文档与对外宣传均不得
    声称"硬隔离"。

两种模式（环境变量 SANDBOX_MODE）：
- local（默认）：subprocess + resource 资源限制 + 环境变量白名单，
  适用于无 docker 的开发环境。
- docker：`docker run --rm --network=none --read-only --cap-drop=ALL
  --memory=512m --pids-limit=100` + tmpfs /tmp，出网默认禁止。
"""
from __future__ import annotations

import os
import resource
import shlex
import subprocess
from dataclasses import dataclass, field


@dataclass
class ExecResult:
    """一次命令执行的完整结果快照."""
    stdout: str = ""
    stderr: str = ""
    exit_code: int = -1
    timed_out: bool = False
    command: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"stdout": self.stdout, "stderr": self.stderr,
                "exit_code": self.exit_code, "timed_out": self.timed_out}


# 环境变量白名单：未列入白名单的变量不会传入子进程
ENV_WHITELIST = ("PATH", "HOME", "LANG", "LC_ALL", "TZ", "TMPDIR",
                 "WARDRAIL_PROJECT", "WARDRAIL_RUN_ID")


class SandboxRunner:
    """进程级沙箱执行器（容器档，非硬沙箱）.

    用法::

        runner = SandboxRunner()          # 读 SANDBOX_MODE，默认 local
        r = runner.run(["echo", "hi"], timeout=10)
        assert r.stdout == "hi\\n" and r.exit_code == 0
    """

    def __init__(self, mode: str | None = None,
                 env_whitelist: tuple[str, ...] = ENV_WHITELIST,
                 memory_limit_mb: int = 512,
                 pids_limit: int = 100):
        # SANDBOX_MODE=docker 切容器模式；默认 local（subprocess 进程级隔离）
        self.mode = (mode or os.environ.get("SANDBOX_MODE", "local")).lower()
        if self.mode not in ("local", "docker"):
            raise ValueError(f"未知 SANDBOX_MODE: {self.mode}（可选 local/docker）")
        self._env_whitelist = env_whitelist
        self._memory_limit_mb = memory_limit_mb
        self._pids_limit = pids_limit

    # ---- docker ----
    def _docker_argv(self, command: list[str], workdir: str | None,
                     timeout: int, network: bool) -> list[str]:
        """构造 docker run 参数：默认禁网、只读根文件系统、drop 全部 capabilities."""
        argv = ["docker", "run", "--rm", "--network=none" if not network else "--network=bridge",
                "--read-only", "--cap-drop=ALL",
                f"--memory={self._memory_limit_mb}m",
                f"--pids-limit={self._pids_limit}",
                "--tmpfs", "/tmp:rw,size=64m",
                # 内存超限由 docker cgroup 强杀
                f"--stop-timeout={max(1, timeout)}"]
        if workdir:
            argv += ["-w", workdir]
        argv += ["wardrail-sandbox:latest"] + command
        return argv

    def _build_env(self) -> dict[str, str]:
        """按白名单过滤当前环境变量，未列入的变量对子进程不可见."""
        return {k: os.environ[k] for k in self._env_whitelist if k in os.environ}

    # ---- local ----
    def _preexec(self) -> None:  # pragma: no cover - 子进程内执行
        """子进程预执行钩子：resource 资源限制（CPU/内存/进程数/文件大小）。"""
        mb = self._memory_limit_mb
        resource.setrlimit(resource.RLIMIT_AS, (mb * 1024 * 1024,) * 2)
        resource.setrlimit(resource.RLIMIT_NPROC, (self._pids_limit,) * 2)
        resource.setrlimit(resource.RLIMIT_FSIZE, (64 * 1024 * 1024,) * 2)
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))  # 禁 core dump

    def run(self, command: list[str], workdir: str | None = None,
            timeout: int = 900, network: bool = False) -> ExecResult:
        """执行命令并返回 ExecResult；超时强杀并标记 timed_out=True."""
        if isinstance(command, str):
            command = shlex.split(command)
        env = self._build_env()
        if self.mode == "docker":
            argv = self._docker_argv(command, workdir, timeout, network)
        else:
            # local 档：进程级隔离。network 参数在 local 档无内核级强制力，
            # 由调用方通过策略层保证（见 ROADMAP 〇、禁止事项：非硬沙箱）
            argv = command
        try:
            proc = subprocess.run(
                argv, cwd=workdir, env=env, timeout=timeout,
                capture_output=True, text=True,
                preexec_fn=self._preexec if self.mode == "local" else None)
            return ExecResult(stdout=proc.stdout, stderr=proc.stderr,
                              exit_code=proc.returncode, command=argv)
        except subprocess.TimeoutExpired as e:
            # 超时：subprocess 已 kill 进程，标记 timed_out
            return ExecResult(
                stdout=e.stdout.decode() if isinstance(e.stdout, bytes) else (e.stdout or ""),
                stderr=e.stderr.decode() if isinstance(e.stderr, bytes) else (e.stderr or ""),
                exit_code=-9, timed_out=True, command=argv)
        except FileNotFoundError as e:
            return ExecResult(stderr=f"命令不存在: {e}", exit_code=127, command=argv)
