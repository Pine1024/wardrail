## 变更说明

<!-- 做了什么、为什么。关联 Issue：Closes #xxx -->

## 变更类型

- [ ] Bug 修复
- [ ] 新功能
- [ ] 重构
- [ ] 文档
- [ ] 测试 / CI

## 涉及模块

- [ ] gateway/
- [ ] orchestrator/
- [ ] registry/ / policy/
- [ ] sim / evals
- [ ] docs

## 自查清单

- [ ] 本地 `pytest gateway/tests orchestrator/tests` 通过
- [ ] `python sim/verify_e2e.py` 与 `python sim/verify_v2.py` 全 PASS
- [ ] 涉及编排行为的改动：`evals/run_evals.py` 分数不低于阈值
- [ ] 新增/变更审计事件符合 Run 快照结构（可 `to_dict` 序列化）
- [ ] 未引入安全回归（DLP、HITL 门禁、scope 校验）
- [ ] 更新了相关文档（README / RELEASE_NOTES / FAQ）

> 安全漏洞请勿通过公开 PR 提交，见 [SECURITY.md](../SECURITY.md)。
