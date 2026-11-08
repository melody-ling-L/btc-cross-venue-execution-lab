"""Historical replays cannot silently inherit synthetic valuation marks."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from crossvenue.cli import run_file
from crossvenue.data import build_scenarios, write_jsonl


class HistoricalConfigTests(unittest.TestCase):
    def _input(self, directory):
        events = build_scenarios()[0]["events"]
        for row in events:
            row["source_kind"] = "tardis_l5_sample"  # Constructed test rows, not vendor records.
        path = directory / "constructed-vendor.jsonl"
        write_jsonl(path, events)
        return path

    def test_empty_or_one_mark_config_cannot_inherit_synthetic_marks(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            path = self._input(directory)
            config = directory / "config.json"
            for value in ({}, {"mark_start": "85000"}, {"mark_end": "86000"}):
                with self.subTest(value=value):
                    config.write_text(json.dumps(value))
                    with self.assertRaisesRegex(ValueError, "mark_start/mark_end"):
                        run_file(path, directory / "report", config, "guarded")
            self.assertFalse((directory / "report").exists())

    def test_supplied_marks_determine_holding_benchmark_and_inventory_pnl(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            path = self._input(directory)
            config = directory / "config.json"
            config.write_text(json.dumps({"mark_start": "85000", "mark_end": "86000"}))
            with patch("crossvenue.cli._save_report"):
                result = run_file(path, directory / "report", config, "guarded")[0]
            self.assertEqual(result["metrics"]["initial_equity_usdt"], "37000.0")
            self.assertEqual(result["metrics"]["hold_benchmark_usdt"], "37200.0")
            self.assertEqual(result["metrics"]["inventory_pnl_usdt"], "200.0")


if __name__ == "__main__":
    unittest.main()
