import importlib
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


class Phase2Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.wiki = self.root / "knowledge"
        self.db = self.root / "wiki_fts.db"

    def tearDown(self):
        self.tmp.cleanup()

    def write_page(self, relative, text):
        path = self.wiki / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def seed_pages(self):
        self.write_page(
            "reports/wiki/美妆/抗初老趋势.md",
            "---\ntags: 抗初老, 早C晚A\n---\n# 抗初老趋势\n抗初老市场关注早C晚A和敏感肌。",
        )
        self.write_page("reports/wiki/美妆/防晒.md", "# 防晒趋势\ntags: 防晒, 美妆\n防晒消费升级。")
        self.write_page("reports/wiki/个护/洗护.md", "# 洗护趋势\nkeywords: 头皮护理, 个护\n头皮护理增长。")
        self.write_page("web/wiki/美妆/成分.md", "# 成分党\nkeywords: 视黄醇, 抗初老\n成分党关注视黄醇。")
        self.write_page("web/wiki/食品饮料/低糖.md", "# 低糖饮料\ntags: 低糖, 饮料\n低糖饮料趋势。")

    def test_indexer_full(self):
        kb_indexer = importlib.import_module("kb_indexer")
        self.seed_pages()

        stats = kb_indexer.build_index(str(self.wiki), str(self.db), full=True)

        self.assertEqual(stats["indexed"], 5)
        conn = sqlite3.connect(str(self.db))
        try:
            count = conn.execute("SELECT COUNT(*) FROM wiki_pages").fetchone()[0]
            rows = conn.execute("SELECT title FROM wiki_fts WHERE wiki_fts MATCH ?", ("抗初老",)).fetchall()
        finally:
            conn.close()
        self.assertEqual(count, 5)
        self.assertTrue(any("抗初老" in row[0] for row in rows))

    def test_indexer_incremental(self):
        kb_indexer = importlib.import_module("kb_indexer")
        page = self.write_page("reports/wiki/美妆/抗初老趋势.md", "# 抗初老趋势\n旧内容")
        kb_indexer.build_index(str(self.wiki), str(self.db), full=True)
        os.utime(page, None)
        page.write_text("# 抗初老趋势\n新增早C晚A内容", encoding="utf-8")

        stats = kb_indexer.build_index(str(self.wiki), str(self.db), full=False)

        self.assertEqual(stats["indexed"], 1)
        conn = sqlite3.connect(str(self.db))
        try:
            row = conn.execute("SELECT content FROM wiki_pages WHERE path = ?", (str(page),)).fetchone()
            hits = conn.execute("SELECT title FROM wiki_fts WHERE wiki_fts MATCH ?", ("早C晚A",)).fetchall()
        finally:
            conn.close()
        self.assertIn("新增早C晚A内容", row[0])
        self.assertEqual(len(hits), 1)

    def test_retriever_search(self):
        kb_indexer = importlib.import_module("kb_indexer")
        kb_retriever = importlib.import_module("kb_retriever")
        self.seed_pages()
        kb_indexer.build_index(str(self.wiki), str(self.db), full=True)
        output = self.root / "context" / "kb_retrieval.md"

        results = kb_retriever.retrieve(["抗初老", "早C晚A"], str(self.db), top=3, output_path=str(output))

        text = output.read_text(encoding="utf-8")
        self.assertGreaterEqual(len(results), 1)
        self.assertIn("# 历史知识检索结果", text)
        self.assertIn("### 1.", text)
        self.assertIn("**路径**", text)
        self.assertIn("**摘要**", text)

    def test_retriever_from_job(self):
        brief_router = importlib.import_module("brief_router")
        kb_indexer = importlib.import_module("kb_indexer")
        kb_retriever = importlib.import_module("kb_retriever")
        self.seed_pages()
        kb_indexer.build_index(str(self.wiki), str(self.db), full=True)
        job_dir = Path(
            brief_router.create_job(
                {"project_id": "KB测试", "direction": "抗初老研究", "objective": ["趋势"], "keywords": ["抗初老"]},
                track="B",
                root_dir=self.root,
            )
        )

        results = kb_retriever.retrieve_for_job(str(job_dir), str(self.db), top=5)

        output = job_dir / "context" / "kb_retrieval.md"
        self.assertTrue(output.exists())
        self.assertGreaterEqual(len(results), 1)
        self.assertIn("抗初老", output.read_text(encoding="utf-8"))

    def test_linker_links_pages(self):
        kb_indexer = importlib.import_module("kb_indexer")
        kb_linker = importlib.import_module("kb_linker")
        self.seed_pages()
        new_page = self.write_page("reports/wiki/美妆/新页面.md", "# 新页面\ntags: 抗初老, 早C晚A\n新页面讨论抗初老。")
        kb_indexer.build_index(str(self.wiki), str(self.db), full=True)

        links = kb_linker.link_page(str(new_page), str(self.db), top=5)

        conn = sqlite3.connect(str(self.db))
        try:
            rows = conn.execute("SELECT source_path, target_path, link_type FROM kb_links").fetchall()
        finally:
            conn.close()
        self.assertGreaterEqual(len(links), 1)
        self.assertGreaterEqual(len(rows), 1)
        self.assertTrue(all(row[0] == str(new_page) for row in rows))

    def test_linker_idempotent(self):
        kb_indexer = importlib.import_module("kb_indexer")
        kb_linker = importlib.import_module("kb_linker")
        self.seed_pages()
        new_page = self.write_page("reports/wiki/美妆/新页面.md", "# 新页面\ntags: 抗初老, 早C晚A\n新页面讨论抗初老。")
        kb_indexer.build_index(str(self.wiki), str(self.db), full=True)

        kb_linker.link_page(str(new_page), str(self.db), top=5)
        first = new_page.read_text(encoding="utf-8")
        kb_linker.link_page(str(new_page), str(self.db), top=5)
        second = new_page.read_text(encoding="utf-8")

        self.assertEqual(first, second)
        self.assertEqual(second.count("## 相关阅读"), 1)

    def test_linker_appends_related_reading(self):
        kb_indexer = importlib.import_module("kb_indexer")
        kb_linker = importlib.import_module("kb_linker")
        self.seed_pages()
        new_page = self.write_page("reports/wiki/美妆/新页面.md", "# 新页面\nkeywords: 抗初老, 早C晚A\n新页面讨论抗初老。")
        kb_indexer.build_index(str(self.wiki), str(self.db), full=True)

        kb_linker.link_page(str(new_page), str(self.db), top=5)

        text = new_page.read_text(encoding="utf-8")
        self.assertIn("## 相关阅读", text)
        self.assertIn("- [", text)


if __name__ == "__main__":
    unittest.main()
