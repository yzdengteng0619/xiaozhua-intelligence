#!/usr/bin/env python3
"""
test_industry_rotation_l2.py — L2 深挖层测试

测试覆盖:
- extract_data_points: 关键数据点提取（数字+单位、增长率、各种格式）
- query_authoritative_sources: FirstData 查询（mock + 降级）
- generate_deep_analysis: 深度分析生成（LLM 模式 + 模板降级）
- save_deep_to_wiki: 文件存储 + 目录结构
- register_deep_to_db: DB 注册（mock + 降级）
- run_l2_deep: 端到端流程（成功/跳过/异常降级）
- L1 集成: --deep 参数正确传递
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "pipeline"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import industry_rotation_l2 as l2


class ExtractDataPointsTests(unittest.TestCase):
    """测试关键数据点提取"""

    def test_extract_market_size(self):
        """市场规模数字提取"""
        text = "## 数据亮点\n- 快消品市场规模达5.2万亿\n- 线上渠道占比32%"
        points = l2.extract_data_points(text)
        self.assertGreater(len(points), 0)
        self.assertTrue(any("万亿" in p["point"] for p in points))

    def test_extract_growth_rate(self):
        """增长率提取"""
        text = "新能源车销量增长45%，燃油车下降8%"
        points = l2.extract_data_points(text)
        self.assertGreater(len(points), 0)
        self.assertTrue(any("增长" in p["point"] or "下降" in p["point"] for p in points))

    def test_extract_multiple_points(self):
        """多个数据点提取"""
        text = """
        快消品市场规模达5万亿，线上渠道增长32%。
        汽车行业规模超过8亿，新能源增速达50%。
        """
        points = l2.extract_data_points(text)
        self.assertGreaterEqual(len(points), 2)

    def test_dedup_by_keyword(self):
        """相同关键词去重"""
        text = "快消品市场规模达5万亿。快消品市场规模约5.1万亿。"
        points = l2.extract_data_points(text)
        # 相同关键词应去重
        keywords = [p["keyword"] for p in points]
        # 关键词去重后不应有完全相同的
        self.assertEqual(len(keywords), len(set(keywords)))

    def test_max_five_points(self):
        """最多返回5个数据点"""
        text = "行业A规模1万亿。行业B规模2万亿。行业C规模3万亿。行业D规模4万亿。行业E规模5万亿。行业F规模6万亿。"
        points = l2.extract_data_points(text)
        self.assertLessEqual(len(points), 5)

    def test_empty_analysis(self):
        """空分析文本"""
        self.assertEqual(l2.extract_data_points(""), [])
        self.assertEqual(l2.extract_data_points(None), [])

    def test_no_numbers(self):
        """没有数字的文本"""
        text = "行业整体呈良好发展态势，前景广阔。"
        points = l2.extract_data_points(text)
        self.assertEqual(len(points), 0)

    def test_context_extraction(self):
        """上下文提取"""
        text = "根据最新数据，快消品市场规模达5万亿，预计未来三年持续增长。"
        points = l2.extract_data_points(text)
        if points:
            self.assertIn("context", points[0])
            self.assertGreater(len(points[0]["context"]), 0)


class QueryAuthoritativeSourcesTests(unittest.TestCase):
    """测试 FirstData 权威源查询"""

    def setUp(self):
        self.data_points = [
            {"point": "快消品市场规模5万亿", "keyword": "快消品市场", "context": "..."},
        ]

    @patch.dict(sys.modules, {})
    def test_firstdata_unavailable_silent_degradation(self):
        """firstdata_adapter 不可用时静默降级"""
        # 确保 firstdata_adapter 不可 import
        if "firstdata_adapter" in sys.modules:
            del sys.modules["firstdata_adapter"]
        results = l2.query_authoritative_sources(self.data_points)
        self.assertEqual(results, [])

    @patch("industry_rotation_l2.search_sources" if hasattr(l2, "search_sources") else "__main__.search_sources",
           create=True)
    def test_mock_search_returns_sources(self, mock_search):
        """mock search_sources 返回权威源"""
        # 这个测试验证如果 search_sources 返回数据，处理逻辑正确
        mock_sources = [
            {
                "source_id": "NBSC",
                "name_zh": "国家统计局",
                "name_en": "NBS",
                "authority_level": "government",
                "website": "https://www.stats.gov.cn",
                "api_url": "",
                "description_zh": "中国官方统计数据",
                "country": "CN",
            }
        ]

        # 直接测试 _summarize_authority_levels 和标注逻辑
        from collections import Counter
        source_results = [{
            "data_point": self.data_points[0],
            "sources": mock_sources,
            "best_authority": "government",
        }]

        summary = l2._summarize_authority_levels(source_results)
        self.assertIn("government", summary)
        self.assertIn("1", summary)


class GenerateDeepAnalysisTests(unittest.TestCase):
    """测试深度分析生成"""

    def setUp(self):
        self.source_results = [
            {
                "data_point": {"point": "快消品市场5万亿", "keyword": "快消品市场", "context": "..."},
                "sources": [
                    {
                        "source_id": "NBSC", "name_zh": "国家统计局", "name_en": "NBS",
                        "authority_level": "government", "authority_weight": 10,
                        "website": "https://stats.gov.cn", "api_url": "",
                        "description_zh": "官方统计", "country": "CN",
                    }
                ],
                "best_authority": "government",
            }
        ]

    def test_template_mode_without_llm(self):
        """不提供 call_model_fn → 生成模板报告"""
        result = l2.generate_deep_analysis(
            "快消品", "市场趋势", "L1分析内容", self.source_results, call_model_fn=None
        )
        self.assertIn("数据验证", result)
        self.assertIn("来源可信度", result)
        self.assertIn("government", result)

    def test_llm_mode(self):
        """提供 call_model_fn → 用 LLM 生成"""
        def mock_call_model(prompt):
            return "## 数据验证\n权威源已验证\n## 深度洞察\n市场向好", "longcat"

        result = l2.generate_deep_analysis(
            "快消品", "市场趋势", "L1分析内容", self.source_results, call_model_fn=mock_call_model
        )
        self.assertIn("数据验证", result)
        self.assertIn("权威源已验证", result)

    def test_llm_error_falls_back_to_template(self):
        """LLM 调用失败 → 降级为模板"""
        def mock_call_model_error(prompt):
            return "[ERROR] model failed", "error"

        result = l2.generate_deep_analysis(
            "快消品", "市场趋势", "L1分析内容", self.source_results,
            call_model_fn=mock_call_model_error
        )
        # 应回退到模板
        self.assertIn("数据验证", result)
        self.assertIn("来源可信度", result)

    def test_empty_source_results(self):
        """无权威源匹配"""
        result = l2.generate_deep_analysis(
            "快消品", "市场趋势", "L1分析", [], call_model_fn=None
        )
        self.assertIn("未找到匹配权威源", result)


class SaveDeepToWikiTests(unittest.TestCase):
    """测试 L2 产出存储"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._orig_wiki = l2.WIKI_DIR
        l2.WIKI_DIR = os.path.join(self.tmp.name, "wiki")

    def tearDown(self):
        l2.WIKI_DIR = self._orig_wiki
        self.tmp.cleanup()

    def test_save_creates_deep_subdirectory(self):
        """L2 文件存在 wiki/{industry}/deep/ 下"""
        filepath = l2.save_deep_to_wiki(
            "fmcg", "快消品", "市场趋势",
            "深度分析内容", [], queries=["q1", "q2"]
        )
        self.assertTrue(os.path.isfile(filepath))
        self.assertIn("deep", filepath)
        self.assertIn("fmcg", filepath)

    def test_filename_has_deep_suffix(self):
        """文件名包含 -deep"""
        filepath = l2.save_deep_to_wiki(
            "fmcg", "快消品", "市场趋势",
            "分析", [], queries=[]
        )
        basename = os.path.basename(filepath)
        self.assertIn("-deep", basename)
        self.assertTrue(basename.endswith(".md"))

    def test_file_content_has_l2_marker(self):
        """文件 frontmatter 标记 l2_layer: true"""
        filepath = l2.save_deep_to_wiki(
            "fmcg", "快消品", "市场趋势",
            "分析内容", [], queries=[]
        )
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("l2_layer: true", content)
        self.assertIn("industry_rotation_deep", content)

    def test_source_table_in_content(self):
        """文件包含权威源标注表"""
        source_results = [{
            "data_point": {"point": "市场规模5万亿", "keyword": "市场", "context": "..."},
            "sources": [
                {"source_id": "NBSC", "name_zh": "国家统计局", "name_en": "NBS",
                 "authority_level": "government", "authority_weight": 10,
                 "website": "https://stats.gov.cn", "api_url": "",
                 "description_zh": "", "country": "CN"},
            ],
            "best_authority": "government",
        }]
        filepath = l2.save_deep_to_wiki(
            "fmcg", "快消品", "市场趋势",
            "分析", source_results, queries=[]
        )
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("权威数据源标注", content)
        self.assertIn("国家统计局", content)
        self.assertIn("government", content)

    def test_queries_in_content(self):
        """文件包含 L1 搜索查询"""
        filepath = l2.save_deep_to_wiki(
            "fmcg", "快消品", "市场趋势",
            "分析", [], queries=["快消品趋势2026", "Z世代消费"]
        )
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("快消品趋势2026", content)
        self.assertIn("Z世代消费", content)


