"""最新时间不完整、编号非时间顺序时，仍选择正确的完整历史快照。"""

import sys
import unittest
from pathlib import Path

EXERCISES = Path(__file__).resolve().parents[1] / "exercises"
sys.path.insert(0, str(EXERCISES))
from day04_snapshots import run_query


class SnapshotSelectionTests(unittest.TestCase):
    def test_selects_latest_complete_by_time_instead_of_maximum_id(self):
        columns, rows = run_query(
            (EXERCISES / "day04_latest_snapshot.sql").read_text(encoding="utf-8")
        )
        self.assertEqual(columns, ["snapshot_id"])
        self.assertEqual(rows, [(99,)])

    def test_account_totals_use_all_positions_from_one_complete_snapshot(self):
        columns, rows = run_query(
            (EXERCISES / "day04_latest_exposure.sql").read_text(encoding="utf-8")
        )
        self.assertEqual(columns, ["account_id", "net_usdt", "gross_usdt"])
        self.assertEqual(set(rows), {("A", 100, 500), ("B", 400, 800)})

    def test_no_breaches_in_selected_complete_historical_snapshot(self):
        columns, rows = run_query(
            (EXERCISES / "day04_latest_breaches.sql").read_text(encoding="utf-8")
        )
        self.assertEqual(columns, ["account_id", "net_usdt", "gross_usdt"])
        self.assertEqual(rows, [])


if __name__ == "__main__":
    unittest.main()
