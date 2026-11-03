# CHANGELOG

按迭代追加，格式：`## YYYY-MM-DD 任务#序号 — 标题`，列完成项 / 测试结果 / 遗留问题。

---

---

## 2026-11-02 任务#2 — 鉴权层：API Key 管理 + JWT 登录

**完成**
- 新增 `orchestrator/wardrail/auth.py`：PBKDF2 密码哈希（`hash_password`/`verify_password`，compare_digest 防时序）、手写 HS256 JWT（`create_token`/`decode_token`）、`InMemoryUserStore`、`ApiKeyStore`（明文 key 仅签发时返回一次，存储只存 sha256 哈希，支持 verify/revoke/list）、`AuthError`
- 控制台 `console.py`：`GET/POST /login` 登录页 + JWT 会话 cookie（HttpOnly/SameSite=Lax/max-age 900）、`POST /logout`、`/ /runs /runs/{id} /approvals` 未登录 303 跳 `/login?next=`、页头显示当前用户与退出按钮；默认管理员 `WARDRAIL_ADMIN_USER/PASSWORD`（默认 admin/changeme），JWT secret 读 `WARDRAIL_SECRET`（缺省随机生成并告警）
- 网关 `wd_gateway/apikey.py` 独立 API Key 实现（不依赖 orchestrator）；`WARDRAIL_GATEWAY_AUTH=1` 时 `/v1/chat/completions` 校验 Bearer key，无效 401（默认关闭保持兼容）
- 测试：`orchestrator/tests/test_auth.py`（11 用例）+ `gateway/tests/test_gateway_auth.py`（3 用例，覆盖开关三态）
- 文档：README 新增「鉴权与安全配置」小节（环境变量表 + 生产建议）

**测试**：32 单测全绿；sim verify_e2e 13/13、verify_v2 9/9 均 PASS。
**遗留**：用户/API Key 存储为内存实现（多副本需持久化）；审批 POST 接口复用同一 cookie 会话，细粒度 RBAC 待策略引擎接入。

## 2026-11-01 初始化 — v0.1 MVP

**完成**
- 模型网关 wd_gateway：OpenAI 兼容协议（JSON/SSE）、路由降级链、DLP 脱敏回填、配额 429、审计 jsonl、mock 模式
- 编排内核 wardrail.core：ReAct 循环、HITL（挂起/批准/拒绝/超时）、checkpoint 序列化
- 控制台 console.py：Agent 列表 / Run 审计时间线 / 审批卡片
- 行业套件：finance/government/manufacturing/healthcare 四套 YAML + kitloader 加载器
- 模拟验证：V1 13 用例 + V2 9 用例（异常注入）
- 部署：docker-compose + deploy-quickstart.md
- 开源材料：README / FAQ / RELEASE_NOTES_v0.1 / CI

**测试**：18 单测 + 22 模拟用例，全绿。
**遗留**：鉴权层、沙箱执行、策略引擎未实现（见 ROADMAP P0 剩余项）。

## 2026-11-02 战略校准 — 基于 Finogeeks FinAIOS 竞品分析

**背景**：研读凡泰极客 FinAIOS 产品白皮书（26 页）及官网，确认其已覆盖"金融行业 + 治理平台"组合，并采用 FDE 按结果计价交付。

**调整**（已写入 ROADMAP.md 第〇节）
- 定位从"企业智能操作系统整机"降为"**治理层**"：不拼平台完整度，以轻量、可嵌入、易审计取胜
- 沙箱范围收缩：不做 OS 级硬隔离，改容器档并**如实标注"进程级隔离"**
- 交付模式避开 FDE 驻场：走"标准方案包 + 远程实施 + 客户自助运营"，主攻中小金融与非金融行业
- P1 新增「可量化效果报告生成器」、P2 新增「轻量本体 Ontology-lite」，分别对标对方"岗位工时计量"与"Fintology"

**不变**：网关/HITL/审计/DLP/评测治理链路、行业套件仍为核心差异化。
**测试**：本次仅文档调整，32 单测 + 22 模拟用例维持全绿。
