# Intelligence Center 架构方案（Codex版）

## 设计哲学
**"轻编排 + 统一 Job Spec + 知识库检索增强 + Markdown 交付"**

不重写现有 pipeline，把现有"搜集/研究/评分/入库"抽成共享 Engine。新增任务编排、Brief 解析、知识库检索增强、交付生成四层能力。

## 模块划分

### 复用现有模块
| 模块 | 复用方式 |
|------|----------|
| night_research.sh | 保留 Track A 定时入口 |
| research_worker.py | 改造成可接收 job spec 的研究执行器 |
| score_worker_web.py / score_worker_pdf.py | 继续负责质量评分、分类 |
| wiki 写入逻辑 | 保留为统一入库出口 |
| SQLite 元数据 | 扩展字段，不推翻原结构 |
| web_search.py | 作为搜索适配器底层能力 |

### 新建模块
| 新模块 | 职责 |
|--------|------|
| task_orchestrator.py | 统一调度 Track A / Track B |
| brief_parser.py | 把项目 brief 解析成结构化 research plan |
| job_schema.py | 定义统一任务 JSON schema |
| knowledge_retriever.py | 从 28K wiki 页面中检索相关历史知识 |
| context_builder.py | 拼接 brief、新检索结果、历史知识，形成 LLM 输入 |
| delivery_generator.py | 生成项目报告 / 行业简报 / 飞书摘要 |
| project_registry.py | 管理项目 ID、brief、任务状态、交付物路径 |

## 接口设计

### Job Spec
模块间用 "JSON job file + SQLite 状态表"

### 状态表 research_jobs
| 字段 | 说明 |
|------|------|
| job_id | 任务唯一 ID |
| track | A / B |
| project_id | Track B 项目 ID |
| status | pending / running / scored / stored / delivered / failed |
| job_spec_path | JSON 文件路径 |
| created_at | 创建时间 |
| updated_at | 更新时间 |
| error | 失败信息 |

## Track B 接入方式
三个入口：
1. 飞书消息：用户贴 brief 或上传文档后，由 Hermes/Codex 生成 brief.md
2. CLI：手动执行 python task_orchestrator.py submit --brief projects/x/brief.md
3. 文件夹监听：projects/inbox/*.md 有新 brief 即生成任务

推荐 MVP 先做 CLI + 飞书半自动。

## 知识库利用（三层关联）
1. SQLite 元数据过滤：按行业、标签、来源、时间、评分筛选候选页面
2. 全文检索：SQLite FTS5 建索引，关键词召回
3. 语义关联：后续加 embedding 表（MVP 不做向量库）

新调研入库时写入：related_page_ids, source_job_id, project_id, relevance_score, novelty_score

## 技术选型
| 方向 | 建议 | 原因 |
|------|------|------|
| 主语言 | Python | 复用现有代码 |
| 调度 | APScheduler + CLI | 比 Airflow/Celery 轻 |
| 状态管理 | SQLite | 已在用 |
| 模块通信 | JSON 文件 + SQLite 状态 | 易 debug |
| 全文检索 | SQLite FTS5 | 无额外服务 |
| 语义检索 | 后续 sqlite-vec / FAISS | 先 MVP |
| LLM 调用 | MiniMax M3 + SenseNova + GLM-5.2 | 现有额度 |
| 报告生成 | Markdown first | 飞书/PDF/wiki 都容易转 |
| 配置 | YAML / JSON | 可读性好 |

## MVP 落地顺序
1. 定义统一 job_spec.json
2. 新建 task_orchestrator.py
3. 新建 brief_parser.py
4. 给 research_worker.py 增加 --job job_spec.json 参数
5. 新建 knowledge_retriever.py（SQLite FTS5）
6. 新建 delivery_generator.py 输出 Markdown 项目报告
7. 飞书侧只负责触发和接收结果
