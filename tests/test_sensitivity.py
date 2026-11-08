"""Economic sensitivity and incomplete-outcome exports, using synthetic data."""

from copy import deepcopy
from decimal import Decimal
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from crossvenue.data import build_scenarios, snapshot
from crossvenue.sensitivity import run_sensitivity, write_sensitivity


class SensitivityTests(unittest.TestCase):
    def test_fee_trap_trades_without_fee_and_rejects_with_two_sided_fee(self):
        events = next(item["events"] for item in build_scenarios() if item["id"] == "fee_trap")
        original = deepcopy(events)
        rows = run_sensitivity(events, {}, fee_bps_values=(0, 10), latency_ms_values=(0,))
        self.assertEqual(rows[0]["submitted_pairs"], 1)
        self.assertEqual(rows[1]["submitted_pairs"], 0)
        self.assertEqual(Decimal(rows[1]["execution_contribution_usdt"]), 0)
        self.assertEqual(events, original)
        default_rows = run_sensitivity(events, {})
        self.assertEqual(len(default_rows), 20)
        self.assertEqual(len({(r["fee_bps_per_side"], r["latency_ms"]) for r in default_rows}), 20)

    def test_unknown_executions_export_null_performance_without_raw_events(self):
        events = [
            snapshot("alpha", 0, 1, [["99", "1"]], [["100", "1"]]),
            snapshot("beta", 0, 1, [["102", "1"]], [["103", "1"]]),
            snapshot("alpha", 50, 2, [["NaN", "1"]], [["100", "1"]]),
            snapshot("beta", 150, 2, [["102", "1"]], [["103", "1"]]),
        ]
        rows = run_sensitivity(events, {"mark_start": "100", "mark_end": "100"},
                               fee_bps_values=(0,), latency_ms_values=(100,))
        row = rows[0]
        self.assertEqual(row["valuation_status"], "incomplete_execution_evidence")
        self.assertEqual(row["execution_unknown_pairs"], 1)
        for field in ("execution_contribution_usdt", "fees_usdt", "max_extra_btc", "ending_extra_btc", "total_pnl_usdt"):
            self.assertIsNone(row[field])
        with tempfile.TemporaryDirectory() as directory:
            paths = write_sensitivity(rows, Path(directory))
            exported = json.loads(Path(paths["sensitivity_json"]).read_text())
            markdown = Path(paths["sensitivity_md"]).read_text()
            self.assertEqual(exported, rows)
            self.assertIn("Unknown", markdown)
            self.assertIn("不年化", markdown)
            self.assertFalse({"bids", "asks", "decisions", "fills", "event_ms", "recv_ms"} & set(exported[0]))

    def test_invalid_grid_or_config_is_rejected(self):
        events = build_scenarios()[0]["events"]
        cases = [
            ({"order_qty": "0"}, {}),
            ({"latency_ms": -1}, {}),
            ({}, {"fee_bps_values": (-1,)}),
            ({}, {"fee_bps_values": ("NaN",)}),
            ({}, {"fee_bps_values": (10000,)}),
            ({}, {"fee_bps_values": (0, "0.0")}),
            ({}, {"latency_ms_values": (-1,)}),
            ({}, {"latency_ms_values": (True,)}),
            ({}, {"latency_ms_values": ()}),
        ]
        for overrides, keywords in cases:
            with self.subTest(overrides=overrides, keywords=keywords), self.assertRaises(ValueError):
                run_sensitivity(events, overrides, **keywords)

    def test_public_writer_rejects_raw_payload_and_nonfinite_performance(self):
        rows = run_sensitivity(build_scenarios()[0]["events"], {},
                               fee_bps_values=(0,), latency_ms_values=(0,))
        with tempfile.TemporaryDirectory() as directory:
            bad = deepcopy(rows)
            bad[0]["bids"] = [["unapproved", "raw payload"]]
            with self.assertRaisesRegex(ValueError, "raw payloads"):
                write_sensitivity(bad, Path(directory))
            self.assertEqual(list(Path(directory).iterdir()), [])
            bad = deepcopy(rows)
            bad[0]["execution_contribution_usdt"] = "NaN"
            with self.assertRaises(ValueError):
                write_sensitivity(bad, Path(directory))

    def test_licensed_data_exports_round_cash_and_exposure_and_omit_total(self):
        rows = run_sensitivity(build_scenarios()[0]["events"], {},
                               fee_bps_values=(0,), latency_ms_values=(0,))
        rows[0].update(source_kind="tardis_l5_sample", execution_contribution_usdt="1.3155000",
                       fees_usdt="83.61832900", max_extra_btc="0.00146", ending_extra_btc="-0.00146",
                       total_pnl_usdt="-24.9675000")
        rows.append({**rows[0], "fee_bps_per_side": "1", "execution_contribution_usdt": "-0.2",
                     "ending_extra_btc": "-0.00046"})
        original = deepcopy(rows)
        with tempfile.TemporaryDirectory() as directory:
            paths = write_sensitivity(rows, Path(directory))
            text = Path(paths["sensitivity_json"]).read_text()
            exported = json.loads(text)
            public = exported[0]
            markdown = Path(paths["sensitivity_md"]).read_text()
        self.assertEqual(rows, original)
        self.assertEqual(public["execution_contribution_usdt"], "1")
        self.assertEqual(public["fees_usdt"], "84")
        self.assertEqual(public["max_extra_btc"], "0.001")
        self.assertEqual(public["ending_extra_btc"], "-0.001")
        self.assertNotIn("total_pnl_usdt", public)
        self.assertNotIn("1.3155000", text)
        self.assertNotIn("83.61832900", text)
        self.assertEqual(exported[1]["execution_contribution_usdt"], "0")
        self.assertEqual(exported[1]["ending_extra_btc"], "0.000")
        self.assertIn("执行贡献USDT（约）", markdown)
        self.assertIn("不是样本外验证", markdown)


if __name__ == "__main__":
    unittest.main()
