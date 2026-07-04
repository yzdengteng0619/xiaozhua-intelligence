#!/usr/bin/env python3
"""
test_fix_unknown_source.py — fix_unknown_source.py 的分类规则测试

验证各种 basename 模式的 source_type 分类正确性，
以及 DB 写入逻辑（dry-run vs apply）的完整性。
"""

import importlib
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "pipeline"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

# fix_unknown_source has a hardcoded DB_PATH; we patch it after import
import fix_unknown_source as fus


class ClassifyBasenameTests(unittest.TestCase):
    """测试 classify_basename() 对各种命名模式的分类正确性"""

    def _classify(self, basename, data_json=None):
        return fus.classify_basename(basename, data_json or "{}")

    # ── web_research 模式 ──────────────────────────────────────

    def test_round_pattern_rNN_qNN(self):
        """-r00-q00 模式 → web_research"""
        self.assertEqual(self._classify("AI趋势-r02-q05"), "web_research")
        self.assertEqual(self._classify("consumer-insights-r10-q15"), "web_research")

    def test_year_prefix_pattern(self):
        """年份前缀 → web_research"""
        self.assertEqual(self._classify("2026-中国AI企业"), "web_research")
        self.assertEqual(self._classify("2023年全球营销"), "web_research")
        self.assertEqual(self._classify("2022H1-中国快消"), "web_research")

    def test_year_prefix_four_digits_required(self):
        """3位数字前缀不应匹配年份模式"""
        # 202 不到4位，不应匹配 ^\d{4}
        self.assertNotEqual(self._classify("202-xxx"), "web_research")

    # ── industry_rotation 模式 ─────────────────────────────────

    def test_industry_rotation(self):
        """industry_rotation 关键词 → industry_rotation"""
        self.assertEqual(self._classify("industry_rotation_2026Q1"), "industry_rotation")
        self.assertEqual(self._classify("Industry_Rotation_summary"), "industry_rotation")

    # ── mineru_enrichment 模式 ─────────────────────────────────

    def test_mineru_topic_prefix(self):
        """topic_prefix_ 模式 → mineru_enrichment"""
        for prefix in ("healthcare", "uncategorized", "automotive", "finance",
                       "tech", "consumer", "education", "retail", "energy", "media"):
            result = self._classify(prefix + "_001_report")
            self.assertEqual(result, "mineru_enrichment",
                             msg=f"{prefix}_001 should be mineru_enrichment")

    def test_mineru_hex_prefix(self):
        """2位十六进制前缀 → mineru_enrichment"""
        self.assertEqual(self._classify("b3_xxx_summary"), "mineru_enrichment")
        self.assertEqual(self._classify("a1_report"), "mineru_enrichment")
        self.assertEqual(self._classify("0f_test"), "mineru_enrichment")

    def test_hex_prefix_only_two_chars(self):
        """3位字符前缀不应匹配 [a-f0-9]{2}_"""
        # xyz_ 不含十六进制字符
        result = self._classify("xyz_report")
        self.assertNotEqual(result, "mineru_enrichment")

    # ── ocr_pipeline 模式 ──────────────────────────────────────

    def test_ocr_numeric_prefix(self):
        """纯数字前缀 → ocr_pipeline"""
        self.assertEqual(self._classify("0184_xxx"), "ocr_pipeline")
        self.assertEqual(self._classify("149681_report"), "ocr_pipeline")
        self.assertEqual(self._classify("12345_白皮书"), "ocr_pipeline")

    def test_ocr_short_numeric_prefix_not_matched(self):
        """3位以下数字前缀不应匹配 ^\d{4,6}_"""
        # 123_ 只有3位，不匹配 \d{4,6}
        result = self._classify("123_xxx")
        self.assertNotEqual(result, "ocr_pipeline")

    def test_ocr_suffix_pattern(self):
        """_ocr / _summary 后缀 → ocr_pipeline"""
        self.assertEqual(self._classify("report_ocr"), "ocr_pipeline")
        self.assertEqual(self._classify("白皮书_summary"), "ocr_pipeline")

    # ── data JSON 兜底 ─────────────────────────────────────────

    def test_wiki_path_research_rounds(self):
        """data.wiki_path 含 research/rounds → web_research"""
        data = json.dumps({"wiki_path": "research/rounds/2026Q1/xxx.md"})
        self.assertEqual(self._classify("unknown_name", data), "web_research")

    def test_wiki_path_reports_wiki(self):
        """data.wiki_path 含 reports/wiki/ → ocr_pipeline"""
        data = json.dumps({"wiki_path": "reports/wiki/healthcare/xxx.md"})
        self.assertEqual(self._classify("unknown_name", data), "ocr_pipeline")

    def test_wiki_path_web_wiki(self):
        """data.wiki_path 含 web/wiki/ → web_research"""
        data = json.dumps({"wiki_path": "web/wiki/consumer/xxx.md"})
        self.assertEqual(self._classify("unknown_name", data), "web_research")

    def test_wiki_path_industry_rotation(self):
        """data.wiki_path 含 industry_rotation → industry_rotation"""
        data = json.dumps({"wiki_path": "industry_rotation/2026.md"})
        self.assertEqual(self._classify("unknown_name", data), "industry_rotation")

    def test_summarized_by_mimo(self):
        """data.summarized_by 含 mimo → mimo_enrichment"""
        data = json.dumps({"summarized_by": "MiMo-7B-RL"})
        self.assertEqual(self._classify("unknown_name", data), "mimo_enrichment")

    def test_summarized_by_case_insensitive(self):
        """mimo 匹配不区分大小写"""
        data = json.dumps({"summarized_by": "MIMO-v2"})
        self.assertEqual(self._classify("unknown_name", data), "mimo_enrichment")

    # ── 无法分类 ───────────────────────────────────────────────

    def test_truly_unknown_returns_none(self):
        """完全无法分类的 basename → None"""
        data = json.dumps({"wiki_path": "", "summarized_by": ""})
        self.assertIsNone(self._classify("random_name_no_pattern", data))

    def test_empty_basename(self):
        """空 basename → None"""
        self.assertIsNone(self._classify("", "{}"))

    def test_none_data_json(self):
        """data_json 为 None → 不崩溃"""
        result = fus.classify_basename("random_name", None)
        # Should not crash, returns None or a classification
        self.assertIn(result, (None, "web_research", "ocr_pipeline", "mineru_enrichment",
                               "industry_rotation", "mimo_enrichment"))

    def test_invalid_json_data(self):
        """data_json 为非法 JSON → 不崩溃"""
        result = fus.classify_basename("random_name", "not json{{{")
        self.assertIsNone(result)

    # ── 优先级测试 ─────────────────────────────────────────────

    def test_pattern_priority_over_data(self):
        """basename 模式优先于 data JSON"""
        # basename 匹配 web_research，但 data 指向 ocr_pipeline
        data = json.dumps({"wiki_path": "reports/wiki/healthcare/xxx.md"})
        result = self._classify("2026-中国AI", data)
        self.assertEqual(result, "web_research",
                         msg="basename pattern should take priority over data JSON")


