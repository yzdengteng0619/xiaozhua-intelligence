# Intelligence Center

小爪网络调研自动化 Pipeline 重构项目

## 架构目标

将小爪的网络调研pipeline从单一管道升级为 **"一个引擎，两种模式，三层架构"** 的 Intelligence Center。

- **一个引擎**：统一的搜集→研究→知识库入库
- **两种模式**：Track A（泛用行业深挖）+ Track B（项目滚动深挖）
- **三层架构**：共享引擎层 + 研究执行层 + 情报交付层

## 协作团队

| 角色 | 成员 | 职责 |
|------|------|------|
| 架构决策 | Allen | PR审批、方向决策 |
| PM协调 | Emma (MiMo) | 架构设计、任务分发、质量验收 |
| 核心开发 | Codex (GPT-5.5) @小艾 | 模块开发、代码实现 |
| 测试验证 | Hermes (GLM-5.2) @小艾 | 对抗性测试、debug |
| 生产运行 | OpenClaw @小爪 | pipeline部署、运维、监控 |

## 项目结构

```
xiaozhua-intelligence/
├── README.md          # 本文件
├── architecture.md    # 架构设计文档
├── briefs/            # 子agent brief模板
├── src/               # 源代码（待开发）
└── docs/              # 设计文档
```

## 通信协议

飞书群协作规则：
1. **Emma→群**：必须@目标bot，否则它们收不到
2. **Bot→群**：必须@Emma，否则Emma看不到
3. **Allen→群**：无需@任何bot（人类消息全员可见）
4. **Brief格式**：每个任务brief必须包含目标、验收标准、交付方式、@Emma回报要求

## 开发流程

1. Allen提需求 → Emma拆解为模块brief
2. Emma在群@Codex → Codex开发+提交PR
3. Emma在群@Hermes → Hermes测试+报告问题
4. Emma验收 → Allen审批
5. 合并后 → 部署到小爪生产环境
