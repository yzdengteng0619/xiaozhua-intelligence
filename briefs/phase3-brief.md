# Phase 3 Brief: report_generator

> 开发环境：小爪 Linux (claw-xiaozhua)
> 代码目录：~/intelligence_center/src/

---

## 目标

构建项目交付层，Track B自动生成项目报告并推送飞书。

## 模块: report_generator.py (~150行)

**职责**：把研究结果组装成项目报告

**输入**：
- jobs/<job_id>/job_spec.json（项目信息）
- jobs/<job_id>/output/research_results/（研究产出）
- jobs/<job_id>/context/kb_retrieval.md（历史知识）

**输出**：
- jobs/<job_id>/output/report.md（项目报告）
- 飞书群推送（可选）

---

## 报告模板

```markdown
# {project_id} — 调研报告

> 生成时间：{created_at}
> 调研方向：{direction}
> 关键词：{keywords}

## 一、调研概要

基于{scope}的调研，围绕"{direction}"方向，共检索{N}个信息源，生成{M}条研究发现。

## 二、核心发现

### 发现1：{title}
- 数据链条：...
- 为什么重要：...
- 与主流判断差异：...

### 发现2：...

## 三、历史知识对比

基于知识库中{K}篇相关历史页面的对比分析：

| 维度 | 历史认知 | 本次新发现 | 增量 |
|------|----------|------------|------|
| ... | ... | ... | ... |

## 四、建议方向

1. ...
2. ...

## 五、数据来源

- 本次调研：{N}个来源
- 历史引用：{K}篇wiki页面
- 知识库关联：{L}个相关页面
```

---

## 核心逻辑

1. 读取job_spec.json获取项目信息
2. 扫描output/research_results/获取研究产出
3. 读取context/kb_retrieval.md获取历史知识
4. 用模板生成报告Markdown
5. 可选：调MiniMax API润色文字
6. 写入output/report.md
7. 可选：推送飞书群

**CLI接口**：
```bash
python3 report_generator.py --job jobs/20260704_001/
python3 report_generator.py --job jobs/20260704_001/ --push-feishu
```

---

## 测试要求

1. 给定job_spec + research_results，验证报告生成正确
2. 验证报告包含所有必要章节
3. 验证历史知识对比部分正确
4. 验证飞书推送（mock测试）

## 交付方式

代码写到 ~/intelligence_center/src/ 目录。
完成后在群里 @Emma 回复。
