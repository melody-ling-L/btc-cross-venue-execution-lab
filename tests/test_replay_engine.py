"""Behavioral checks for causal execution, inventory accounting and data gates."""

import json
import unittest
from copy import deepcopy
from decimal import Decimal as D

from crossvenue.engine import run_replay


def event(venue, now=0, sequence=1, *, bid=None, ask=None, event_ms=None, bids=None, asks=None):
    return {"venue": venue, "symbol": "BTCUSDT", "event_ms": now if event_ms is None else event_ms,
            "recv_ms": now, "sequence": sequence,
            "bids": bids if bids is not None else [[str(bid or (99990 if venue == "alpha" else 100500)), "1"]],
            "asks": asks if asks is not None else [[str(ask or (100000 if venue == "alpha" else 100510)), "1"]],
            "source_kind": "synthetic", "timing_basis": "synthetic_receive"}


def config(**changes):
    result = {"initial_balances": {"alpha": {"usdt": "10000", "btc": "0.1"},
                                   "beta": {"usdt": "10000", "btc": "0.1"}},
              "order_qty": "0.02", "fee_bps": {"alpha": "10", "beta": "10"},
              "latency_ms": 100, "max_quote_age_ms": 1000, "max_event_skew_ms": 1000,
              "mark_start": "100000", "mark_end": "98000"}
    result.update(changes)
    return result


def seed():
    return [event("alpha"), event("beta")]


