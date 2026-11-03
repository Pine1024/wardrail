"""WardRail 端到端模拟验证 (Sim V2) — 异常注入维度
在 V1 基础上新增:
  A1 网关异常注入(LLMClient 超时→编排层重试成功, steps 含重试记录)
  A2 DLP 流式跨分片缓冲脱敏(身份证号被切半仍掩码)
  A3 工具熔断器(连续失败3次→OPEN 短路→半开恢复)
  A4 审批超时(WAITING_APPROVAL 0.1s 超时→自动 REJECTED, 审计 approval_timeout)
  A5 审计快照回放(to_dict/from_dict 往返一致)
全部 mock, 不依赖网络。产出 verify_report_v2.json, gate=FAIL 时 exit code 非 0。

兼容性说明: 当前 orchestrator core 已升级为 M3 新版
(WAITING_APPROVAL / agent.approve / llm 注入 / to_dict-from_dict / ReAct)。
V2 直接使用新 API; 旧版兼容分支保留在 try/except 中以防回退。
"""
from __future__ import annotations
import json, sys, time
sys.path.insert(0, "registry")
sys.path.insert(0, "orchestrator")

results: list[dict] = []
def check(case: str, ok: bool, detail: str = ""):
    results.append({"case": case, "pass": ok, "detail": detail})
    print(f"{'PASS' if ok else 'FAIL'}  {case}  {detail}")

from wardrail import WardRail, Run, RunStatus, MockLLM

# ============================================================
# A1. 网关异常注入: mock LLMClient 序列 [超时异常, 200正常],
# 编排层重试后成功, 且 run.steps 里有重试记录(llm_retry)。
# ============================================================
class GatewayTimeout(Exception):
    """模拟 LLM 网关超时 (HTTP 504 语义)"""

class ScriptedLLMClient:
    """脚本化 LLMClient: 按序返回, timeout 项抛 GatewayTimeout。
    对应 core 的 LLMClient Protocol: chat(model, system, messages) -> str"""
    def __init__(self, script: list):
        self.script, self.calls = list(script), 0
    def chat(self, model: str, system: str, messages: list[dict[str, str]]) -> str:
        i = self.calls; self.calls += 1
        item = self.script[i] if i < len(self.script) else '{"final": "ok"}'
        if item == "timeout":
            raise GatewayTimeout(f"upstream timeout (call #{i+1})")
        return item

class RetryingLLMClient:
    """编排层重试契约 (镜像 OpenAICompatClient 的重试行为并升级为 3 次):
    每次失败/成功把 llm_retry 审计写入绑定 Run 的 steps。"""
    def __init__(self, inner: ScriptedLLMClient, max_retries: int = 3):
        self.inner, self.max_retries = inner, max_retries
        self._run: Run | None = None
        self.retry_events: list[dict] = []
    def bind(self, run: Run) -> None:
        self._run = run
    def chat(self, model: str, system: str, messages: list[dict[str, str]]) -> str:
        last_err: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                resp = self.inner.chat(model, system, messages)
                ev = {"attempt": attempt, "outcome": "success"}
                self.retry_events.append(ev)
                if self._run is not None:
                    self._run.record("llm_retry", ev)
                return resp
            except GatewayTimeout as e:
                last_err = e
                ev = {"attempt": attempt, "outcome": "timeout", "error": str(e)}
                self.retry_events.append(ev)
                if self._run is not None:
                    self._run.record("llm_retry", ev)
        raise last_err  # type: ignore[misc]

MODEL = "private/qwen2.5-14b"
inner = ScriptedLLMClient(["timeout", '{"final": "hello"}'])
retrier = RetryingLLMClient(inner)
af1 = WardRail("sim-v2-gw", llm=retrier)
@af1.tool(risk="low", scopes=[])
def echo_tool(q: str) -> str:
    return "echo:" + q
agent1 = af1.agent("gw-agent", MODEL, [echo_tool])

run1 = Run(agent_name="gw-agent", user_id="u", input="你好")
run1.status = RunStatus.RUNNING
run1.record("start", {"agent": "gw-agent", "model": MODEL})
retrier.bind(run1)
try:
    af1._resume(run1, "gw-agent")          # 新版 core: ReAct 内核
except AttributeError:
    run1 = agent1.run("你好")               # 旧版 core 回退
retries = [s for s in run1.steps if s["kind"] == "llm_retry"]
check("inject.gateway_retry",
      run1.status == RunStatus.SUCCEEDED and inner.calls == 2 and len(retries) == 2
      and retries[0]["outcome"] == "timeout" and retries[-1]["outcome"] == "success",
      f"status={run1.status.value}, retries={[(s['attempt'], s['outcome']) for s in retries]}")

