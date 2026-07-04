#!/usr/bin/env python3
"""
fix_unknown_source.py — 批量修复 source_type='unknown' 的DB记录

用法:
  python3 fix_unknown_source.py --dry-run    # 只打印不写入
  python3 fix_unknown_source.py --apply      # 实际写入
"""

import sqlite3
import json
import re
import os
import sys
from collections import Counter
from datetime import datetime

DB_PATH = "/home/ubuntu/clawd/knowledge/pipeline_checklist.db"

# basename 命名模式 → source_type 映射（按优先级）
PATTERNS = [
    # Research round 产出
    (re.compile(r'-r\d{2}-q\d{2}'), "web_research"),
    # 带日期前缀的 web research（如 2026-中国AI企业、2023年全球营销、2022H1-中国）
    (re.compile(r'^\d{4}[-年H]'), "web_research"),
    # Industry rotation
    (re.compile(r'industry_rotation', re.IGNORECASE), "industry_rotation"),
    # MinerU 管线：topic_prefix + ID 模式（healthcare_xxx, uncategorized_xxx, b3_xxx 等）
    (re.compile(r'^(healthcare|uncategorized|automotive|finance|tech|consumer|education|retail|energy|media|[a-f0-9]{2})_'), "mineru_enrichment"),
    # OCR 管线：纯数字前缀模式（0184_xxx, 149681_xxx 等）
    (re.compile(r'^\d{4,6}_'), "ocr_pipeline"),
    # 旧格式：带 _ocr/_summary 后缀
    (re.compile(r'_ocr|_summary'), "ocr_pipeline"),
    # 兜底：任何剩余的未匹配记录，根据data中的wiki_path判断
    # （classify_basename中的wiki_path检查会处理这个兜底）
]

def classify_basename(basename, data_json):
    """根据basename和data JSON判断source_type"""
    # Pattern 1-5: basename模式匹配
    for pattern, source in PATTERNS:
        if pattern.search(basename):
            return source
    
    # Pattern 6: data JSON中的字段
    try:
        data = json.loads(data_json) if data_json else {}
    except:
        data = {}
    
    # 检查 wiki_path（所有PDF管线产出的wiki_path都在 reports/wiki/ 下）
    wiki_path = data.get("wiki_path", "")
    if "research/rounds" in wiki_path:
        return "web_research"
    if "industry_rotation" in wiki_path:
        return "industry_rotation"
    
    # 检查 summarized_by
    summarized_by = data.get("summarized_by", "")
    if "mimo" in summarized_by.lower():
        return "mimo_enrichment"
    
    # wiki_path 兜底：reports/wiki/ 下都是 PDF 管线产出
    wiki_path = data.get("wiki_path", "")
    if "reports/wiki/" in wiki_path:
        return "ocr_pipeline"
    if "web/wiki/" in wiki_path:
        return "web_research"
    
    return None  # 无法分类

def main():
    dry_run = "--dry-run" in sys.argv
    apply_mode = "--apply" in sys.argv
    
    if not dry_run and not apply_mode:
        print("用法: python3 fix_unknown_source.py --dry-run | --apply")
        sys.exit(1)
    
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    
    # 查所有 unknown 记录
    rows = conn.execute(
        "SELECT id, basename, data FROM files WHERE source_type='unknown'"
    ).fetchall()
    
    print(f"Total unknown: {len(rows)}")
    
    # 分类统计
    stats = Counter()
    matched = []
    unmatched = []
    
    for row_id, basename, data_json in rows:
        source = classify_basename(basename or "", data_json)
        if source:
            stats[source] += 1
            matched.append((row_id, basename, source))
        else:
            unmatched.append((row_id, basename))
    
    # 打印统计
    print("\n=== Classification Results ===")
    for source, count in stats.most_common():
        print(f"  {source}: {count}")
    print(f"  UNMATCHED: {len(unmatched)}")
    
    # 打印unmatched样本
    if unmatched:
        print(f"\n=== Unmatched Samples (first 20) ===")
        for row_id, bn in unmatched[:20]:
            print(f"  [{row_id}] {bn}")
    
    # 执行更新
    if apply_mode and matched:
        print(f"\n=== Applying {len(matched)} updates ===")
        now = datetime.now().isoformat()
        updated = 0
        for row_id, basename, source in matched:
            # 读取现有data，更新source_type字段
            data_json = conn.execute(
                "SELECT data FROM files WHERE id=?", (row_id,)
            ).fetchone()[0]
            try:
                data = json.loads(data_json) if data_json else {}
            except:
                data = {}
            data["source_type"] = source
            data["source_fixed_at"] = now
            
            conn.execute(
                "UPDATE files SET source_type=?, data=? WHERE id=?",
                (source, json.dumps(data, ensure_ascii=False), row_id)
            )
            updated += 1
        
        conn.commit()
        print(f"Updated: {updated}")
        
        # 验证
        remaining = conn.execute(
            "SELECT COUNT(*) FROM files WHERE source_type='unknown'"
        ).fetchone()[0]
        print(f"Remaining unknown: {remaining}")
    elif dry_run:
        print("\n[DRY RUN] No changes made.")
    
    conn.close()

if __name__ == "__main__":
    main()
