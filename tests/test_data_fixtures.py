from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT / "service"
if str(SERVICE) not in sys.path:
    sys.path.insert(0, str(SERVICE))

from data_insight import DataInsight
from storage import Storage


class DataInsightFixtureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture_dir = ROOT / "benchmark" / "fixtures" / "data"
        self.truth = json.loads((self.fixture_dir / "data_insight_ground_truth.json").read_text(encoding="utf-8"))

    def test_fixture_hashes_and_deterministic_risks_match_ground_truth(self) -> None:
        with tempfile.TemporaryDirectory(prefix="contextflow-fixture-test-") as directory:
            insight = DataInsight(Storage(Path(directory) / "state.db"), artifact_dir=Path(directory) / "charts")
            for name, expected in self.truth["fixtures"].items():
                path = self.fixture_dir / name
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), expected["sha256"])
                dataset_id = path.stem
                profile = insight.profile_file("fixture-test", dataset_id, str(path))
                analysis = insight.analyze("fixture-test", dataset_id)
                self.assertEqual(profile["row_count"], expected["row_count"])
                self.assertEqual(profile["column_count"], expected["column_count"])
                self.assertEqual(analysis["risk_level"], expected["expected_risk_level"])
                observed = {(risk["type"], risk.get("column")) for risk in analysis["risks"]}
                labeled = {(risk["type"], risk.get("column")) for risk in expected["expected_risks"]}
                self.assertEqual(observed, labeled)

    def test_chart_contracts_are_real_and_invalid_schema_fails_safely(self) -> None:
        request = self.truth["chart_request"]
        with tempfile.TemporaryDirectory(prefix="contextflow-chart-fixture-test-") as directory:
            insight = DataInsight(Storage(Path(directory) / "state.db"), artifact_dir=Path(directory) / "charts")
            for name, expected in self.truth["fixtures"].items():
                dataset_id = Path(name).stem
                insight.profile_file("fixture-test", dataset_id, str(self.fixture_dir / name))
                chart_truth = expected["expected_chart"]
                if chart_truth["created"]:
                    chart = insight.chart("fixture-test", dataset_id, request["x_column"], request["y_column"])
                    artifact = Path(chart["artifact_path"])
                    self.assertTrue(artifact.is_file())
                    self.assertGreater(artifact.stat().st_size, 0)
                    self.assertEqual(chart["points"], chart_truth["points"])
                    self.assertIn("<svg", artifact.read_text(encoding="utf-8"))
                else:
                    with self.assertRaisesRegex(ValueError, chart_truth["error"]):
                        insight.chart("fixture-test", dataset_id, request["x_column"], request["y_column"])


if __name__ == "__main__":
    unittest.main()
