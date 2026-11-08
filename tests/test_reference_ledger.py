"""记账与敞口检查；不测试或声称策略盈利。"""

import sys
import unittest
from decimal import Decimal as D
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "examples"))
from ledger_reference import Account, apply_fill, run_case


class LedgerTests(unittest.TestCase):
    def test_both_legs_balance_and_fees(self):
        report = run_case("both_filled")
        self.assertEqual(report["A"], Account(D("4995"), D("0.05")))
        self.assertEqual(report["B"], Account(D("5002.4925"), D("0.05")))
        self.assertEqual(report["fees_usdt"], D("10.0075"))
        self.assertEqual(report["execution_contribution"], D("-2.5075"))
        self.assertEqual(report["extra_btc"], D("0"))
        self.assertEqual(report["total_btc"], D("0.1"))

    def test_buy_only_creates_extra_exposure(self):
        report = run_case("buy_only")
        self.assertEqual(report["ending_equity"], D("19995"))
        self.assertEqual(report["execution_contribution"], D("-5"))
        self.assertEqual(report["extra_btc"], D("0.05"))

    def test_lower_mark_reveals_inventory_risk(self):
        both = run_case("both_filled", D("98000"))
        self.assertEqual(both["ending_equity"], D("19797.4925"))
        self.assertEqual(both["hold_benchmark"], D("19800"))
        self.assertEqual(both["execution_contribution"], D("-2.5075"))
        single = run_case("buy_only", D("98000"))
        self.assertEqual(single["execution_contribution"], D("-105"))

    def test_no_fill_has_no_execution_contribution(self):
        report = run_case("no_fill", D("98000"))
        self.assertEqual(report["ending_equity"], report["hold_benchmark"])
        self.assertEqual(report["fees_usdt"], D("0"))

    def test_fee_must_fit_in_buying_venue_cash(self):
        with self.assertRaises(ValueError):
            apply_fill(Account(D("5000"), D("0")), "buy", D("100000"), D("0.05"), D("0.001"))

    def test_cannot_sell_other_venue_inventory(self):
        with self.assertRaises(ValueError):
            apply_fill(Account(D("10000"), D("0")), "sell", D("100150"), D("0.05"), D("0.001"))

    def test_invalid_inputs_do_not_create_fills(self):
        initial = Account(D("10000"), D("0.1"))
        for bad_qty in [D("0"), D("-1"), D("NaN"), D("Infinity")]:
            with self.subTest(qty=str(bad_qty)), self.assertRaises(ValueError):
                apply_fill(initial, "buy", D("100000"), bad_qty, D("0.001"))
        with self.assertRaises(ValueError):
            apply_fill(initial, "buy", D("100000"), D("0.01"), D("-0.001"))
        self.assertEqual(initial, Account(D("10000"), D("0.1")))


if __name__ == "__main__":
    unittest.main()
