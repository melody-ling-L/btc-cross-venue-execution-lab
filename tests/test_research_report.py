"""Report integrity: portable evidence, exact reconciliation and honest sources."""

from html.parser import HTMLParser
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from crossvenue.report import write_report


def sample_run() -> dict:
    return {
        "schema_version": 1, "scenario": "fee_trap", "model": "fees", "source_kind": "synthetic",
        "valuation_status": "complete_within_model",
        "parameters": {"order_qty": "0.05", "fee_bps": {"alpha": "10", "beta": "10"},
                       "latency_ms": 100, "max_quote_age_ms": 250, "max_event_skew_ms": 100,
                       "max_unhedged_btc": "0.005", "min_edge_bps": "0", "max_pairs": 1,
                       "depth_mode": "l2", "signal_policy": "net", "quality_gates": True,
                       "risk_halt": True, "require_event_time": True},
        "assumptions": {"mark_end": "100000", "note": "<script>alert('not executable')</script>"},
        "initial_balances": {"A": {"usdt": "10000", "btc": "0"}, "B": {"usdt": "0", "btc": "0.1"}},
        "final_balances": {"A": {"usdt": "4995", "btc": "0.05"}, "B": {"usdt": "5002.4925", "btc": "0.05"}},
        "metrics": {
            "candidates": 1, "submitted_pairs": 1, "round_trips_complete": 1,
            "partial_pairs": 0, "single_leg_pairs": 0, "no_fill_pairs": 0, "reject_counts": {},
            "censored_pairs": 0, "execution_unknown_pairs": 0,
            "fees_usdt": "10.0075", "initial_equity_usdt": "20000", "ending_equity_usdt": "19997.4925",
            "hold_benchmark_usdt": "20000", "total_pnl_usdt": "-2.5075", "inventory_pnl_usdt": "0",
            "execution_contribution_usdt": "-2.5075", "max_extra_btc": "0.05", "ending_extra_btc": "0",
            "max_unhedged_duration_ms": 100, "risk_halts": 0,
        },
        "decisions": [{"reason": "bid & ask"}], "fills": [{"fee_usdt": "10.0075"}],
        "equity_curve": [{"equity_usdt": "19997.4925"}], "quality_events": [],
    }


class SurfaceParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.scripts = []
        self.external_assets = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "script":
            self.scripts.append(attrs)
        if tag in {"img", "script", "link", "iframe"}:
            if attrs.get("src") or attrs.get("href"):
                self.external_assets.append(attrs)


