# 调优后方案对比

## Codex 调优版（vs 原版改动）

### 核心变化
1. **去掉APScheduler** → task_watcher.py 30s轮询文件队列
2. **模块7→5+1**：砍掉context_builder(并入kb_retriever)、project_registry(用SQLite表字段)、job_schema(内联)；新增kb_indexer.py
3. **FTS5单层**：不做embedding/FAISS/三层检索
4. **飞书单一入口**：CLI和文件夹监听后置
5. **加优先级调度**：Track B可插队Track A（不强杀，停止派发新子任务）
6. **最小接口改造**：research_worker加--job参数，入库逻辑加project_id/source_job_id

### 最终模块
| 模块 | 职责 |
|------|------|
| task_watcher.py | 30s轮询，调度+抢占+失败重试 |
| brief_router.py | 解析飞书brief，生成job_spec.json |
| kb_indexer.py | 首次全量索引28K+增量更新FTS5 |
| kb_retriever.py | FTS5检索历史知识，注入研究前置上下文 |
| kb_linker.py | 入库后关联新旧页面 |
| report_generator.py | 项目交付Markdown/飞书摘要 |

### MVP流程
飞书brief → brief_router → job_spec.json → task_watcher调度 → kb_retriever前置检索 → 共享引擎(搜集→研究→评分) → 入库 → kb_linker后置关联 → report_generator → 飞书交付

---

## 小艾调优版（vs 原版改动）

### 核心变化
1. **承认Job Spec需要贯穿**：research_worker加--job可选参数（~15行改动）
2. **Job Bundle目录结构**：jobs/<id>/下含job_spec.json + context/ + direction.json + output/ + status.json
3. **KB前/后双阶段明确**：brief→KB检索→研究→入库→KB关联
4. **补失败恢复/去重/幂等/并发锁**：
   - 失败恢复：检测running超时任务→标记failed
   - 去重：job_id唯一约束
   - 幂等：状态机pending→running→done/failed
   - 并发锁：flock文件锁
5. **报告阶段再查一次KB**：报告引用已有页面作为"历史发现"对比

### 最终模块
| 模块 | 职责 |
|------|------|
| brief_router.py | 统一入口，接收Track A/B，分发到引擎 |
| task_watcher.py | 30s轮询tasks/目录 |
| kb_retriever.py | FTS5检索+构建研究上下文 |
| kb_linker.py | 入库后自动关联 |
| report_generator.py | 项目报告Markdown→飞书 |

### 关键设计：Job Bundle
```
jobs/20260704_002/
  job_spec.json      ← 完整数据契约
  context/           ← KB注入的上下文
  direction.json     ← 从job_spec生成，引擎直接吃
  output/            ← 引擎输出
  status.json        ← 运行状态
```

---

## 两版调优后的共识

| 维度 | Codex调优版 | 小艾调优版 | 共识度 |
|------|-------------|------------|--------|
| 调度 | 文件队列+30s轮询 | 文件队列+30s轮询 | ✅ 完全一致 |
| 模块数 | 6个 | 5个 | ✅ 接近 |
| 知识库 | FTS5单层 | FTS5单层 | ✅ 完全一致 |
| KB前置 | kb_retriever前置检索 | KB Retriever前置+报告时再查 | ✅ 一致 |
| KB后置 | kb_linker关联 | kb_linker关联 | ✅ 完全一致 |
| Brief入口 | 仅飞书 | 仅飞书 | ✅ 完全一致 |
| 引擎改造 | --job参数+入库字段 | --job可选参数(~15行) | ✅ 本质一致 |
| 优先级 | Track B插队Track A | 未明确 | Codex更完整 |
| 失败恢复 | 提及 | 详细设计 | 小艾更完整 |
| FTS5索引 | kb_indexer.py | 未单独提 | Codex更完整 |
| Job格式 | JSON+SQLite | Job Bundle目录 | 小艾更结构化 |
