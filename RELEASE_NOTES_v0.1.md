# Release Notes — WardRail v0.1 (alpha)

发布日期：2026-11
定位：Governance-first 的企业级 Agent 治理平台（开源 + 商业双轨）

## 功能清单

### 模型网关（`gateway/`）
- OpenAI 兼容 API（`/v1/chat/completions`），替换 base_url 即可接入
- 多模型路由：`models.yaml` 声明模型池，按 priority 升序构成降级链，首选不可用自动降级并在响应中标 `degraded`
- DLP 出站脱敏：逐条消息脱敏后才调用上游，响应回填还原；累计命中数记入审计
- 配额：按 api_key 的 RPM/TPM 滑动窗口限流，超限 429
- 审计日志：request_id / model / token 用量 / dlp_hits / latency
- SSE 流式、`/health`、`/metrics`、mock 上游（`mock://` 或 `GATEWAY_MOCK=1`）

### 编排内核（`orchestrator/`）
- 真 ReAct 循环：LLM 输出结构化 JSON `{"thought","action","action_input"}` 或 `{"final"}`
- Run 审计单元：trace_id 贯穿 LLM/工具/审批，每步记录出入参快照与 latency_ms
- HITL：high/critical 工具挂起为 `WAITING_APPROVAL`，`agent.approve(run, decision, modified_input)` 批准/拒绝/改参后恢复
- checkpoint：Run 完整序列化（`to_dict`/`from_dict`），支持落盘恢复
- LLM 抽象：`MockLLM`（脚本化，零依赖演示）与 `OpenAICompatClient`（超时 60s，失败重试 1 次）
- 极简 Web 控制台（`console.py`）：查看 Run、审批挂起工单

### 工具注册中心（`registry/`）
- MCP 工具注册：name/schema/risk_level/scopes/owner/endpoint
- 风险四级：low / medium / high / critical；critical 强制 `requires_approval` + `sandbox_required`
- scope 授权校验：无授权 403 拒绝

### 策略（`policy/`）
- Casbin 模型策略：谁能用哪个 Agent / 工具 / 模型（示例 `policy/examples/rbac.conf`）

### 验证与评测（`sim/`、`evals/`）
- `sim/verify_e2e.py`：全链路模拟（DLP→门禁→注册→编排 HITL→网关→评测），产出 `verify_report.json`
- `sim/verify_v2.py`：异常注入验证（LLM 超时重试、流式跨分片脱敏、工具熔断、审批超时、快照回放一致性），FAIL 时退出码非 0，可作 CI 门禁
- `evals/run_evals.py --threshold 0.85`：关键词评测回归，低于阈值退出码非 0

## 已知限制

1. 网关上游统一非流式调用，流式响应由网关本地切片模拟
2. 配额与审计为单进程内存态，重启丢失，未持久化（Redis/DB 在 roadmap）
3. 编排最大步数硬编码 8；工具异常即终止 Run，无自动重试
4. DLP 为规则掩码，非模型级 NER；restore 依赖网关内同一实例状态
5. 无认证体系：网关 api_key 仅作配额/审计维度，不做身份鉴权；多租户在 v0.5
6. 沙箱执行未实现，`sandbox_required` 仅标记不强制
7. 控制台为演示级极简实现，无登录与权限
8. `evals` 为关键词命中率，非语义评测

## 升级路径

- v0.1 是首个公开版本，无升级事项。
- 后续版本承诺：checkpoint JSON 结构向后兼容（新增字段仅追加）；网关 `/v1/chat/completions` 请求/响应保持 OpenAI 兼容；`models.yaml` 字段向后兼容。
- 从内部预览版（M3 之前的 core API）升级：`RunStatus`、`agent.approve()`、`to_dict/from_dict` 为新版 API，旧版兼容分支见 `sim/verify_v2.py` 头部说明。
