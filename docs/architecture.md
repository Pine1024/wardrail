# WardRail 架构总览

八模块架构（M1-M8），纵贯层 M7 评测观测 / M8 安全合规横切全部模块：

M1 多端接入 → M2 智能体编排 → M3 知识与记忆 → M4 工具执行(沙箱) → M5 模型网关 → M6 模型池 → L5 数据与企业系统

完整设计文档与架构图见仓库 wiki / `docs/adr/`（ADR: Architecture Decision Records）。

核心设计原则：
1. 单点收口 — 模型调用只经网关，工具调用只经注册中心+沙箱
2. 最小权限 — Agent 独立身份，短时 scoped token，高危动作 HITL
3. 全程可观测 — trace_id 贯穿，Run 快照可回放
