# Phase 2 Brief: kb_indexer + kb_retriever + kb_linker

> 开发环境：小爪 Linux (claw-xiaozhua)
> 代码目录：~/intelligence_center/src/
> 完成后推送到 GitHub

---

## 目标

构建知识库层，让28K+ wiki页面参与研究流程。

## 现有资产

- 小爪wiki目录：~/clawd/knowledge/reports/wiki/ + web/wiki/
- SQLite DB：~/clawd/knowledge/pipeline_checklist.db（28K+条记录）
- wiki文件格式：Markdown，含keywords/source/content等元数据

---

## 模块 1: kb_indexer.py (~120行)

**职责**：为28K wiki页面建FTS5全文索引

**输入**：~/clawd/knowledge/ 下的wiki文件
**输出**：~/intelligence_center/data/wiki_fts.db（SQLite FTS5索引）

**核心逻辑**：
1. 首次全量索引：
   - 扫描 reports/wiki/ 和 web/wiki/ 下所有.md文件
   - 提取：文件路径、标题（第一行#标题）、内容（全文）、行业（从目录推断）、关键词（从文件内keywords字段提取）
   - 写入FTS5虚拟表
2. 增量更新：
   - 记录上次索引时间
   - 只处理 mtime > 上次索引时间 的文件
3. FTS5表结构：
```sql
CREATE VIRTUAL TABLE wiki_fts USING fts5(
    page_path,
    title,
    content,
    industry,
    tags,
    content=wiki_pages,
    content_rowid=rowid
);
CREATE TABLE wiki_pages(
    rowid INTEGER PRIMARY KEY,
    page_path TEXT,
    title TEXT,
    content TEXT,
    industry TEXT,
    tags TEXT,
    mtime REAL
);
```

---

## 模块 2: kb_retriever.py (~150行)

**职责**：基于FTS5检索历史知识，注入研究context

**输入**：brief关键词 + wiki_fts索引
**输出**：jobs/<job_id>/context/kb_retrieval.md

**核心逻辑**：
1. 接收brief的keywords列表
2. 用FTS5 MATCH查询检索相关页面（top 10）
3. 对结果按相关性排序（FTS5 BM25）
4. 生成context文件：
```markdown
# 历史知识检索结果

## 检索关键词：抗初老, 早C晚A

## 相关页面（10篇）

### 1. [页面标题] (相关度: 0.95)
- 路径：reports/wiki/美妆/xxx.md
- 摘要：...
- 关键数据点：...

### 2. ...
```
5. 写入 context/kb_retrieval.md

**CLI接口**：
```bash
python3 kb_retriever.py --keywords "抗初老,早C晚A" --top 10 --output context/kb_retrieval.md
python3 kb_retriever.py --job jobs/20260704_001/  # 从job_spec.json读keywords
```

---

## 模块 3: kb_linker.py (~120行)

**职责**：新页面入库后，自动关联已有页面

**输入**：新入库的wiki页面
**输出**：kb_links表记录

**核心逻辑**：
1. 新页面入库时触发（由入库hook调用）
2. 提取新页面的tags/industry/keywords
3. 用FTS5检索同标签/同行业的已有页面
4. 计算关联分数（关键词重叠度）
5. 写入kb_links表：
```sql
CREATE TABLE IF NOT EXISTS kb_links(
    source_page TEXT,
    linked_page TEXT,
    link_type TEXT,  -- 'same_industry' | 'same_topic' | 'cited'
    score REAL,
    created_at TEXT
);
```
6. 在新页面底部追加"相关阅读"链接

**CLI接口**：
```bash
python3 kb_linker.py --page reports/wiki/美妆/新页面.md
python3 kb_linker.py --job jobs/20260704_001/  # 处理job产出的所有新页面
```

---

## 测试要求

1. kb_indexer：索引100个wiki页面，验证FTS5查询能返回结果
2. kb_retriever：用关键词查询，验证返回相关页面且格式正确
3. kb_linker：给一个新页面，验证能关联到已有页面
4. 集成测试：brief → kb_retriever → direction.json → kb_linker 全流程

## 交付方式

代码写到 ~/intelligence_center/src/ 目录。
完成后在群里 @Emma 回复。
