#!/usr/bin/env python3
"""
test_register_round.py — register_round.py 的注册逻辑测试

验证 register_round_dir() 和 scan_and_register() 的正确性，
包括：幂等性、DB 写入、已有评分保留、异常处理。
"""

import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "pipeline"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import register_round as rr


class RegisterRoundTests(unittest.TestCase):
    """测试 register_round_dir() 注册逻辑"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db_path = str(self.root / "test.db")
        self.knowledge_base = str(self.root / "knowledge")

        # Patch DB_PATH and KNOWLEDGE_BASE
        self._orig_db = rr.DB_PATH
        self._orig_kb = rr.KNOWLEDGE_BASE
        rr.DB_PATH = self.db_path
        rr.KNOWLEDGE_BASE = self.knowledge_base

        self._setup_db()
        self._setup_round_dir()

    def tearDown(self):
        rr.DB_PATH = self._orig_db
        rr.KNOWLEDGE_BASE = self._orig_kb
        self.tmp.cleanup()

    def _setup_db(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            CREATE TABLE files (
                id INTEGER PRIMARY KEY,
                basename TEXT,
                pdf_name TEXT,
                status TEXT,
                source_type TEXT,
                data TEXT
            )
        """)
        conn.commit()
        conn.close()

    def _setup_round_dir(self):
        """创建测试用 round 目录"""
        self.round_dir = os.path.join(self.knowledge_base, "research", "rounds", "2026Q1-ai-r00")
        os.makedirs(self.round_dir)
        # 写几个 .md 文件
        for name in ["AI趋势分析.md", "大模型应用.md", "notes.txt"]:
            path = os.path.join(self.round_dir, name)
            with open(path, "w", encoding="utf-8") as f:
                f.write("# " + name.replace(".md", "") + "\n\n内容\n")

    def _count_files(self):
        conn = sqlite3.connect(self.db_path)
        count = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        conn.close()
        return count

    def test_register_new_round(self):
        """注册新 round → 2 个 .md 文件入库（.txt 跳过）"""
        count = rr.register_round_dir(self.round_dir)
        self.assertEqual(count, 2)
        self.assertEqual(self._count_files(), 2)

    def test_register_skips_txt_files(self):
        """.txt 文件不被注册"""
        rr.register_round_dir(self.round_dir)
        conn = sqlite3.connect(self.db_path)
        basenames = [row[0] for row in conn.execute("SELECT basename FROM files").fetchall()]
        conn.close()
        self.assertNotIn("notes", basenames)
        self.assertIn("AI趋势分析", basenames)

    def test_register_idempotent(self):
        """重复注册同一 round → 不产生重复记录"""
        rr.register_round_dir(self.round_dir)
        rr.register_round_dir(self.round_dir)
        self.assertEqual(self._count_files(), 2)

    def test_register_nonexistent_dir(self):
        """注册不存在的目录 → 返回 0，不崩溃"""
        count = rr.register_round_dir("/nonexistent/path/xxx")
        self.assertEqual(count, 0)

    def test_register_writes_correct_source_type(self):
        """注册的记录 source_type = web_research"""
        rr.register_round_dir(self.round_dir)
        conn = sqlite3.connect(self.db_path)
        row = conn.execute("SELECT source_type FROM files LIMIT 1").fetchone()
        conn.close()
        self.assertEqual(row[0], "web_research")

    def test_register_writes_wiki_path_in_data(self):
        """注册的 data JSON 中包含 wiki_path"""
        rr.register_round_dir(self.round_dir)
        conn = sqlite3.connect(self.db_path)
        row = conn.execute("SELECT data FROM files WHERE basename='AI趋势分析'").fetchone()
        conn.close()
        data = json.loads(row[0])
        self.assertIn("wiki_path", data)
        self.assertIn("AI趋势分析.md", data["wiki_path"])

    def test_register_preserves_existing_score(self):
        """已有评分的记录更新时保留评分"""
        # 先手动插入一条带评分的记录
        conn = sqlite3.connect(self.db_path)
        old_data = json.dumps({
            "basename": "AI趋势分析",
            "score": 8.5,
            "score_model": "gpt-4",
            "score_tags": ["insightful"],
            "score_reason": "good analysis",
        })
        conn.execute(
            "INSERT INTO files (basename, pdf_name, status, source_type, data) VALUES (?,?,?,?,?)",
            ("AI趋势分析", "", "scored", "unknown", old_data)
        )
        conn.commit()
        conn.close()

        # 注册 round
        rr.register_round_dir(self.round_dir)

        # 验证评分被保留
        conn = sqlite3.connect(self.db_path)
        row = conn.execute("SELECT data FROM files WHERE basename='AI趋势分析'").fetchone()
        conn.close()
        data = json.loads(row[0])
        self.assertEqual(data["score"], 8.5)
        self.assertEqual(data["score_model"], "gpt-4")
        self.assertEqual(data["score_tags"], ["insightful"])

    def test_register_does_not_preserve_pending_score(self):
        """score_model='pending' 的评分不保留"""
        conn = sqlite3.connect(self.db_path)
        old_data = json.dumps({
            "basename": "AI趋势分析",
            "score": 0,
            "score_model": "pending",
        })
        conn.execute(
            "INSERT INTO files (basename, pdf_name, status, source_type, data) VALUES (?,?,?,?,?)",
            ("AI趋势分析", "", "pending", "unknown", old_data)
        )
        conn.commit()
        conn.close()

        rr.register_round_dir(self.round_dir)

        conn = sqlite3.connect(self.db_path)
        row = conn.execute("SELECT data FROM files WHERE basename='AI趋势分析'").fetchone()
        conn.close()
        data = json.loads(row[0])
        self.assertNotIn("score_model", data)


