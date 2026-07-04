# Pipeline Scripts

Pipeline 管理脚本，部署在小爪节点。

## 脚本列表

### register_round.py
Research round 产出即时注册到 pipeline_checklist.db。
- `python3 register_round.py <round-dir>` — 注册单个 round
- `python3 register_round.py --scan` — 批量扫描注册
- 被 research_worker.py 调用（实时注册钩子）

### fix_unknown_source.py
批量修复 source_type='unknown' 的 DB 记录。
- `python3 fix_unknown_source.py --dry-run` — 预览分类结果
- `python3 fix_unknown_source.py --apply` — 执行修复
- 分类规则：basename 命名模式 + data JSON wiki_path 兜底

## 部署位置
生产环境：`~/clawd/scripts/`
GitHub 仓库：`src/pipeline/`

## DB 路径
`~/clawd/knowledge/pipeline_checklist.db`
