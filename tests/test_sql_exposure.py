"""验证按账户分组，并保留多空抵消前的总名义敞口。"""

import sys
import unittest
from pathlib import Path

EXERCISES = Path(__file__).resolve().parents[1] / "exercises"
sys.path.insert(0, str(EXERCISES))
from day04_exposure import POSITIONS, run_query


class ExposureSqlTests(unittest.TestCase):
    def test_signed_net_and_gross_are_distinct_per_account(self):
        columns, rows = run_query((EXERCISES / "day04_exposure.sql").read_text(encoding="utf-8"))
        self.assertEqual(columns, ["account_id", "net_usdt", "gross_usdt"])
        self.assertEqual(set(rows), {("A", 100, 1500), ("B", 400, 800)})

    def test_gross_breach_is_grouped_and_strictly_above_limit(self):
        positions = POSITIONS + (("C", 5, 500), ("C", 6, -500))
        _, rows = run_query(
            (EXERCISES / "day04_breaches.sql").read_text(encoding="utf-8"), positions
        )
        self.assertEqual(set(rows), {("A", 100, 1500)})


if __name__ == "__main__":
    unittest.main()
