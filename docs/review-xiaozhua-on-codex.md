# 小艾对Codex方案的审查

## 1. 最大风险/漏洞

### 风险一：APScheduler 是陷阱
- 需要 jobstore 持久化（引入 SQLAlchemy 或 Redis backend）
- 在 cron + nohup 共存环境里容易打架
- 崩了或 jobstore 损坏，整个任务队列丢失且不可人工 inspect
- 文件系统队列 crash 后 tasks/*.json 全在，人工可读可恢复

### 风险二："worker 改造"违反 Brief 约束
- Brief 明确写了"不需要重写现有模块"
- 正确做法：在引擎外围加适配器（把 job_spec 翻译成 direction.json），引擎本身一行不改

## 2. 设计过度

| 过度项 | 问题 | 建议 |
|--------|------|------|
| 7个新模块 | context_builder跟knowledge_retriever重叠；project_registry用SQLite表就够 | 砍到4-5个 |
| 三层知识库检索 | 28K页面直接FTS5够用，三层增加3倍调试面 | MVP只做FTS5 |
| 3个Brief入口 | 3个入口=3套解析逻辑=3倍测试面 | MVP只做飞书 |
| project_registry独立模块 | 项目管理用表字段就够了 | 降级为表字段 |

## 3. 设计不够

| 缺失项 | 影响 | 补法 |
|--------|------|------|
| 没有KB Linker | 知识库只进不出，越积越散 | 加入库hook自动关联 |
| 没有优先级抢占 | Track A跑大任务时Track B紧急brief进来怎么办 | Router加优先级队列 |
| FTS5索引怎么建没说 | 28K文件怎么灌进FTS5 | 需要kb_indexer.py |

## 4. 改进建议

1. 砍掉APScheduler，用文件系统队列
2. 合并模块7→4（knowledge_retriever+context_builder→kb_retriever, project_registry降级为表字段）
3. 加KB Linker（入库后自动关联）
4. 加优先级抢占（Track B可抢占Track A）
5. 加kb_indexer.py（首次全量索引+增量更新）

## 核心差异总结

Codex="给现有系统套一个编排框架"（更工程化但更重）
小艾="在现有系统外围贴几个薄适配器"（更轻但编排能力弱）

如果未来Track B需要DAG依赖（先跑竞品→再跑消费者→合并报告），Codex的orchestrator更灵活。但MVP阶段编排框架是过度投资。