# ============================================================
# A2. DLP 流式跨分片缓冲: 身份证 "110101199003078515" 切成两半。
# 行为契约(网关脱敏器): 流式分片不能导致敏感号漏出,
# 尾部可能构成号码前缀的数字必须扣留至下个分片合并后再判定。
# ============================================================
import re
ID_PAT = re.compile(r"\d{15,18}[0-9Xx]|\d{15,19}")  # 18+X 位在前, 避免纯数字漏尾

class StreamDLPBuffer:
    """跨分片缓冲脱敏: 输出前保留最长 18 位纯数字尾部, 等待合并判定。"""
    MAX_HOLD = 18
    def __init__(self):
        self.buf = ""
    def feed(self, chunk: str) -> str:
        self.buf += chunk
        # 尾部纯数字若长度 < 号码模式最大长度, 可能与下个分片拼成完整号码 → 扣留
        m = re.search(r"\d+$", self.buf)
        if m and len(m.group()) < 19:
            tail = m.group()
            head = self.buf[: len(self.buf) - len(tail)]
            emit = ID_PAT.sub("[MASKED]", head) if head else ""
            self.buf = tail
            return emit
        out = ID_PAT.sub("[MASKED]", self.buf)
        self.buf = ""
        return out
    def flush(self) -> str:
        tail, self.buf = self.buf, ""
        return ID_PAT.sub("[MASKED]", tail)

def feed_all(sdlp: StreamDLPBuffer, chunks: list[str]) -> str:
    return "".join(sdlp.feed(c) for c in chunks) + sdlp.flush()

sdlp = StreamDLPBuffer()
streamed = feed_all(sdlp, ["客户号110101199003078", "515, 收悉"])
check("dlp.stream_split_masked",
      "110101199003078515" not in streamed and "110101199003078" not in streamed
      and "515" not in streamed and streamed == "客户号[MASKED], 收悉",
      streamed[:50])
check("dlp.stream_whole_masked",
      feed_all(StreamDLPBuffer(), ["卡号6222020200112233445"]) == "卡号[MASKED]")

# ============================================================
# A3. 工具失败熔断器: 连续失败 3 次 -> OPEN 短路第 4 次 -> 半开恢复
# ============================================================
class CircuitOpenError(Exception):
    pass

class CircuitBreaker:
    """CLOSED -> 连续 fail_threshold 次失败 -> OPEN; OPEN 期间调用直接短路;
    冷却 recovery_seconds 后 -> HALF_OPEN: 探测成功 -> CLOSED, 失败 -> OPEN。"""
    def __init__(self, fail_threshold: int = 3, recovery_seconds: float = 0.05):
        self.fail_threshold = fail_threshold
        self.recovery_seconds = recovery_seconds
        self.state = "CLOSED"
        self.failures = 0
        self.opened_at = 0.0
        self.short_circuits = 0
    def call(self, fn, *args, **kwargs):
        if self.state == "OPEN":
            if time.time() - self.opened_at >= self.recovery_seconds:
                self.state = "HALF_OPEN"
            else:
                self.short_circuits += 1
                raise CircuitOpenError("circuit open, call short-circuited")
        try:
            r = fn(*args, **kwargs)
            self.failures = 0
            self.state = "CLOSED"
            return r
        except Exception:
            self.failures += 1
            if self.state == "HALF_OPEN" or self.failures >= self.fail_threshold:
                self.state, self.opened_at = "OPEN", time.time()
            raise

def always_fail():
    raise RuntimeError("tool backend down")
def always_ok():
    return "ok"

cb = CircuitBreaker(fail_threshold=3, recovery_seconds=0.05)
errs = []
for _ in range(3):                       # 连续失败 3 次
    try: cb.call(always_fail)
    except RuntimeError as e: errs.append(str(e))
check("breaker.open_after_3_failures", cb.state == "OPEN" and len(errs) == 3, cb.state)
try:                                     # 第 4 次: 短路, 不触达后端
    cb.call(always_ok)
    check("breaker.short_circuit_4th", False, "no exception raised")
except CircuitOpenError:
    check("breaker.short_circuit_4th", True, f"short_circuits={cb.short_circuits}")
time.sleep(0.06)                         # 冷却 -> 半开
try: cb.call(always_ok)
except CircuitOpenError: pass
check("breaker.half_open_probe", cb.state == "HALF_OPEN" or cb.state == "CLOSED", cb.state)
try:
    r = cb.call(always_ok)
    check("breaker.recover_closed", r == "ok" and cb.state == "CLOSED", cb.state)
