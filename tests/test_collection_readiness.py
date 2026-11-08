"""确认每个发布前条件都有效，未完成任何一项都不能通过。"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "exercises"))
from day05_collection import ready_to_publish


class CollectionReadinessTests(unittest.TestCase):
    def test_all_required_checks_pass_without_unresolved_errors(self):
        self.assertIs(ready_to_publish(True, True, True, False), True)

    def test_missing_required_scope_blocks_readiness(self):
        self.assertIs(ready_to_publish(False, True, True, False), False)

    def test_unfinished_pagination_blocks_readiness(self):
        self.assertIs(ready_to_publish(True, False, True, False), False)

    def test_failed_record_checks_block_readiness(self):
        self.assertIs(ready_to_publish(True, True, False, False), False)

    def test_unresolved_batch_error_blocks_readiness(self):
        self.assertIs(ready_to_publish(True, True, True, True), False)


if __name__ == "__main__":
    unittest.main()