class ReplayEngineTests(unittest.TestCase):
    def test_result_parameters_describe_actual_overrides_not_the_model_label(self):
        supplied = config(order_qty="0.0125", fee_bps={"alpha": "2.5", "beta": "7"},
                          latency_ms=37, max_quote_age_ms=400, max_event_skew_ms=20,
                          max_unhedged_btc="0.003", min_edge_bps="1", max_pairs=1,
                          depth_mode="top", signal_policy="gross", quality_gates=False,
                          risk_halt=False, require_event_time=False)
        result = run_replay(seed() + [event("alpha", 200, 2)], supplied, model="guarded")
        parameters = result["parameters"]
        expected_keys = {"order_qty", "fee_bps", "latency_ms", "max_quote_age_ms",
                         "max_event_skew_ms", "max_unhedged_btc", "min_edge_bps", "max_pairs",
                         "depth_mode", "signal_policy", "quality_gates", "risk_halt", "require_event_time"}
        self.assertEqual(set(parameters), expected_keys)
        for key in expected_keys - {"fee_bps", "order_qty", "max_unhedged_btc", "min_edge_bps"}:
            self.assertEqual(parameters[key], supplied[key])
        for key in ("order_qty", "max_unhedged_btc", "min_edge_bps"):
            self.assertIsInstance(parameters[key], str)
            self.assertEqual(D(parameters[key]), D(supplied[key]))
        self.assertEqual({v: D(rate) for v, rate in parameters["fee_bps"].items()},
                         {"alpha": D("2.5"), "beta": D("7")})
        self.assertTrue(all(fill["execution_ms"] == 37 for fill in result["fills"]))
        expected_fees = sum(D(fill["notional_usdt"]) * D(parameters["fee_bps"][fill["venue"]]) / 10000
                            for fill in result["fills"])
        self.assertEqual(D(result["metrics"]["fees_usdt"]), expected_fees)

    def test_future_book_changes_do_not_change_earlier_decision_or_input(self):
        original = seed() + [event("beta", 99, 2, bid=100010, ask=100020), event("alpha", 200, 2)]
        changed = deepcopy(original)
        changed[2]["bids"][0][0] = "99500"
        changed[2]["asks"][0][0] = "99510"
        before = deepcopy(original)
        first = run_replay(original, config())
        second = run_replay(changed, config())
        self.assertEqual(first["decisions"][0], second["decisions"][0])
        self.assertEqual(original, before)
        self.assertEqual(first["decisions"][0]["decision_ms"], 0)

    def test_fees_and_terminal_inventory_pnl_reconcile_without_double_charging(self):
        result = run_replay(seed() + [event("alpha", 150, 2, ask=101000)], config())
        metrics = result["metrics"]
        self.assertEqual(metrics["round_trips_complete"], 1)
        self.assertEqual(D(metrics["fees_usdt"]), D("4.01"))
        self.assertEqual(D(metrics["execution_contribution_usdt"]), D("5.99"))
        self.assertEqual(D(metrics["inventory_pnl_usdt"]), D("-400"))
        self.assertEqual(D(metrics["total_pnl_usdt"]), D("-394.01"))
        cash0 = sum(D(a["usdt"]) for a in result["initial_balances"].values())
        cash1 = sum(D(a["usdt"]) for a in result["final_balances"].values())
        btc0 = sum(D(a["btc"]) for a in result["initial_balances"].values())
        btc1 = sum(D(a["btc"]) for a in result["final_balances"].values())
        self.assertEqual(D(metrics["execution_contribution_usdt"]), cash1-cash0+(btc1-btc0)*D("98000"))
        json.dumps(result, allow_nan=False)

    def test_ioc_uses_due_time_book_and_does_not_fill_at_unavailable_old_price(self):
        events = seed() + [event("beta", 99, 2, bid=100010, ask=100020), event("alpha", 200, 2, ask=101000)]
        result = run_replay(events, config())
        first = result["fills"][:2]
        self.assertEqual([f["execution_ms"] for f in first], [100, 100])
        self.assertEqual([f["status"] for f in first], ["filled", "no_fill"])
        self.assertEqual(D(first[1]["qty"]), 0)
        self.assertEqual(result["metrics"]["single_leg_pairs"], 1)
        self.assertEqual(result["metrics"]["risk_halts"], 1)

    def test_partial_sell_creates_signed_residual_and_halts_new_pairs(self):
        events = seed() + [event("beta", 99, 2, bids=[["100500", "0.005"]]),
                           event("alpha", 250, 2)]
        result = run_replay(events, config())
        self.assertEqual(result["fills"][1]["status"], "partial")
        self.assertEqual(D(result["metrics"]["ending_extra_btc"]), D("0.015"))
        self.assertEqual(D(result["metrics"]["max_extra_btc"]), D("0.015"))
        self.assertEqual(result["metrics"]["max_unhedged_duration_ms"], 150)
        self.assertEqual(result["metrics"]["submitted_pairs"], 1)
        self.assertEqual(result["metrics"]["partial_pairs"], 1)

    def test_stale_and_skew_gates_are_distinct_and_sequence_jump_is_not_a_gap_failure(self):
        stale = [event("alpha", 1000, event_ms=0), event("beta", 1000, event_ms=0), event("alpha", 1200, 9)]
        strict = run_replay(stale, config(max_quote_age_ms=250))
        loose = run_replay(stale, config(max_quote_age_ms=250, quality_gates=False))
        self.assertEqual(strict["metrics"]["submitted_pairs"], 0)
        self.assertIn("stale_quote", strict["metrics"]["reject_counts"])
        self.assertGreater(loose["metrics"]["submitted_pairs"], 0)
        skewed = [event("alpha", 1000, event_ms=800), event("beta", 1000, event_ms=999)]
        skew = run_replay(skewed, config(max_event_skew_ms=100))
        self.assertIn("cross_venue_time_skew", skew["metrics"]["reject_counts"])
        jumped = run_replay(seed() + [event("alpha", 200, 99, ask=101000)], config())
        self.assertEqual(jumped["metrics"]["round_trips_complete"], 1)

    def test_consumed_snapshot_depth_cannot_be_reused_when_only_other_venue_refreshes(self):
        events = [event("alpha", asks=[["100000", "0.02"]]), event("beta"),
                  event("beta", 100, 2), event("beta", 200, 3)]
        result = run_replay(events, config(latency_ms=0))
        alpha_buys = sum(D(f["qty"]) for f in result["fills"] if f["venue"] == "alpha" and f["side"] == "buy")
        self.assertEqual(alpha_buys, D("0.02"))
        self.assertEqual(result["metrics"]["submitted_pairs"], 1)

    def test_duplicate_old_and_future_events_never_reset_valid_depth(self):
        events = [event("alpha", asks=[["100000", "0.02"]]), event("beta"),
                  event("alpha", 10, 1), event("alpha", 20, 0),
                  event("alpha", 30, 2, event_ms=31), event("beta", 100, 2)]
        result = run_replay(events, config(latency_ms=0, quality_gates=False))
        reasons = result["metrics"]["reject_counts"]
        for reason in ("duplicate_sequence", "old_sequence", "future_event_timestamp"):
            self.assertIn(reason, reasons)
        self.assertEqual(result["metrics"]["submitted_pairs"], 1)

    def test_missing_event_timestamp_requires_explicit_receive_only_permission(self):
        events = seed()
        events[0]["event_ms"] = None
        strict = run_replay(events, config(latency_ms=0))
        allowed = run_replay(events, config(latency_ms=0, require_event_time=False))
        self.assertEqual(strict["metrics"]["submitted_pairs"], 0)
        self.assertEqual(allowed["metrics"]["round_trips_complete"], 1)
        self.assertTrue(any("receive-only" in note for note in allowed["assumptions"]))

    def test_venue_inventory_and_cash_constraints_hold_even_without_gates(self):
        balances = {"alpha": {"usdt": "10000", "btc": "1"}, "beta": {"usdt": "0", "btc": "0"}}
        result = run_replay(seed(), config(initial_balances=balances, quality_gates=False,
                                           risk_halt=False, latency_ms=0))
        self.assertEqual(result["metrics"]["submitted_pairs"], 0)
        self.assertEqual(result["final_balances"], result["initial_balances"])

    def test_terminal_pending_order_is_not_filled_beyond_data_coverage(self):
        result = run_replay(seed(), config(latency_ms=100))
        self.assertEqual(result["metrics"]["submitted_pairs"], 1)
        self.assertEqual(result["metrics"]["no_fill_pairs"], 0)
        self.assertEqual(result["metrics"]["censored_pairs"], 1)
        self.assertEqual(result["valuation_status"], "incomplete_execution_evidence")
        self.assertTrue(all(f["reason"] == "end_of_data" and f["qty"] is None
                            and f["status"] == "censored" and f["execution_ms"] is None
                            and f["scheduled_execution_ms"] == 100
                            and f["observed_until_ms"] == 0 for f in result["fills"]))
        self.assertEqual(result["final_balances"], result["initial_balances"])

    def test_submitted_order_with_stale_execution_evidence_is_unknown_not_cancelled(self):
        events = seed() + [event("alpha", 150, 2), event("beta", 150, 2), event("alpha", 300, 3)]
        result = run_replay(events, config(max_quote_age_ms=50, risk_halt=False))
        metrics = result["metrics"]
        self.assertEqual(metrics["submitted_pairs"], 1)
        self.assertEqual(metrics["execution_unknown_pairs"], 1)
        self.assertEqual(metrics["no_fill_pairs"], 0)
        self.assertEqual(metrics["censored_pairs"], 0)
        self.assertEqual(result["valuation_status"], "incomplete_execution_evidence")
        self.assertEqual(result["final_balances"], result["initial_balances"])
        self.assertTrue(all(f["status"] == "execution_unknown" and f["qty"] is None
                            and f["notional_usdt"] is None and f["fee_usdt"] is None
                            and f["execution_ms"] is None for f in result["fills"]))
        self.assertIn("unresolved_execution_state", metrics["reject_counts"])

    def test_regressing_event_clock_quarantines_even_when_receive_sequence_increases(self):
        events = [event("alpha", 1000, bid=100000, ask=100010),
                  event("beta", 1000, bid=100000, ask=100010),
                  event("alpha", 1010, 2, event_ms=950, bid=98990, ask=99000),
                  event("beta", 1200, 2, bid=100000, ask=100010)]
        blocked = run_replay(events, config(quality_gates=False))
        self.assertEqual(blocked["metrics"]["submitted_pairs"], 0)
        self.assertIn("event_clock_regression", blocked["metrics"]["reject_counts"])
        self.assertIn("alpha", blocked["quarantined_venues"])
        recovered = run_replay(events + [event("alpha", 1210, 3, event_ms=1100, bid=98990, ask=99000),
                                         event("alpha", 1400, 4, ask=101000)],
                               config(quality_gates=False, max_pairs=1))
        self.assertEqual(recovered["metrics"]["round_trips_complete"], 1)
        self.assertNotIn("alpha", recovered["quarantined_venues"])
        self.assertEqual(recovered["decisions"][-1]["decision_ms"], 1210)

    def test_malformed_new_snapshot_does_not_leave_old_book_tradable_until_recovery(self):
        events = [event("alpha", bid=100000, ask=100010),
                  event("beta", bid=100000, ask=100010),
                  event("alpha", 10, 2, asks="broken"), event("beta", 20, 2)]
        blocked = run_replay(events, config(latency_ms=0, quality_gates=False))
        self.assertEqual(blocked["metrics"]["submitted_pairs"], 0)
        self.assertIn("invalid_depth_snapshot", blocked["metrics"]["reject_counts"])
        self.assertIn("alpha", blocked["quarantined_venues"])
        recovered = run_replay(events + [event("alpha", 30, 3)],
                               config(latency_ms=0, quality_gates=False, max_pairs=1))
        self.assertEqual(recovered["metrics"]["round_trips_complete"], 1)
        self.assertNotIn("alpha", recovered["quarantined_venues"])

    def test_infeasible_profitable_direction_keeps_balance_reasons_despite_feasible_negative_reverse(self):
        balances = {"alpha": {"usdt": "0", "btc": "0.1"},
                    "beta": {"usdt": "10000", "btc": "0"}}
        result = run_replay(seed(), config(initial_balances=balances, latency_ms=0))
        self.assertEqual(result["metrics"]["submitted_pairs"], 0)
        self.assertEqual(result["metrics"]["candidates"], 1)
        for reason in ("insufficient_buy_cash", "insufficient_sell_inventory", "edge_not_above_minimum"):
            self.assertIn(reason, result["metrics"]["reject_counts"])

    def test_gross_screen_still_charges_fees_and_l2_can_reverse_top_price_edge(self):
        fees = [event("alpha"), event("beta", bid=100100, ask=100110)]
        net = run_replay(fees, config(latency_ms=0, signal_policy="net"))
        gross = run_replay(fees, config(latency_ms=0, signal_policy="gross"))
        self.assertEqual(net["metrics"]["submitted_pairs"], 0)
        self.assertLess(D(gross["metrics"]["execution_contribution_usdt"]), 0)
        self.assertGreater(D(gross["metrics"]["fees_usdt"]), 0)
        depth = [event("alpha", asks=[["100000", "0.001"], ["101000", "1"]]), event("beta")]
        guarded = run_replay(depth, config(latency_ms=0))
        optimistic = run_replay(depth, config(latency_ms=0, depth_mode="top"))
        self.assertEqual(guarded["metrics"]["submitted_pairs"], 0)
        self.assertEqual(optimistic["metrics"]["round_trips_complete"], 1)

    def test_pair_budget_caps_submissions_not_candidate_observations(self):
        events = seed() + [event("alpha", 150, 2), event("beta", 200, 2)]
        result = run_replay(events, config(latency_ms=0, max_pairs=1))
        self.assertEqual(result["metrics"]["submitted_pairs"], 1)
        self.assertGreater(result["metrics"]["candidates"], 1)
        self.assertIn("pair_budget", result["metrics"]["reject_counts"])
        self.assertEqual(D(result["metrics"]["max_extra_btc"]), D("0"))

    def test_vendor_receive_source_remains_labelled_and_unknown_event_time_is_explicit(self):
        events = seed()
        for item in events:
            item.update(source_kind="tardis_l5_sample", timing_basis="vendor_receive", event_ms=None)
        strict = run_replay(events, config(latency_ms=0))
        allowed = run_replay(events, config(latency_ms=0, require_event_time=False))
        self.assertEqual(strict["metrics"]["submitted_pairs"], 0)
        self.assertEqual(allowed["source_kind"], "tardis_l5_sample")
        self.assertEqual(allowed["metrics"]["round_trips_complete"], 1)
        self.assertTrue(any("limited observed depth" in note for note in allowed["assumptions"]))


if __name__ == "__main__":
    unittest.main()
