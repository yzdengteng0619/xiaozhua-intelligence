#!/usr/bin/env python3
"""
test_search_api.py — search_api.py 的 HTTP API 测试

使用 FastAPI TestClient 进行端点测试，无需启动真实服务器。
测试覆盖：/health, /stats, /search（正常+边界+错误情况）。
"""

import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
IC_SRC = SRC / "ic"
KB_SRC = SRC  # kb_common/kb_retriever/kb_indexer 在 src/ 下
for p in (str(SRC), str(IC_SRC)):
    if p not in sys.path:
        sys.path.insert(0, p)

# Patch DB path before importing search_api
from kb_common import get_db_path

# We'll set IC_DB_PATH env var to point to our test DB
import ic.search_api as api_mod


class SearchAPITests(unittest.TestCase):
    """FastAPI TestClient 测试"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db_path = os.path.join(cls.tmp.name, "test_fts.db")
        cls._setup_test_db()

        # Override the global DB path
        cls._orig_resolve = api_mod._resolve_db
        api_mod._resolve_db = lambda: cls.db_path

        from fastapi.testclient import TestClient
        cls.client = TestClient(api_mod.app)

    @classmethod
    def tearDownClass(cls):
        api_mod._resolve_db = cls._orig_resolve
        cls.tmp.cleanup()

    @classmethod
    def _setup_test_db(cls):
        """创建带 FTS5 索引的测试 DB + 插入测试数据"""
        sys.path.insert(0, str(SRC))
        from kb_indexer import connect, init_db, upsert_page
        from kb_common import now_iso

        conn = connect(cls.db_path)
        init_db(conn)

        # 插入测试 wiki 页面
        pages = [
            (os.path.join(cls.tmp.name, "reports", "wiki", "tech", "AI趋势.md"),
             "# AI趋势\n\n人工智能在2026年的发展趋势\nkeywords: AI, 人工智能, 趋势"),
            (os.path.join(cls.tmp.name, "reports", "wiki", "consumer", "消费洞察.md"),
             "# 消费洞察\n\nZ世代消费行为分析\nkeywords: 消费, Z世代, 洞察"),
            (os.path.join(cls.tmp.name, "web", "wiki", "marketing", "数字营销.md"),
             "# 数字营销\n\n2026年数字营销趋势报告\nkeywords: 营销, 数字化, 趋势"),
        ]
        for path, content in pages:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            upsert_page(conn, path)
        conn.commit()
        conn.close()

    # ── /health ───────────────────────────────────────────────

    def test_health_returns_ok(self):
        resp = self.client.get("/health")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "ok")
        self.assertTrue(data["db_exists"])

    def test_health_includes_db_path(self):
        resp = self.client.get("/health")
        data = resp.json()
        self.assertIn("db", data)

    # ── /stats ────────────────────────────────────────────────

    def test_stats_returns_page_count(self):
        resp = self.client.get("/stats")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertGreaterEqual(data["pages"], 3)
        self.assertTrue(data["exists"])

    # ── /search 正常情况 ──────────────────────────────────────

    def test_search_returns_results(self):
        resp = self.client.get("/search", params={"q": "AI", "top": 5})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["query"], "AI")
        self.assertGreater(data["count"], 0)
        self.assertIn("results", data)
        # 验证结果结构
        result = data["results"][0]
        self.assertIn("title", result)
        self.assertIn("path", result)
        self.assertIn("rank", result)

    def test_search_multiple_keywords(self):
        """逗号分隔多关键词"""
        resp = self.client.get("/search", params={"q": "AI,营销", "top": 10})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertGreater(data["count"], 0)

    def test_search_top_limit(self):
        """top 参数限制返回数"""
        resp = self.client.get("/search", params={"q": "趋势", "top": 1})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertLessEqual(data["count"], 1)

    # ── /search 边界 & 错误 ───────────────────────────────────

    def test_search_empty_query_returns_400(self):
        resp = self.client.get("/search", params={"q": ""})
        self.assertEqual(resp.status_code, 400)

    def test_search_whitespace_query_returns_400(self):
        resp = self.client.get("/search", params={"q": "   "})
        self.assertEqual(resp.status_code, 400)

    def test_search_no_results(self):
        """无结果的关键词返回空列表"""
        resp = self.client.get("/search", params={"q": "不存在的关键词xyz123"})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["count"], 0)
        self.assertEqual(data["results"], [])

    def test_search_top_validation_min(self):
        """top < 1 → 422"""
        resp = self.client.get("/search", params={"q": "AI", "top": 0})
        self.assertEqual(resp.status_code, 422)

    def test_search_top_validation_max(self):
        """top > 100 → 422"""
        resp = self.client.get("/search", params={"q": "AI", "top": 101})
        self.assertEqual(resp.status_code, 422)

    def test_search_missing_q_param(self):
        """缺少 q 参数 → 422"""
        resp = self.client.get("/search")
        self.assertEqual(resp.status_code, 422)


if __name__ == "__main__":
    unittest.main()
