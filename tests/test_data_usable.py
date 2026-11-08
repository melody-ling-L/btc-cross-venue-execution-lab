"""完整性与时效条件的闭区间及不可用情境检查。"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "exercises"))
from day04_data_quality import data_usable


class DataUsableTests(unittest.TestCase):
    def test_complete_recent_snapshot_meets_conditions(self):
        self.assertIs(data_usable(True, 30, 60), True)

    def test_incomplete_snapshot_does_not_meet_conditions(self):
        self.assertIs(data_usable(False, 30, 60), False)

    def test_complete_but_stale_snapshot_does_not_meet_conditions(self):
        for age, maximum in ((240, 60), (11, 10)):
            with self.subTest(age=age, maximum=maximum):
                self.assertIs(data_usable(True, age, maximum), False)

    def test_zero_age_is_included(self):
        self.assertIs(data_usable(True, 0, 60), True)

    def test_exact_maximum_age_is_included(self):
        self.assertIs(data_usable(True, 60, 60), True)

    def test_negative_age_is_rejected_in_this_clock_model(self):
        self.assertIs(data_usable(True, -1, 60), False)


if __name__ == "__main__":
    unittest.main()