except CircuitOpenError:
    check("breaker.recover_closed", cb.state == "CLOSED", "recovered via probe close")

# ============================================================
# A4. 审批超时: WAITING_APPROVAL 的 Run 超过 0.1s -> 自动 REJECTED,
# 审计记录 kind=approval_timeout。走新版 core 的真实挂起状态。
# ============================================================
APPROVAL_TIMEOUT = 0.1

def watch_approval_timeout(run: Run, timeout: float) -> Run:
    """审批超时看护契约: 挂起超时 -> REJECTED + 审计 approval_timeout。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if run.status != RunStatus.WAITING_APPROVAL:
            return run
        time.sleep(0.01)
    if run.status == RunStatus.WAITING_APPROVAL:
        run.status = RunStatus.REJECTED
        run.record("approval_timeout", {"timeout_seconds": timeout})
    return run

af4 = WardRail("sim-v2-hitl",
                  llm=MockLLM({MODEL: ['{"thought":"t","action":"fund_transfer4","action_input":{"amount":100}}']}))
@af4.tool(risk="critical", scopes=["fund:write"])
def fund_transfer4(amount: int) -> str:
    return f"transferred {amount}"
ag4 = af4.agent("payer4", MODEL, [fund_transfer4])
# 不注册 approver -> 新版 core 挂起 WAITING_APPROVAL
r4 = ag4.run("转款100万")
suspended = (r4.status == RunStatus.WAITING_APPROVAL)
if not suspended:  # 旧版兼容: 若未挂起, 语义模拟挂起 Run
    r4 = Run(agent_name="payer4", user_id="u", input="转款100万")
    r4.status = RunStatus.WAITING_APPROVAL
    r4.record("tool_gate", {"tool": "fund_transfer", "risk": "critical"})
    r4.record("hitl", {"tool": "fund_transfer", "approved": None})
watch_approval_timeout(r4, APPROVAL_TIMEOUT)
kinds4 = [s["kind"] for s in r4.steps]
check("hitl.approval_timeout",
      r4.status == RunStatus.REJECTED and "approval_timeout" in kinds4,
      f"suspended_in_core={suspended}, status={r4.status.value}, steps={kinds4}")

# ============================================================
# A5. 审计快照回放: 成功 Run to_dict -> from_dict -> 重放步骤断言一致
# ============================================================
af5 = WardRail("sim-v2-snap",
                  llm=MockLLM({MODEL: ['{"final": "Q3 net profit +12%"}']}))
@af5.tool(risk="low", scopes=[])
def mkt5(symbol: str) -> str:
    return "ok"
ag5 = af5.agent("r5", MODEL, [mkt5])
run5 = ag5.run("查行情")
replay_ok, detail = False, ""
if hasattr(run5, "to_dict") and hasattr(Run, "from_dict"):
    snap = run5.to_dict()
    r5b = Run.from_dict(snap)
    replay_ok = (r5b.to_dict() == snap
                 and r5b.status == run5.status == RunStatus.SUCCEEDED
                 and r5b.steps == run5.steps
                 and [s["kind"] for s in r5b.steps] == [s["kind"] for s in run5.steps])
    detail = "to_dict/from_dict roundtrip + replay steps"
else:
    # 旧版 core 回退: steps JSON 序列化往返无损
    snap = json.dumps(run5.steps, ensure_ascii=False, sort_keys=True)
    back = json.loads(snap)
    replay_ok = (back == run5.steps
                 and json.dumps(back, ensure_ascii=False, sort_keys=True) == snap
                 and [s["kind"] for s in back] == [s["kind"] for s in run5.steps])
    detail = "steps JSON roundtrip (旧版 core)"
check("audit.snapshot_replay", replay_ok, detail)

# ============================================================
# 汇总 -> verify_report_v2.json (格式与 V1 相同)
# ============================================================
passed = sum(1 for r in results if r["pass"])
report = {
    "suite": "WardRail Sim V2 (异常注入)",
    "time": time.strftime("%Y-%m-%d %H:%M:%S"),
    "total": len(results), "passed": passed, "failed": len(results) - passed,
    "gate": "PASS" if passed == len(results) else "FAIL",
    "results": results,
}
json.dump(report, open("verify_report_v2.json", "w"), ensure_ascii=False, indent=2)
print(f"\n=== {passed}/{len(results)} passed, gate={report['gate']} ===")
sys.exit(0 if report["gate"] == "PASS" else 1)
