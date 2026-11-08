"""第二天三个现金流场景和三个 BTC 数量场景的验收。"""

import sys
import unittest
from decimal import Decimal as D
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "exercises"))
from day02_net_pnl import net_btc_change, net_execution_pnl


class NetExecutionPnlTests(unittest.TestCase):
    def test_zero_fees_leave_gross_spread(self):
        result = net_execution_pnl(
            D("0.03"), D("100000"), D("100200"), D("0"), D("0")
        )
        self.assertEqual(result, D("6"))

    def test_positive_spread_can_lose_after_fees(self):
        result = net_execution_pnl(
            D("0.03"), D("100000"), D("100200"), D("0.001"), D("0.001")
        )
        self.assertEqual(result, D("-0.006"))

    def test_each_fee_uses_its_own_leg_notional(self):
        result = net_execution_pnl(
            D("0.03"), D("100000"), D("100200"), D("0.0005"), D("0.001")
        )
        self.assertEqual(result, D("1.494"))


class NetBtcChangeTests(unittest.TestCase):
    def test_equal_gross_fills_leave_btc_fee_inventory_loss(self):
        result = net_btc_change(D("0.03"), D("0.03"), D("0.001"))
        self.assertEqual(result, D("-0.00003"))

    def test_equal_fills_with_zero_btc_fee_leave_inventory_unchanged(self):
        result = net_btc_change(D("0.03"), D("0.03"), D("0"))
        self.assertEqual(result, D("0"))

    def test_selling_net_received_quantity_preserves_inventory(self):
        result = net_btc_change(D("0.03"), D("0.02997"), D("0.001"))
        self.assertEqual(result, D("0"))


if __name__ == "__main__":
    unittest.main()
