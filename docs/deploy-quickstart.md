# WardRail 一键部署速查（Quickstart）

三条启动路径按需选择：本地裸跑最快、docker compose 一键全栈、生产走 helm。

## A. 本地裸跑（开发调试）

依赖：Python 3.11+

```bash
# 网关
cd gateway && pip install -r requirements.txt
GATEWAY_MOCK=1 uvicorn wd_gateway.main:app --port 8002 &

# 编排器控制台（另开终端）
cd orchestrator && pip install fastapi uvicorn httpx
uvicorn console:app --port 8000
```

- 网关 `GATEWAY_MOCK=1` 时所有模型走本地 mock 回复，无需任何上游 API key。
- 本地直跑时 `GATEWAY_URL` 缺省为 `http://localhost:8002`，无需额外配置。
- 运行完整 demo（含高危审批路径）：`cd orchestrator && python demo.py`，然后访问控制台 `/approvals`。

## B. Docker Compose 一键启动

```bash
docker compose up -d --build
```

端口一览：

| 端口 | 服务 | 说明 |
|------|------|------|
| 8000 | orchestrator | 控制台（Agent 列表 / 运行记录 / 待审批） |
| 8002 | gateway | OpenAI 兼容模型网关（路由/降级/DLP/审计） |
| 3000 | langfuse | 可观测与 Trace |
| 5432 / 6379 / 8200 | postgres / redis / vault | 基础设施 |

编排器默认 `GATEWAY_URL=http://gateway:8002`，网关默认 `GATEWAY_MOCK=1`（mock 模式免 key）。两个服务均带 `/health` healthcheck，可用 `docker compose ps` 查看健康状态。

运行 demo：`docker compose run --rm orchestrator python demo.py`。

## C. 生产部署建议

- 参考 `helm/` 目录的 Helm Chart（Kubernetes 部署，含资源限制、探针、密钥管理）。
- 网关关闭 mock：`GATEWAY_MOCK` 置空，并通过 Vault/Secrets 注入各上游 `*_API_KEY`。
- 审计文件 `audit.jsonl` 挂载持久卷或接入集中日志。
- 后续计划：HPA 弹性扩缩、多副本网关 + Redis 限流、控制台 SSO 接入（见 docs/adr/adr-001-003.md）。

## 冒烟验证清单

```bash
# 1. 网关健康
curl -s http://localhost:8002/health          # {"status":"ok"}

# 2. 控制台健康
curl -s http://localhost:8000/health          # {"status":"ok"}

# 3. mock chat completions（返回 "这是来自 wd-gateway 的模拟回复: ..."）
curl -s http://localhost:8002/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"private/qwen2.5-14b","messages":[{"role":"user","content":"hello"}]}' \
  | grep content

# 4. 审计文件增长
wc -l gateway/audit.jsonl   # 每请求 +1 行

# 5. 控制台页面 200
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8000/            # 200
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8000/approvals   # 200
```
