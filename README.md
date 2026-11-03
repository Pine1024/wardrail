# WardRail — Agent Governance Layer

**A lightweight, embeddable governance layer for AI agents — approval, audit, DLP, evaluation. Ship in two days.**

[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![CI](https://github.com/<you>/wardrail/actions/workflows/ci.yml/badge.svg)]()
[![Status](https://img.shields.io/badge/status-v0.1--alpha-orange)]()

## 我们不做另一个 Agent 平台，我们做它的刹车和行车记录仪

WardRail **不是**又一个 Dify/LangChain——那些是让 Agent 跑起来的东西。WardRail 解决的是**跑起来之后**的问题：

> 这个 Agent 到底调了哪个模型、动了哪条数据、谁批准的、出了问题怎么复盘？

把 WardRail 挂到你已有的 Agent 栈前面，你就获得了：**模型调用收口（脱敏/配额/降级）· 工具风险分级 · 高危操作人工审批 · 全链路审计 · 评测回归**。两天装完，代码量两万行以内，看得懂、改得动、开得了源。

| 你现在的状况 | 装 WardRail 之后 |
|---|---|
| Agent 直接调模型，不知道发了什么出去 | 所有调用经网关，敏感字段脱敏、配额可控、可降级 |
| Agent 删库/转账/发邮件没人管 | 高危工具强制挂起，等人在控制台点"批准" |
| 客户/审计问"这笔操作谁做的"答不上来 | 每次 Run 有完整快照：谁、用了什么、动了什么 |
| 改了 prompt 不知道效果变好还是变坏 | 评测集回归，分数降 2% 自动拦截发布 |

### 我们明确**不做**什么

诚实说明边界，比夸大能力更能建立信任：

- ❌ **不做 OS 级硬沙箱**：我们提供容器档"进程级隔离"，不是 Firecracker/Seatbelt 那种内核级边界。需要硬隔离请上云厂商或专业沙箱方案。
- ❌ **不做应用层客户端**：不做白牌桌面端、小程序容器。你的前端你说了算。
- ❌ **不做整机运行时替换**：不要求你放弃 Dify/LangChain/自研框架，WardRail 是加法不是替换。
- ❌ **不做通用 Agent 编排平台**：编排是别人的强项，我们只管"跑起来之后"。

### 与主流方案的关系

| | 编排/低代码 | 治理/审批/审计 | 部署形态 | 开放性 |
|---|---|---|---|---|
| Dify | ✅ 强 | ⚠️ 企业版才有 | 中型平台 | Apache 改良版 |
| LangChain/LangGraph | ✅ 强 | ❌ 无 | 代码库 | MIT |
| 商业 AI OS（如 FinAIOS） | ✅ 完整 | ✅ 强 | 重型，月级实施 | 闭源 |
| **WardRail** | ❌ 不做 | ✅ 专注 | **轻量，两天装完** | **Apache-2.0** |

推荐组合：**Dify 做编排 + WardRail 做治理**，各取所长。

## ✨ 核心能力（v0.1 全部已实现）

| 模块 | 能力 | 状态 |
|---|---|---|
| `gateway/` | OpenAI 兼容模型网关：多模型路由、按 priority 降级链、RPM/TPM 配额（滑动窗口）、DLP 出站脱敏 + 回填、审计日志、SSE 流式、mock 上游 | ✅ v0.1 |
| `orchestrator/` | ReAct 编排内核（结构化 JSON 决策）、Run 审计单元（每步出入参快照 + latency）、checkpoint 序列化/恢复、HITL（WAITING_APPROVAL → approve()/reject 恢复）、LLM 抽象（MockLLM / OpenAICompatClient，失败重试） | ✅ v0.1 |
| `registry/` | MCP 工具注册中心 + 风险分级（low/medium/high/critical）+ scope 授权校验（403 拒绝）+ critical 强制 HITL/沙箱 | ✅ v0.1 |
| `policy/` | 策略即代码：Casbin 模型，谁能用哪个 Agent/工具/模型 | ✅ v0.1 |
| `sim/` | 端到端模拟验证框架：V1 全链路（DLP→门禁→注册→编排 HITL→网关→评测）+ V2 异常注入（超时重试、流式跨分片脱敏、工具熔断、审批超时、快照回放），产出 verify_report*.json，FAIL 退出码非 0 | ✅ v0.1 |
| `evals/` | 评测回归脚本（`--threshold` 阈值门禁，可集成 CI） | ✅ v0.1 |
| `orchestrator/console.py` | 极简 Web 控制台：查看 Run、审批挂起工单 | ✅ v0.1 |
| `sandbox/` | 容器档进程级隔离（**非** OS 级硬沙箱），高危工具执行 + 超时熔断 | 🚧 下个迭代 |
| `knowledge/` | RAG：ACL 权限过滤检索、引用溯源 | 🚧 v0.3 |
| `apps/web` | 完整控制台 | 📋 规划 |
| ~~OS 级硬沙箱 / 应用层客户端~~ | 刻意不做，见上文"我们明确不做什么" | ❌ |

## 🚀 Quickstart

### 两条命令跑通（无需 docker、无需 API key，内置 MockLLM）

```bash
pip install -r orchestrator/requirements.txt
python orchestrator/demo.py
```

打开 http://localhost:8000 查看极简控制台。demo 演示两条治理路径：

- **低风险直通**：行情查询 Agent → 直接 `SUCCEEDED`
- **高危审批**：工单 Agent 调用 `ticket_create`（high 风险）→ 挂起 `WAITING_APPROVAL` → 控制台/`approve()` 批准 → 恢复执行至完成

### 可选：docker compose 启动完整服务（网关 + 注册中心）

```bash
cp .env.example .env
docker compose up -d
# 网关: http://localhost:4000/v1/chat/completions  ·  /health  ·  /metrics
```

### 在代码中使用

```python
from wardrail import WardRail

af = WardRail(project="demo", llm=...)   # 不传 llm 则走 OpenAI 兼容网关

@af.tool(risk="high", scopes=["fs:read"])   # high 风险 → 自动触发人工审批
def read_report(path: str) -> str:
    return open(path).read()

agent = af.agent("analyst", model="private/qwen2.5-14b", tools=[read_report])
run = agent.run("总结本季度财报要点")        # 高危工具 → WAITING_APPROVAL
run = agent.approve(run, decision=True)      # 人工批准后恢复执行
print(run.answer)
print(run.to_dict())                          # 全链路审计快照，可回放
```

## 🏗 架构

```
用户请求
   │
   ▼
┌─────────────────────────── orchestrator (ReAct 内核) ───────────────────────────┐
│  Run(trace_id) ──► LLM 决策(JSON) ──► 风险门禁 ──┬─ low/medium ──► 执行工具        │
│                                                  └─ high/critical ──► HITL 挂起   │
│        ▲                                          (approve/reject 恢复)           │
│        └──── 工具结果回放 ◄── checkpoint 快照可序列化/恢复 ◄────────────────────────┘
│                │                                    │
│                ▼                                    ▼
│  ┌─── gateway (OpenAI 兼容) ───┐      ┌─── registry (工具注册中心) ───┐
│  │ DLP 脱敏→路由→降级链→配额    │      │ 风险分级 / scope 校验 / 授权    │
│  │ →上游调用→审计→响应回填      │      └───────────────────────────────┘
│  └─────────────────────────────┘
└──────────────────────────────────────────────────────────────────────────────────┘
                 │                                  │
                 ▼                                  ▼
        sim/verify_e2e.py (全链路验证)      sim/verify_v2.py (异常注入验证)
        evals/run_evals.py (回归评测, CI 门禁)
```

详见 [docs/architecture.md](docs/architecture.md) 与 [docs/adr/](docs/adr/)。

## 🆚 与 Dify / LangChain 的差异化定位

| | Dify / LangChain | WardRail |
|---|---|---|
| 设计原点 | 编排与低代码快速构建 | 治理与安全合规（Governance-first） |
| 模型调用 | 直连各家 SDK | 强制网关收口：DLP 脱敏、配额、降级、审计 |
| 工具执行 | 函数调用即执行 | 注册中心分级 + scope 校验 + 高危强制 HITL |
| 审批 | 需自行搭建 | 内置 WAITING_APPROVAL 状态机，checkpoint 挂起/恢复 |
| 审计 | 日志插件 | Run 级结构化快照（每步出入参 + latency），可序列化回放 |
| 质量保障 | 无 | sim 验证框架 + evals 阈值门禁，可入 CI |
| 定位 | 让 Agent 跑起来 | **让 Agent 跑得下去（可管、可审、可复盘）** |

两者互补：可以用 Dify/LangChain 构建业务流，把 WardRail 作为其下层的治理底座（网关 + 门禁 + 审计）。

## 🔐 鉴权与安全配置

v0.2 起内置两层鉴权（零重依赖实现）：

- **控制台 JWT 登录**（`orchestrator/console.py`）：`GET /login` 登录页 → `POST /login` 签发 HS256 JWT 写入 `wr_session`（HttpOnly, SameSite=Lax, max-age 900s），`/`、`/runs`、`/approvals` 等页面未登录自动 303 跳登录页，登录后跳回原路径；`POST /logout` 退出。密码用 PBKDF2-HMAC-SHA256 哈希存储，JWT 与 API Key 逻辑见 `orchestrator/wardrail/auth.py`。
- **网关 API Key**（`gateway/wd_gateway/apikey.py`）：设置 `WARDRAIL_GATEWAY_AUTH=1` 启用后，`/v1/chat/completions` 校验 `Authorization: Bearer wr_live_xxx`，无效返回 401。Key 仅在创建时返回一次明文，存储只保留 sha256 哈希。

### 环境变量

| 变量 | 默认值 | 说明 |
|---|---|---|
| `WARDRAIL_ADMIN_USER` | `admin` | 控制台默认管理员用户名 |
| `WARDRAIL_ADMIN_PASSWORD` | `changeme` | **生产环境必须修改**，否则禁止对外暴露 |
| `WARDRAIL_SECRET` | 随机生成（日志告警） | JWT 签名密钥；不设置则重启后所有会话失效 |
| `WARDRAIL_GATEWAY_AUTH` | `0`（关闭） | 网关 Bearer API Key 校验开关，生产建议置 `1` |
| `WARDRAIL_AUTH_DISABLED` | 未设置 | 仅测试用（pytest 内放行），生产严禁设置 |

### 生产建议

- 始终显式设置 `WARDRAIL_SECRET`（≥32 字节随机值）与强 `WARDRAIL_ADMIN_PASSWORD`，通过 K8s Secret / 密钥管理注入。
- 网关启用 `WARDRAIL_GATEWAY_AUTH=1`，为每个调用方签发独立 API Key 并按 scope 最小化授权。
- `InMemoryUserStore` / `ApiKeyStore` 为内存实现，仅适用于单实例部署；多副本或需审计留存时替换为持久化后端（如数据库 + 只存哈希）。
- 会话 JWT 有效期 15 分钟，建议前置 HTTPS 反向代理，cookie 已设 HttpOnly + SameSite=Lax 防 XSS/CSRF 基础面。

## 🗺 Roadmap

我们只做治理层，路线图同样遵守"不做"边界：

- **v0.3**：知识检索 ACL 权限过滤、评测引擎产品化、**自动 ROI 报告生成器**（省了多少工时、拦了多少高危操作）、审批中心增强
- **v0.5**：多租户隔离、K8s Helm 部署、TS SDK、**轻量本体 Ontology-lite**（从文档自动抽取业务实体关系）
- **v1.0**：托管版（SaaS 审批中心、审计归档、合规报表）
- ~~OS 级硬沙箱~~ · ~~应用层客户端~~ · ~~通用 Agent 编排~~（明确不做，见上文）

## 🤝 Contributing

欢迎 PR！请先阅读 [CONTRIBUTING.md](CONTRIBUTING.md)。安全漏洞请勿公开提交，见 [SECURITY.md](SECURITY.md)。

## 📄 License

Apache-2.0

## 🧩 行业套件（Industry Kits）

`industry-kits/` 内置金融/政企/制造/医疗四套预置包（Agent 模板 + 策略 + DLP 规则 + 评测集）。加载方式：

```python
from wardrail.kitloader import load_kit
kit = load_kit("industry-kits/government/agents/hotline-worker.yaml")
# 或直接跑演示: python orchestrator/demo_kit.py
```

## 🏭 行业套件案例

- **政企**：12345 热线工单助手（投诉类自动转审批督办）· 信创栈（麒麟/达梦/昇腾）
- **医疗**：病历质控助手（HIS 只读脱敏 scope，全链路强脱敏）
- **制造**：设备故障诊断（中心云 + 车间边缘混合，OT 隔离区单向网关）
- **金融**：投研助手（输出投资建议强制人工复核，云模型默认全禁）

## 📌 适用与不适用

**适合**：已经在用 Dify/LangChain/自研 Agent，需要补审批、审计、脱敏、评测的组织；数据不能出内网、需要私有化部署的中小金融/政企/医疗/制造客户。

**不适合**：还没有 Agent、想从零搭一个 Agent 应用平台（请用 Dify）；需要内核级强制隔离（请用专业沙箱）；需要白牌桌面客户端（请找 FinClip 这类方案）。