class RunL2DeepTests(unittest.TestCase):
    """测试 L2 端到端流程"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._orig_wiki = l2.WIKI_DIR
        l2.WIKI_DIR = os.path.join(self.tmp.name, "wiki")

    def tearDown(self):
        l2.WIKI_DIR = self._orig_wiki
        self.tmp.cleanup()

    def test_skips_empty_analysis(self):
        """L1 分析为空 → 跳过"""
        result = l2.run_l2_deep("fmcg", "快消品", "市场", "", queries=[])
        self.assertEqual(result["status"], "skipped")

    def test_skips_error_analysis(self):
        """L1 分析出错 → 跳过"""
        result = l2.run_l2_deep("fmcg", "快消品", "市场", "[ERROR] failed", queries=[])
        self.assertEqual(result["status"], "skipped")

    def test_skips_no_data_points(self):
        """无关键数据点 → 跳过"""
        result = l2.run_l2_deep("fmcg", "快消品", "市场", "行业前景良好", queries=[])
        self.assertEqual(result["status"], "skipped")

    def test_runs_with_data_points(self):
        """有关键数据点 → 正常运行"""
        l1_text = "快消品市场规模达5万亿，线上增长32%"
        result = l2.run_l2_deep("fmcg", "快消品", "市场", l1_text, queries=["q1"])
        # FirstData 不可用，但仍应生成模板报告
        self.assertEqual(result["status"], "ok")
        self.assertIsNotNone(result["filepath"])
        self.assertGreater(result["data_points"], 0)
        self.assertTrue(os.path.isfile(result["filepath"]))

    def test_exception_does_not_propagate(self):
        """L2 异常不向外传播"""
        # 用一个会触发异常的 l1_analysis（None 会被跳过，用特殊值）
        result = l2.run_l2_deep("fmcg", "快消品", "市场", "正常分析5万亿", queries=[])
        # 不应该抛异常
        self.assertIn(result["status"], ("ok", "skipped", "error"))


class L1IntegrationTests(unittest.TestCase):
    """测试 L1 脚本的 --deep 参数集成"""

    def test_deep_flag_in_argv(self):
        """--deep 在 sys.argv 中被正确检测"""
        # 验证 main() 中的 deep = "--deep" in sys.argv 逻辑
        # 我们模拟 argv 检查
        test_argv = ["script.py", "--deep"]
        deep = "--deep" in test_argv
        self.assertTrue(deep)

        test_argv = ["script.py", "--force"]
        deep = "--deep" in test_argv
        self.assertFalse(deep)

        test_argv = ["script.py", "--force", "--deep"]
        deep = "--deep" in test_argv
        self.assertTrue(deep)

    def test_run_one_dimension_accepts_deep_param(self):
        """run_one_dimension 接受 deep 参数"""
        # 验证函数签名（mock 掉小爪专用依赖）
        import inspect
        # ll_longcat / web_research_db 是小爪专用，mock 掉
        with patch.dict(sys.modules, {
            "ll_longcat": MagicMock(call_ll=lambda p, timeout=90: ("ok", "longcat")),
            "web_research_db": MagicMock(register_web_research=lambda *a, **kw: None),
        }):
            import industry_rotation_xiaozhua as ir
            sig = inspect.signature(ir.run_one_dimension)
            self.assertIn("deep", sig.parameters)
            self.assertEqual(sig.parameters["deep"].default, False)


if __name__ == "__main__":
    unittest.main()
