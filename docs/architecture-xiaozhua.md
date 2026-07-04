# Intelligence Center 架构方案（小艾版）

## 设计哲学
**不重写、不引入框架、用文件系统做消息总线，把现有单一管道包装成"可路由的共享引擎"。**

核心思路是适配器模式 + 文件系统任务队列——现有四个模块完全不动，在外围加三个薄层：路由层、检索层、交付层。

## 模块划分

### 复用（零改动）
- 搜集：night_research.sh + research_worker.py
- 研究：research_worker.py 内置
- 评分：score_worker_web.py / score_worker_pdf.py
- 入库：wiki写入 + SQLite元数据

### 新建（5个薄模块，~750行）
| 模块 | 脚本 | 职责 | 行数 |
|------|------|------|------|
| Brief Router | brief_router.py | 统一入口，接收Track A/B任务，分发到共享引擎 | ~150行 |
| KB Retriever | kb_retriever.py | 调研前从28K wiki检索相关内容，注入research context | ~200行 |
| KB Linker | kb_linker.py | 入库后自动关联新页面与已有页面（tag/行业/语义） | ~120行 |
| Report Generator | report_generator.py | Track B专属——把研究结果组装成项目报告 | ~200行 |
| Task Watcher | task_watcher.py | 轻量文件系统轮询，检测tasks/新任务并触发router | ~80行 |

## 接口设计

### 任务文件格式
```json
{
  "task_id": "20260704_001",
  "track": "A",
  "direction": "美妆行业",
  "brief": null,
  "status": "pending",
  "priority": 0,
  "kb_context": null,
  "result_dir": null,
  "report_path": null
}
```

### 模块间通信
全部用文件系统 + SQLite，不引入消息队列/Redis
- 触发层→Router: tasks/*.json文件
- Router→搜集引擎: direction.json（现有格式）
- KB Retriever→研究引擎: context/目录（MD文件）
- 引擎→入库: 现有wiki路径 + SQLite
- 入库→KB Linker: SQLite trigger
- Report Gen→交付: reports/<task_id>/report.md

## SQLite新增表
```sql
CREATE TABLE tasks (task_id TEXT PRIMARY KEY, track TEXT, direction TEXT, brief_json TEXT, status TEXT, created_at TEXT, completed_at TEXT, result_dir TEXT, report_path TEXT);
CREATE TABLE kb_links (source_page TEXT, linked_page TEXT, link_type TEXT, score REAL, created_at TEXT);
CREATE VIRTUAL TABLE wiki_fts USING fts5(page_path, title, content, industry, tags);
```

## 知识库利用
- 调研前: FTS5检索已有页面→注入research context
- 调研后: KB Linker自动关联新页面与已有页面
- 报告生成: 引用已有页面作为背景知识
- 去重: 新调研结果跟已有页面高度相似时降权

## 实施路线
1. Step 1: 最小可用（Track B跑通）- tasks/ + task_watcher + brief_router
2. Step 2: 知识库激活 - kb_retriever + kb_linker
3. Step 3: 项目交付能力 - report_generator

## 技术选型
- 任务队列: 文件系统 tasks/*.json
- 任务调度: task_watcher.py 30s轮询
- 知识库检索: SQLite FTS5
- Brief解析: 飞书webhook + 正则/LLM
- 报告生成: Jinja2模板 + MiniMax润色