class DBIntegrationTests(unittest.TestCase):
    """测试 DB 写入逻辑（使用临时 DB）"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "test.db")
        self._orig_db = fus.DB_PATH
        fus.DB_PATH = self.db_path
        self._setup_db()

    def tearDown(self):
        fus.DB_PATH = self._orig_db
        self.tmp.cleanup()

    def _setup_db(self):
        """创建与生产相同的 schema"""
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
        # 插入测试数据
        test_rows = [
            ("2026-中国AI企业", "", "summarized", "unknown", "{}"),
            ("b3_healthcare_001", "", "summarized", "unknown", "{}"),
            ("0184_白皮书", "", "summarized", "unknown", "{}"),
            ("random_unknown_name", "", "summarized", "unknown",
             json.dumps({"wiki_path": "reports/wiki/tech/x.md"})),
            ("truly_unknown", "", "summarized", "unknown", "{}"),
        ]
        for row in test_rows:
            conn.execute(
                "INSERT INTO files (basename, pdf_name, status, source_type, data) VALUES (?,?,?,?,?)",
                row
            )
        conn.commit()
        conn.close()

    def test_dry_run_does_not_modify(self):
        """--dry-run 不修改 DB"""
        fus.main.__wrapped__ if hasattr(fus.main, '__wrapped__') else None
        # 直接调用 main with --dry-run
        orig_argv = sys.argv
        sys.argv = ["fix_unknown_source.py", "--dry-run"]
        try:
            fus.main()
        finally:
            sys.argv = orig_argv

        conn = sqlite3.connect(self.db_path)
        unknown_count = conn.execute(
            "SELECT COUNT(*) FROM files WHERE source_type='unknown'"
        ).fetchone()[0]
        conn.close()
        self.assertEqual(unknown_count, 5, "Dry-run should not modify DB")

    def test_apply_updates_known_patterns(self):
        """--apply 正确更新可分类的记录"""
        orig_argv = sys.argv
        sys.argv = ["fix_unknown_source.py", "--apply"]
        try:
            fus.main()
        finally:
            sys.argv = orig_argv

        conn = sqlite3.connect(self.db_path)
        rows = conn.execute(
            "SELECT basename, source_type FROM files ORDER BY basename"
        ).fetchall()
        conn.close()

        results = {row[0]: row[1] for row in rows}
        # 4 条可分类，1 条不可
        self.assertEqual(results["2026-中国AI企业"], "web_research")
        self.assertEqual(results["b3_healthcare_001"], "mineru_enrichment")
        self.assertEqual(results["0184_白皮书"], "ocr_pipeline")
        self.assertEqual(results["random_unknown_name"], "ocr_pipeline")
        self.assertEqual(results["truly_unknown"], "unknown")

    def test_apply_writes_source_type_in_data_json(self):
        """--apply 后 data JSON 中包含 source_type 和 source_fixed_at"""
        orig_argv = sys.argv
        sys.argv = ["fix_unknown_source.py", "--apply"]
        try:
            fus.main()
        finally:
            sys.argv = orig_argv

        conn = sqlite3.connect(self.db_path)
        row = conn.execute(
            "SELECT data FROM files WHERE basename='2026-中国AI企业'"
        ).fetchone()
        conn.close()

        data = json.loads(row[0])
        self.assertEqual(data["source_type"], "web_research")
        self.assertIn("source_fixed_at", data)


if __name__ == "__main__":
    unittest.main()
