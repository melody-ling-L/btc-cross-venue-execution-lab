"""回归核对：不完整状态不能掩盖可信下界已经证明的超限。"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "exercises"))
from day05_gross_risk import gross_risk_state


class GrossRiskTests(unittest.TestCase):
    def test_incomplete_data_does_not_hide_a_confirmed_gross_breach(self):
        for observed in (1500, 1900):
            for complete in (False, True):
                with self.subTest(observed=observed, complete=complete):
                    self.assertEqual(gross_risk_state(observed, complete, 1000), "breach")

    def test_below_limit_requires_complete_scope_to_confirm_within_limit(self):
        self.assertEqual(gross_risk_state(700, False, 1000), "unknown")
        self.assertEqual(gross_risk_state(700, True, 1000), "within_limit")

    def test_exact_limit_is_not_a_breach_but_still_requires_completeness(self):
        self.assertEqual(gross_risk_state(1000, False, 1000), "unknown")
        self.assertEqual(gross_risk_state(1000, True, 1000), "within_limit")


if __name__ == "__main__":
    unittest.main()
