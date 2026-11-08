"""核对合成 SQL 聚合与风险分类的衔接，不连接真实账户。"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "exercises"))
from day06_risk_report import risk_report


class RiskReportTests(unittest.TestCase):
    def test_partial_report_uses_gross_instead_of_net_to_keep_confirmed_breach(self):
        self.assertEqual(
            risk_report(False, 800),
            [("A", 100, 900, "breach"), ("B", 100, 500, "unknown")],
        )

    def test_limit_and_completeness_are_forwarded_to_account_risk_classification(self):
        cases = (
            (False, 1000, [("A", 100, 900, "unknown"), ("B", 100, 500, "unknown")]),
            # True 是已齐范围的对比教学假设，不是实际采集完整性的证据。
            (True, 800, [("A", 100, 900, "breach"), ("B", 100, 500, "within_limit")]),
        )
        for complete, limit, expected in cases:
            with self.subTest(is_complete=complete, limit=limit):
                self.assertEqual(risk_report(complete, limit), expected)


if __name__ == "__main__":
    unittest.main()
