# Codex 对小艾方案的审查

## 1. 最大风险/漏洞

**数据契约没有贯穿核心 pipeline。** Track B 需要把 brief、项目目标、检索上下文、交付格式、知识库关联一起传进研究/评分/入库流程。如果现有模块完全不改，只靠外围薄层包一圈，brief 解析了但 research_worker 仍按 Track A 逻辑跑，KB 检索到了但没有真正进入研究上下文。

## 2. 过度 vs 不够

**过度**：
- task_watcher.py 30s轮询只是触发方式，不应过度强调
- "文件系统做消息总线"表述偏重，实际应该是 job spec + SQLite 状态表

**不够**：
- tasks/*.json 字段不够：缺少 job_id / project_id / objective / keywords / constraints / output_namespace / error
- 知识库关联不能只放在"入库后"，更关键的是研究前检索
- 没看到失败恢复、去重、任务幂等、并发锁设计

## 3. 关键差异

小艾偏向"现有pipeline外围套薄层，核心零改动"
Codex偏向"把现有pipeline抽象成可接收统一Job Spec的研究引擎"

关键差异：必须给 research_worker.py 加最小接口改造，否则 Track B 只是"另一个触发器"。

## 4. 改进建议

1. tasks/*.json 升级为正式 Job Spec（补齐 job_id/project_id/objective/topics/keywords/output/error）
2. 允许现有模块做最小必要改造（research_worker 加 --job 参数）
3. KB Retriever 前置，KB Linker 后置（brief → KB检索 → 研究上下文 → 新调研 → 入库 → KB关联）
