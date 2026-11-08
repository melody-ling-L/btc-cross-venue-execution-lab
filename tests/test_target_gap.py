"""用增持、已达标、减持三个状态核对目标差额的方向与金额。"""

import sys
import unittest
from decimal import Decimal as D
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "exercises"))
from day03_target import target_gap


class TargetGapTests(unittest.TestCase):
    def test_underweight_requires_only_missing_value(self):
        self.assertEqual(target_gap(D("10000"), D("0.45"), D("4000")), D("500"))

    def test_already_at_target_requires_no_additional_purchase(self):
        self.assertEqual(target_gap(D("10000"), D("0.45"), D("4500")), D("0"))

    def test_overweight_requires_reducing_existing_holdings(self):
        self.assertEqual(target_gap(D("10000"), D("0.45"), D("5000")), D("-500"))


if __name__ == "__main__":
    unittest.main()
