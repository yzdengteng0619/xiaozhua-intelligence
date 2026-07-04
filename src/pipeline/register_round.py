#!/usr/bin/env python3
"""
register_round.py — 将 research round 产出即时注册到 pipeline_checklist.db

用法:
  # 注册指定 round 目录
  python3 register_round.py /path/to/round-dir
  
  # 批量注册所有未注册的 round
  python3 register_round.py --scan

可被 research_worker.py 直接 import 调用:
  from register_round import register_round_dir
  register_round_dir("/path/to/round-dir")
"""

import sqlite3
import json
import os
import re
import sys
from datetime import datetime

DB_PATH = "/home/ubuntu/clawd/knowledge/pipeline_checklist.db"
KNOWLEDGE_BASE = "/home/ubuntu/clawd/knowledge"


def get_conn():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def register_file(basename, wiki_path, source_type, conn=None):
    """注册单个文件到DB"""
    close = False
    if conn is None:
        conn = get_conn()
        close = True
    
    try:
        existing = conn.execute(
            "SELECT id, data FROM files WHERE basename=?", (basename,)
        ).fetchone()

        now = datetime.now().isoformat()
        data = {
            "basename": basename,
            "wiki_path": wiki_path,
            "source_type": source_type,
            "registered_at": now,
        }

        if existing:
            try:
                old_data = json.loads(existing[1]) if existing[1] else {}
            except (json.JSONDecodeError, TypeError):
                old_data = {}
            # 保留已有评分
            if old_data.get("score_model") and old_data["score_model"] != "pending":
                data["score"] = old_data["score"]
                data["score_model"] = old_data["score_model"]
                data["score_tags"] = old_data.get("score_tags", [])
                data["score_reason"] = old_data.get("score_reason", "")
            conn.execute(
                "UPDATE files SET data=?, source_type=? WHERE id=?",
                (json.dumps(data, ensure_ascii=False), source_type, existing[0])
            )
            return "updated"
        else:
            conn.execute(
                "INSERT INTO files (basename, pdf_name, status, source_type, data) VALUES (?, ?, ?, ?, ?)",
                (basename, "", "summarized", source_type,
                 json.dumps(data, ensure_ascii=False))
            )
            return "inserted"
    except Exception as e:
        print(f"  [DB ERROR] {basename}: {e}")
        return "error"
    finally:
        if close:
            conn.close()


def register_round_dir(round_dir):
    """注册一个 round 目录下的所有 .md 文件"""
    if not os.path.isdir(round_dir):
        print(f"  [SKIP] Not a directory: {round_dir}")
        return 0
    
    conn = get_conn()
    count = 0
    round_name = os.path.basename(round_dir)
    
    for fname in os.listdir(round_dir):
        if not fname.endswith(".md"):
            continue
        basename = fname[:-3]  # 去掉 .md
        rel_path = os.path.relpath(
            os.path.join(round_dir, fname), KNOWLEDGE_BASE
        )
        result = register_file(basename, rel_path, "web_research", conn)
        if result in ("inserted", "updated"):
            count += 1
    
    conn.commit()
    conn.close()
    print(f"  [REGISTER] {round_name}: {count} files")
    return count


def scan_and_register():
    """扫描所有 round 目录，注册未入库的文件"""
    rounds_base = os.path.join(KNOWLEDGE_BASE, "research", "rounds")
    if not os.path.isdir(rounds_base):
        print(f"Rounds directory not found: {rounds_base}")
        return
    
    conn = get_conn()
    
    # 获取已注册的 basenames
    registered = set(
        row[0] for row in conn.execute("SELECT basename FROM files").fetchall()
    )
    
    total_new = 0
    total_dirs = 0
    
    # 支持两种目录结构：
    # 1. 扁平：rounds/{direction-sub-r00}/*.md
    # 2. 嵌套：rounds/{direction}/{direction-sub-r00}/*.md
    for entry in sorted(os.listdir(rounds_base)):
        entry_path = os.path.join(rounds_base, entry)
        if not os.path.isdir(entry_path):
            continue
        
        # 检查是否直接包含 .md 文件（扁平结构）
        md_files = [f for f in os.listdir(entry_path) if f.endswith(".md")]
        if md_files:
            # 扁平结构：entry 本身就是一个 round 目录
            round_dirs_to_scan = [entry_path]
        else:
            # 嵌套结构：entry 是方向目录，包含多个 round 目录
            round_dirs_to_scan = [
                os.path.join(entry_path, sub)
                for sub in sorted(os.listdir(entry_path))
                if os.path.isdir(os.path.join(entry_path, sub))
            ]
        
        for round_path in round_dirs_to_scan:
            total_dirs += 1
            new_in_round = 0
            round_name = os.path.basename(round_path)
            
            for fname in os.listdir(round_path):
                if not fname.endswith(".md"):
                    continue
                basename = fname[:-3]
                if basename in registered:
                    continue
                
                rel_path = os.path.relpath(
                    os.path.join(round_path, fname), KNOWLEDGE_BASE
                )
                result = register_file(basename, rel_path, "web_research", conn)
                if result == "inserted":
                    new_in_round += 1
                    registered.add(basename)
            
            if new_in_round > 0:
                print(f"  [NEW] {round_name}: {new_in_round} files")
                total_new += new_in_round
    
    conn.commit()
    conn.close()
    print(f"\nScan complete: {total_dirs} round dirs, {total_new} new files registered")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python3 register_round.py <round-dir> | --scan")
        sys.exit(1)
    
    if sys.argv[1] == "--scan":
        scan_and_register()
    else:
        register_round_dir(sys.argv[1])