@unittest.skipUnless(importlib.util.find_spec("matplotlib"), "optional report renderer is not installed")
class ResearchReportTests(unittest.TestCase):
    def test_offline_report_preserves_evidence_and_does_not_deduct_fees_twice(self):
        run = sample_run()
        with tempfile.TemporaryDirectory() as directory:
            paths = write_report([run], Path(directory))
            for path in paths.values():
                self.assertTrue(Path(path).is_file())
            html = Path(paths["report_html"]).read_text(encoding="utf-8")
            parser = SurfaceParser()
            parser.feed(html)
            self.assertEqual(parser.scripts, [])
            self.assertEqual(parser.external_assets, [])
            self.assertIn("<svg", html)
            self.assertIn("&lt;script&gt;", html)
            self.assertIn("All supplied runs reconcile", html)
            self.assertIn("-2.5075", html)
            self.assertIn("10.0075", html)
            self.assertIn("Synthetic Mechanism Experiments", html)
            self.assertEqual(json.loads(Path(paths["results_json"]).read_text()), [run])
            md = Path(paths["report_md"]).read_text(encoding="utf-8")
            self.assertIn("(figures/execution_contribution.svg)", md)
            self.assertTrue((Path(directory) / "figures/execution_contribution.svg").exists())
            self.assertIn("not a second deduction", md)
            # Reordered input keys preserve canonical identity and all source values.
            before = html
            write_report([dict(reversed(list(run.items())))], Path(directory))
            self.assertEqual(Path(paths["report_html"]).read_text(), before)

    def test_real_source_public_export_never_contains_raw_trace_or_arbitrary_metadata(self):
        run = sample_run()
        run["source_kind"] = "snapshot_replay"
        secret = "SECRET_TICK_PRICE"
        for field in ("decisions", "fills", "equity_curve", "quality_events"):
            run[field] = [{"raw": secret, "nested": {"book": secret}}]
        run["assumptions"] = {"latency_ms": 100, "raw_rows": secret, "source_path": secret,
                              "order_qty_btc": secret}
        run["metrics"]["raw_prices"] = secret
        run["metrics"]["reject_counts"] = {secret: 2, "stale_quote": 3}
        run["parameters"]["source_path"] = secret
        run["parameters"]["fee_bps"] = {"alpha": "3.75", "beta": "31.1250"}
        run["parameters"]["latency_ms"] = 137
        run["parameters"]["quality_gates"] = False
        run["initial_balances"]["A"]["account_flow"] = secret
        run["source_metadata"] = {"raw_book": secret}
        with tempfile.TemporaryDirectory() as directory:
            paths = write_report([run], Path(directory))
            for key in ("report_html", "report_md", "results_json", "figure_svg"):
                content = Path(paths[key]).read_text()
                self.assertNotIn(secret, content)
            exported = json.loads(Path(paths["results_json"]).read_text())[0]
            self.assertIn("Rounded aggregate export", exported["assumptions"]["publication"])
            self.assertEqual(exported["parameters"]["fee_bps"], {"alpha": "3.75", "beta": "31.1250"})
            self.assertEqual(exported["parameters"]["latency_ms"], 137)
            self.assertIs(exported["parameters"]["quality_gates"], False)
            self.assertNotIn("source_path", exported["parameters"])
            self.assertEqual(exported["metrics"]["reject_counts"], {"redacted_unrecognized_reason": 2, "stale_quote": 3})
            self.assertNotIn("source_metadata", exported)
            self.assertIn("aggregate_only", Path(paths["results_json"]).read_text())

    def test_missing_nonfinite_and_duplicate_results_fail_without_zero_imputation(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "not-created"
            bad = sample_run()
            del bad["metrics"]["execution_contribution_usdt"]
            with self.assertRaisesRegex(ValueError, "missing metric execution_contribution"):
                write_report([bad], destination)
            self.assertFalse(destination.exists())
            bad = sample_run()
            bad["metrics"]["max_extra_btc"] = "NaN"
            with self.assertRaisesRegex(ValueError, "non-finite"):
                write_report([bad], destination)
            with self.assertRaisesRegex(ValueError, "duplicate scenario/model"):
                write_report([sample_run(), sample_run()], destination)
            with self.assertRaisesRegex(ValueError, "explicit boolean"):
                write_report([sample_run()], destination, public_safe=None)
            bad = sample_run()
            del bad["parameters"]["order_qty"]
            with self.assertRaisesRegex(ValueError, "resolved engine configuration"):
                write_report([bad], destination)
            self.assertFalse(destination.exists())

    def test_failed_accounting_and_replay_sources_are_visible(self):
        run = sample_run()
        run["source_kind"] = "snapshot_replay"
        run["metrics"]["execution_contribution_usdt"] = "7.5"
        run["metrics"]["reject_counts"] = {"stale_quote<unsafe>": 1}
        with tempfile.TemporaryDirectory() as directory:
            paths = write_report([run], Path(directory))
            html = Path(paths["report_html"]).read_text()
            self.assertIn("Snapshot-Replay / Source-Declared Experiments", html)
            self.assertNotIn("Synthetic Mechanism Experiments", html)
            self.assertNotIn("All inputs are synthetic", html)
            self.assertIn("1 exact private known-fill ledger(s) failed", html)
            self.assertNotIn("stale_quote&lt;unsafe&gt;", html)
            self.assertIn("redacted_unrecognized_reason", html)
            self.assertIn("FAIL", html)
            self.assertNotIn("-10.0075", html)
            exported = json.loads(Path(paths["results_json"]).read_text())
            self.assertEqual(exported[0]["metrics"]["execution_contribution_usdt"], "8")
            self.assertEqual(exported[0]["metrics"]["reject_counts"], {"redacted_unrecognized_reason": 1})
            self.assertNotIn("fills", exported[0])
            self.assertEqual(exported[0]["provenance"]["export"], "aggregate_only")

    def test_incomplete_execution_is_unknown_even_when_known_fill_ledger_reconciles(self):
        run = sample_run()
        run["source_kind"] = "snapshot_replay"
        run["valuation_status"] = "incomplete_execution_evidence"
        run["metrics"]["censored_pairs"] = 1
        with tempfile.TemporaryDirectory() as directory:
            paths = write_report([run], Path(directory))
            html = Path(paths["report_html"]).read_text()
            svg = Path(paths["figure_svg"]).read_text()
            exported = json.loads(Path(paths["results_json"]).read_text())[0]
            self.assertIn("INCOMPLETE", html)
            self.assertIn("Unknown (known ledger: ≈-3)", html)
            self.assertIn("Exact ledger checked before export", html)
            self.assertIn("Unknown execution evidence", svg)
            self.assertIn("?", svg)
            self.assertNotIn("run-bar-execution_contribution_usdt-fee_trap-fees", svg)
            self.assertNotIn("run-bar-max_extra_btc-fee_trap-fees", svg)
            self.assertIn("unknown-execution_contribution_usdt-fee_trap-fees", svg)
            self.assertIn("unknown-max_extra_btc-fee_trap-fees", svg)
            self.assertEqual(exported["valuation_status"], "incomplete_execution_evidence")
            self.assertEqual(exported["metrics"]["censored_pairs"], 1)
            self.assertEqual(exported["metrics"]["execution_unknown_pairs"], 0)
            run["valuation_status"] = "complete_within_model"
            with self.assertRaisesRegex(ValueError, "uncertain executions"):
                write_report([run], Path(directory))

    def test_single_pair_public_export_does_not_reveal_exact_leg_prices(self):
        first = sample_run()
        first["source_kind"] = "snapshot_replay"
        second = json.loads(json.dumps(first))
        # Both alternative legs are 10 USDT higher. The exact books/fills and
        # cash differ, while their approved coarse financial exports coincide.
        second["final_balances"]["A"]["usdt"] = "4994.4995"
        second["final_balances"]["B"]["usdt"] = "5002.9920"
        second["metrics"]["fees_usdt"] = "10.0085"
        second["metrics"]["ending_equity_usdt"] = "19997.4915"
        second["metrics"]["total_pnl_usdt"] = "-2.5085"
        second["metrics"]["execution_contribution_usdt"] = "-2.5085"
        second["fills"] = [{"vwap": "100010", "side": "buy"}, {"vwap": "100160", "side": "sell"}]
        with tempfile.TemporaryDirectory() as directory:
            exports = []
            for number, run in enumerate((first, second)):
                paths = write_report([run], Path(directory) / str(number))
                public = json.loads(Path(paths["results_json"]).read_text())[0]
                self.assertNotIn("initial_balances", public)
                self.assertNotIn("final_balances", public)
                self.assertNotIn("initial_equity_usdt", public["metrics"])
                self.assertNotIn("ending_equity_usdt", public["metrics"])
                self.assertNotIn("hold_benchmark_usdt", public["metrics"])
                self.assertNotIn("inventory_pnl_usdt", public["metrics"])
                self.assertTrue(all(check["status"] == "PASS" for check in public["ledger_audit"]["checks"]))
                self.assertTrue(all(set(check) == {"identity", "status"} for check in public["ledger_audit"]["checks"]))
                self.assertEqual(public["publication"]["money_quantum_usdt"], "1")
                self.assertIn("Exact ledger checked before export", Path(paths["report_md"]).read_text())
                exports.append(public["metrics"])
            self.assertEqual(exports[0], exports[1])


if __name__ == "__main__":
    unittest.main()
