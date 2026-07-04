import importlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


class Phase1Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_track_b_job_creation_writes_expected_files(self):
        brief_router = importlib.import_module("brief_router")
        payload = {
            "project_id": "XXX品牌Q3策略",
            "direction": "某品牌抗初老品类策略",
            "objective": ["竞品梳理", "搜索行为", "KOL趋势"],
            "keywords": ["抗初老", "早C晚A", "敏感肌"],
            "constraints": {"scope": "近3个月", "max_sources": 50, "deadline": "2026-07-06"},
            "output_format": "project_report",
            "created_by": "feishu",
        }

        job_dir = Path(brief_router.create_job(payload, track="B", root_dir=self.root))

        self.assertTrue((job_dir / "job_spec.json").exists())
        self.assertTrue((job_dir / "direction.json").exists())
        self.assertTrue((job_dir / "status.json").exists())

        spec = json.loads((job_dir / "job_spec.json").read_text(encoding="utf-8"))
        direction = json.loads((job_dir / "direction.json").read_text(encoding="utf-8"))
        status = json.loads((job_dir / "status.json").read_text(encoding="utf-8"))

        self.assertEqual(spec["track"], "B")
        self.assertEqual(spec["project_id"], "XXX品牌Q3策略")
        self.assertEqual(spec["keywords"], ["抗初老", "早C晚A", "敏感肌"])
        self.assertEqual(status["state"], "pending")
        self.assertEqual(status["retry_count"], 0)
        self.assertEqual(direction["scope"], "近3个月")
        self.assertEqual(direction["max_results"], 50)
        self.assertGreaterEqual(len(direction["directions"]), 3)

    def test_dedup_rejects_matching_pending_job(self):
        brief_router = importlib.import_module("brief_router")
        payload = {
            "project_id": "重复项目",
            "direction": "重复方向",
            "objective": ["竞品梳理"],
            "keywords": ["抗初老", "早C晚A"],
        }

        brief_router.create_job(payload, track="B", root_dir=self.root)

        with self.assertRaises(ValueError):
            brief_router.create_job(payload, track="B", root_dir=self.root)

    def test_jsonish_brief_cli_shape_is_parsed(self):
        brief_router = importlib.import_module("brief_router")

        parsed = brief_router.parse_jsonish(
            "{project_id:XXX品牌Q3策略,direction:某品牌抗初老品类策略,"
            "objective:[竞品梳理,搜索行为],keywords:[抗初老,早C晚A],"
            "constraints:{scope:近3个月,max_sources:50}}"
        )

        self.assertEqual(parsed["project_id"], "XXX品牌Q3策略")
        self.assertEqual(parsed["objective"], ["竞品梳理", "搜索行为"])
        self.assertEqual(parsed["constraints"]["max_sources"], 50)

    def test_watcher_processes_pending_job_to_done(self):
        brief_router = importlib.import_module("brief_router")
        task_watcher = importlib.import_module("task_watcher")
        payload = {
            "project_id": "Watcher测试",
            "direction": "Watcher方向",
            "objective": ["竞品梳理"],
            "keywords": ["抗初老"],
        }
        job_dir = Path(brief_router.create_job(payload, track="B", root_dir=self.root))

        result = task_watcher.run_once(root_dir=self.root, router_path=str(SRC / "brief_router.py"))

        self.assertEqual(result, "processed")
        status = json.loads((job_dir / "status.json").read_text(encoding="utf-8"))
        self.assertEqual(status["state"], "done")
        self.assertIsNotNone(status["started_at"])
        self.assertIsNotNone(status["completed_at"])
        self.assertIsNone(status["error"])


if __name__ == "__main__":
    unittest.main()
