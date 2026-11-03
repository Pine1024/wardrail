# CHANGELOG

按迭代追加，格式：`## YYYY-MM-DD 任务#序号 — 标题`，列完成项 / 测试结果 / 遗留问题。

---

---

## 2026-11-03 任务#4 — 策略引擎 WardRail-Policy（YAML DSL + 决策缓存 + 门禁接入）

**完成**
- 新增 `orchestrator/wardrail/policy.py`：`PolicyEngine` / `Decision` / `PolicyError`
  - YAML DSL 复用 `kitloader.parse_simple_yaml`（零第三方依赖），结构对齐 `industry-kits/*/policy/policy.yaml`（subject/domain/resource/actions/default）
  - 匹配语义（首条命中即生效）：subject 支持单值/列表/`*`；resource 支持 `tool:*` 前缀通配；actions 列表任一命中即通过，支持 `call:*` 前缀通配；规则可选 `effect: deny`（白名单中的黑名单，排列在前优先生效）；未命中走 `default`（缺省 deny）
  - 决策缓存：`(subjects, domain, resource, action) -> Decision`，TTL 过期 + `reload()`/`invalidate()` 失效 + hits/misses 累计统计（跨 reload 保留）
- `core.py` 门禁接入（全部向后兼容，不配 policy 行为不变）：
  - `WardRail(policy=engine)` 新增可选参数
  - `AgentHandle.run()` 前置 agent 级检查（`resource=agent:<name>, action=run`），deny 返回 REJECTED 的完整审计 Run，不产生任何 LLM/工具调用；新增可选 `subject/domain` 参数，缺省 `user:<user_id>` / 项目名
  - `_gate()` 前置工具级检查（`resource=tool:<name>, action=call`），deny → REJECTED 终止并记 done
  - **策略主体贯穿全链路**：`Run.policy_subject/policy_domain` 新增字段（含 checkpoint 序列化），agent 级与工具级门禁共用同一主体，杜绝"agent 放行、工具换主体"的绕过
- 测试：`orchestrator/tests/test_policy.py`（11 用例：DSL 解析/default deny/通配与 effect/结构校验/缓存命中与 reload 失效/TTL 过期/工具级拒绝与放行/agent 级拒绝/subject 显式传入/两级授权独立性/无 policy 向后兼容）
- 文档：ROADMAP P0 勾选；README 能力表 policy 行更新为内置引擎说明

**测试**：策略 11/11 绿；orchestrator 32/33（1 个失败为本机终端沙箱 preexec_fn 限制，非代码问题）；sim verify_e2e 13/13、verify_v2 9/9 全 PASS。
**遗留**：gateway 12 个用例在本终端因 pytest 临时目录权限报 setup error（CI 环境正常，待 workflow scope 开通后在 CI 验证）；策略与 Casbin 模型（policy/examples/rbac.conf）的双轨关系待文档化——当前内置引擎覆盖 YAML 子集，RBAC 角色继承等复杂场景仍可接 Casbin。

---

## 2026-11-03 任务#3 — 工具执行约束（容器档，非硬沙箱）

> 补执行 11-03 定时迭代，原自动化执行异常，本次由人工补做。

**完成**
- 新增 `sandbox/wd_sandbox.py`：`SandboxRunner.run(command, workdir, timeout, network) -> ExecResult`（stdout/stderr/exit_code/timed_out）
  - `SANDBOX_MODE=local`（默认）：subprocess + resource 资源限制（RLIMIT_AS/NPROC/FSIZE/CORE）+ 环境变量白名单（未列入白名单的变量对子进程不可见）
  - `SANDBOX_MODE=docker`：`docker run --rm --network=none --read-only --cap-drop=ALL --memory=512m --pids-limit=100 --tmpfs /tmp`，出网默认禁止
  - 超时强杀并标记 `timed_out=True`；docstring 与文档明确声明：**local/docker 档均为进程级隔离，不是内核级硬沙箱；需要强隔离请使用 Firecracker 等方案**
- `orchestrator/wardrail/core.py`：工具装饰器新增可选 `sandbox=True` 参数 + `WardRail.sandboxed(command)` 标记 + 实例可选 `sandbox_runner` 配置；声明沙箱的工具改由 SandboxRunner 执行，未配置时行为完全不变（API 向后兼容）
- 测试：`sandbox/tests/test_sandbox.py`（8 用例：正常执行/stdout 捕获/非零退出码/超时杀掉/环境变量白名单/docker 参数 mock/docker 放网/非法模式）+ `orchestrator/tests/test_sandbox_integration.py`（2 用例：沙箱路径走通、无 runner 保持旧行为）
- 文档：ROADMAP 勾选该项，README 能力表 sandbox 行改 ✅

**测试**：42 单测全绿；sim verify_e2e / verify_v2 均 PASS。
**遗留**：出网白名单目前仅 docker 档可强制（--network=none），local 档由策略层约束； WardRail-Policy（下一项）接入后统一门禁。

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
