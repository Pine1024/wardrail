# WardRail FAQ

## 1. WardRail 和 Dify / LangChain 有什么区别？

Dify / LangChain 的设计原点是**编排与快速构建**：把 LLM 调用、工具、知识库拼成业务流。WardRail 的设计原点是**治理（Governance-first）**：模型调用必须经网关收口（DLP 脱敏、配额、降级、审计），工具执行必须经风险分级 + scope 校验，高危动作必须人工审批，全程 trace 可回放。两者互补——你仍可以用 Dify/LangChain 构建业务流，把 WardRail 作为其下层的治理底座。

## 2. 为什么高危操作一定要人工审批（HITL）？

因为高危工具（如创建工单、资金操作、写文件）一旦被提示注入或模型幻觉误触发，损失不可逆。v0.1 中 risk 为 high/critical 的工具会挂起为 `WAITING_APPROVAL`，Run 连同待审批动作被完整快照，批准（可修改入参）或拒绝后恢复执行；critical 工具在注册中心还被强制标记 `requires_approval` + `sandbox_required`。这是最小权限原则在 Agent 场景的落地。

## 3. 能不能关掉审批？

可以，但只建议在低风险场景关闭。审批绑定在工具的 `risk` 声明上：把工具标为 `low/medium` 即直通，`high/critical` 走 HITL。也可以通过 `set_approver()` 注入自动审批回调（如工单系统自动通过）。**不建议**把真实涉及资金/数据的工具降级为 low——审计里会记录每次 `tool_gate` 决策，降级等于自动免责。

## 4. 支持哪些模型？

任何 OpenAI 兼容 API。网关本身就是 OpenAI 兼容协议，`models.yaml` 里声明 endpoint + `api_key_env` 即可接入：OpenAI/Anthropic 兼容代理、vLLM/Ollama 本地模型（如 `private/qwen2.5-14b`）、或 `mock://` 演用 mock。编排层 `OpenAICompatClient` 只依赖网关，`MockLLM` 可完全脱离模型跑通全流程。

## 5. 私有化部署要求是什么？

v0.1 最小部署只需 Python 3.11+：`pip install -r orchestrator/requirements.txt && python orchestrator/demo.py` 即可跑通（内置 MockLLM，无需外网和 API key）。完整服务（网关 + 注册中心）提供 docker compose 和 helm chart。所有组件无外部 SaaS 依赖，可在完全离线环境运行；数据不出企业边界。

## 6. 数据存在哪里？会不会上传？

v0.1 全部数据存本地：Run 审计快照与网关审计日志由你的部署自行决定落盘位置（当前默认内存/进程内，生产建议写入你自己的 DB——`run.to_dict()` 即落盘格式）；DLP 只做脱敏转发，模型请求只发往你在 `models.yaml` 里配置的上游。WardRail 本身不外发任何数据。审计持久化与归档在 v0.5/v1.0 完善。

## 7. 如何添加自定义工具？

用 `@af.tool(risk=..., scopes=[...])` 装饰器声明函数即可：

```python
@af.tool(risk="medium", scopes=["crm:read"])
def query_customer(cust_id: str) -> dict:
    return db.get_customer(cust_id)
```

如需跨 Agent 共享与集中治理（风险分级、scope 授权、critical 强制审批），把工具注册到 `registry/` 注册中心（POST `/tools`），编排时按名称引用。

## 8. 商业版（Enterprise）包含什么？

开源版（Apache-2.0）包含全部 v0.1 核心：网关、编排、HITL、注册中心、sim/evals。商业版规划包含：多租户与 SSO/RBAC 管理台、审计归档与合规报表（SOC2/等保）、官方 SLA 支持、行业套件（finance/government）深度定制、沙箱托管执行、与 IM/审批系统的官方连接器。

## 9. sim 验证框架是干什么的？为什么要跑它？

`sim/verify_e2e.py` 用进程内 TestClient 走完全链路（脱敏→门禁→注册→编排 HITL→网关），`sim/verify_v2.py` 再叠加异常注入（LLM 超时重试、流式跨分片脱敏、工具熔断、审批超时、快照回放一致性）。两者全 PASS 且退出码为 0 才算验证通过，已接入 CI——改动治理逻辑时，这是防回归的第一道闸。

## 10. 遇到安全漏洞怎么办？

请勿公开提交 Issue/PR。按 [SECURITY.md](../SECURITY.md) 中的联系方式私密报告。