class ScanAndRegisterTests(unittest.TestCase):
    """测试 scan_and_register() 批量扫描"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db_path = str(self.root / "test.db")
        self.knowledge_base = str(self.root / "knowledge")

        self._orig_db = rr.DB_PATH
        self._orig_kb = rr.KNOWLEDGE_BASE
        rr.DB_PATH = self.db_path
        rr.KNOWLEDGE_BASE = self.knowledge_base

        self._setup_db()
        self._setup_nested_rounds()

    def tearDown(self):
        rr.DB_PATH = self._orig_db
        rr.KNOWLEDGE_BASE = self._orig_kb
        self.tmp.cleanup()

    def _setup_db(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            CREATE TABLE files (
                id INTEGER PRIMARY KEY,
                basename TEXT,
                pdf_name TEXT,
                status TEXT,
                source_type TEXT,
                data TEXT
            )
        """)
        conn.commit()
        conn.close()

    def _setup_nested_rounds(self):
        """创建扁平 + 嵌套两种目录结构"""
        rounds_base = os.path.join(self.knowledge_base, "research", "rounds")

        # 扁平结构: rounds/flat-r00/*.md
        flat = os.path.join(rounds_base, "flat-r00")
        os.makedirs(flat)
        for name in ["flat-1.md", "flat-2.md"]:
            with open(os.path.join(flat, name), "w", encoding="utf-8") as f:
                f.write("# " + name + "\n")

        # 嵌套结构: rounds/direction/nested-r00/*.md
        nested_dir = os.path.join(rounds_base, "consumer")
        nested_round = os.path.join(nested_dir, "nested-r00")
        os.makedirs(nested_round)
        for name in ["nested-1.md", "nested-2.md", "nested-3.md"]:
            with open(os.path.join(nested_round, name), "w", encoding="utf-8") as f:
                f.write("# " + name + "\n")

    def test_scan_finds_flat_and_nested(self):
        """scan 能同时找到扁平和嵌套结构的 .md 文件"""
        rr.scan_and_register()
        conn = sqlite3.connect(self.db_path)
        count = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        conn.close()
        self.assertEqual(count, 5, "Should find 2 flat + 3 nested = 5 files")

    def test_scan_idempotent(self):
        """重复 scan 不产生重复"""
        rr.scan_and_register()
        rr.scan_and_register()
        conn = sqlite3.connect(self.db_path)
        count = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        conn.close()
        self.assertEqual(count, 5)

    def test_scan_nonexistent_rounds_dir(self):
        """rounds 目录不存在 → 不崩溃"""
        rr.KNOWLEDGE_BASE = str(self.root / "empty_kb")
        # Should not raise
        rr.scan_and_register()


if __name__ == "__main__":
    unittest.main()
