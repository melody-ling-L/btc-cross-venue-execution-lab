"""已确认完整成交清单中，零成交场所仍须保留期初资产。

只验证合成 SQLite 内存练习；不将未知、缺采或查询失败当成零成交。
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXERCISES = ROOT / "exercises"
sys.path.insert(0, str(EXERCISES))
sys.path.insert(0, str(ROOT / "examples"))
from day04_trade_cashflows import run_query
from ledger_reference import run_case


class ZeroFillBalancesTests(unittest.TestCase):
    def test_known_idle_venue_keeps_initial_assets_and_traded_venues_match_decimal_reference(self):
        columns, rows = run_query("SELECT * FROM ending_balances ORDER BY venue", include_idle_venue=True)
        self.assertEqual(columns, ["venue", "ending_usdt", "ending_btc", "total_fees_usdt"])
        self.assertEqual(len(rows), 3)
        balances = {row[0]: dict(zip(columns, row)) for row in rows}
        self.assertEqual(set(balances), {"A", "B", "C"})
        idle = balances["C"]
        self.assertEqual((idle["ending_usdt"], idle["ending_btc"], idle["total_fees_usdt"]),
                         (1000.0, 0.0, 0.0))
        reference = run_case("both_filled")
        for venue in ("A", "B"):
            with self.subTest(venue=venue):
                self.assertAlmostEqual(balances[venue]["ending_usdt"], float(reference[venue].usdt), places=8)
                self.assertAlmostEqual(balances[venue]["ending_btc"], float(reference[venue].btc), places=12)
        self.assertAlmostEqual(sum(row["total_fees_usdt"] for row in balances.values()),
                               float(reference["fees_usdt"]), places=8)

    def test_adding_known_idle_cash_changes_equity_and_cash_without_changing_trades_or_fees(self):
        sql = (EXERCISES / "day04_trade_equity.sql").read_text(encoding="utf-8")
        columns, base_rows = run_query(sql)
        extended_columns, extended_rows = run_query(sql, include_idle_venue=True)
        self.assertEqual(extended_columns, columns)
        self.assertEqual(len(base_rows), 1)
        self.assertEqual(len(extended_rows), 1)
        baseline = dict(zip(columns, base_rows[0]))
        extended = dict(zip(columns, extended_rows[0]))
        self.assertAlmostEqual(extended["total_usdt"] - baseline["total_usdt"], 1000.0, places=8)
        self.assertAlmostEqual(extended["total_equity_usdt"] - baseline["total_equity_usdt"], 1000.0, places=8)
        self.assertAlmostEqual(extended["total_btc"], baseline["total_btc"], places=12)
        self.assertAlmostEqual(extended["total_fees_usdt"], baseline["total_fees_usdt"], places=8)


if __name__ == "__main__":
    unittest.main()
