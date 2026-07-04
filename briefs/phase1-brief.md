# Phase 1 Brief: task_watcher + brief_router

> 开发环境：小爪 Linux (claw-xiaozhua) 或小艾 Windows
> 代码目录：~/intelligence_center/src/ (小爪) 或 C:\Users\yzden\intelligence_center\src\ (小艾)
> 完成后推送到 GitHub: git@github.com:yzdengteng0619/xiaozhua-intelligence.git

---

## 目标

构建 Intelligence Center 的任务调度层，实现 Track B（项目深挖）的基本流程。

## 模块 1: task_watcher.py (~80行)

**职责**：每30秒扫描 jobs/ 目录，发现新任务并触发执行

**输入**：jobs/<job_id>/ 目录下的 job_spec.json

**状态机**：
```
pending → running → done | failed
```

**核心逻辑**：
1. 扫描 jobs/*/status.json，找 state=pending 的任务
2. 检查是否有 running 的任务（并发锁，同一时间只跑1个）
3. 如果 Track B 任务进来且 Track A 在跑，Track B 优先（不强杀，等当前子任务结束）
4. 找到 pending 任务后：
   - 更新 status.json 为 running
   - 调用 brief_router.py 处理
   - 完成后更新 status.json 为 done 或 failed
5. 超时检测：running 超过 timeout 的任务标记为 failed

**文件格式**：
```json
// jobs/<job_id>/status.json
{
  "job_id": "20260704_001",
  "state": "pending",
  "started_at": null,
  "completed_at": null,
  "timeout": 3600,
  "error": null,
  "retry_count": 0
}
```

**并发锁**：用 fcntl.flock 文件锁，锁文件 jobs/.lock

---

## 模块 2: brief_router.py (~150行)

**职责**：解析 brief，生成 job_spec.json 和 direction.json

**输入**：
- Track A: direction 模板（18行业列表）
- Track B: 飞书消息或 brief 文件

**输出**：jobs/<job_id>/ 目录下的完整结构

**Job Spec 格式**：
```json
{
  "job_id": "20260704_001",
  "track": "B",
  "project_id": "XXX品牌Q3策略",
  "direction": "某品牌抗初老品类策略",
  "objective": ["竞品梳理", "搜索行为", "KOL趋势"],
  "keywords": ["抗初老", "早C晚A", "敏感肌"],
  "constraints": {
    "scope": "近3个月",
    "max_sources": 50,
    "deadline": "2026-07-06"
  },
  "output_format": "project_report",
  "created_by": "feishu",
  "created_at": "2026-07-04T10:00:00"
}
```

**核心逻辑**：
1. 接收 brief（JSON 或自然语言文本）
2. 如果是自然语言，用 MiniMax API 解析成结构化 job_spec
3. 创建 jobs/<job_id>/ 目录
4. 写入 job_spec.json
5. 从 job_spec 生成 direction.json（引擎输入格式）
6. 写入 status.json（state=pending）

**direction.json 格式**（保持现有引擎兼容）：
```json
{
  "directions": [
    {
      "topic": "竞品抗初老产品线梳理",
      "keywords": ["抗初老", "竞品", "产品线"],
      "industry": "美妆"
    }
  ],
  "scope": "近3个月",
  "max_results": 50
}
```

---

## 测试要求

1. 手动创建一个 job_spec.json，验证 task_watcher 能发现并处理
2. 验证并发锁：同时放两个任务，确认串行执行
3. 验证优先级：Track A 在跑时 Track B 能插队
4. 验证超时检测：设短超时，确认 failed 状态正确
5. 验证 brief_router 能从自然语言生成 job_spec

## 交付方式

代码写到 ~/intelligence_center/src/ 目录，推送到 GitHub main 分支。
完成后在群里 @Emma 回复，说明完成了什么、测试结果、遇到的问题。
