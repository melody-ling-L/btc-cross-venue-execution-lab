"""检查助手内存参考的事务边界与整组一致性，不连接真实数据库。"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "examples"))
from snapshot_publish_reference import (
    FIXTURE,
    IncompleteSnapshot,
    InjectedBeforeCommit,
    SimulatedResponseLost,
    SnapshotConflict,
    SnapshotStore,
)


class SnapshotPublishTests(unittest.TestCase):
    def setUp(self):
        self.store = SnapshotStore()
        self.addCleanup(self.store.close)

    def assert_original_published(self):
        view = self.store.readback(300)
        self.assertIsNotNone(view)
        self.assertTrue(view.is_complete)
        self.assertEqual(view.records, FIXTURE)
        self.assertEqual(self.store.detail_count(), len(FIXTURE))

    def test_database_is_memory_only_and_foreign_keys_enabled(self):
        databases = self.store._db.execute("PRAGMA database_list").fetchall()
        self.assertEqual([(name, path) for _, name, path in databases], [("main", "")])
        self.assertEqual(self.store._db.execute("PRAGMA foreign_keys").fetchone()[0], 1)

    def test_publish_commits_entire_batch_and_completion_marker(self):
        self.assertEqual(self.store.publish(300, FIXTURE), "published")
        self.assertFalse(self.store._db.in_transaction)
        self.assert_original_published()

    def test_same_batch_retry_ignores_input_order_without_adding_rows(self):
        self.store.publish(300, FIXTURE)
        self.assertEqual(self.store.publish(300, reversed(FIXTURE)), "already_published")
        self.assertEqual(self.store._db.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0], 1)
        self.assert_original_published()

    def test_changed_value_conflicts_and_preserves_original_batch(self):
        self.store.publish(300, FIXTURE)
        changed = (("A", 1, 900),) + FIXTURE[1:]
        with self.assertRaises(SnapshotConflict):
            self.store.publish(300, changed)
        self.assert_original_published()

    def test_missing_record_conflicts_even_when_every_supplied_record_matches(self):
        self.store.publish(300, FIXTURE)
        with self.assertRaises(SnapshotConflict):
            self.store.publish(300, FIXTURE[:-1])
        self.assert_original_published()

    def test_extra_record_conflicts_even_when_all_original_records_match(self):
        self.store.publish(300, FIXTURE)
        with self.assertRaises(SnapshotConflict):
            self.store.publish(300, FIXTURE + (("C", 2, 50),))
        self.assert_original_published()

    def test_existing_incomplete_batch_is_not_resumed_or_reported_success(self):
        for existing_records in (FIXTURE[:1], FIXTURE):
            with self.subTest(existing_records=existing_records), SnapshotStore() as store:
                # 故意预置一个已提交的未完成状态，测试拒绝静默补写的边界。
                store._db.execute("BEGIN")
                store._db.execute("INSERT INTO snapshots VALUES (300, 0)")
                store._db.executemany(
                    "INSERT INTO positions VALUES (?, ?, ?, ?)",
                    ((300, account, position, value) for account, position, value in existing_records),
                )
                store._db.execute("COMMIT")
                before = store.readback(300)
                with self.assertRaises(IncompleteSnapshot):
                    store.publish(300, FIXTURE)
                self.assertEqual(store.readback(300), before)
                self.assertFalse(store.readback(300).is_complete)

    def test_duplicate_input_key_is_rejected_before_any_database_write(self):
        for duplicate_value in (800, 900):
            with self.subTest(value=duplicate_value):
                duplicate = FIXTURE + (("A", 1, duplicate_value),)
                with self.assertRaisesRegex(ValueError, "duplicate input key"):
                    self.store.publish(300, duplicate)
                self.assertIsNone(self.store.readback(300))
                self.assertEqual(self.store.detail_count(), 0)
                self.assertFalse(self.store._db.in_transaction)

    def test_failure_after_marker_update_rolls_back_marker_and_all_details(self):
        with self.assertRaises(InjectedBeforeCommit):
            self.store.publish(300, FIXTURE, fail_before_commit=True)
        self.assertFalse(self.store._db.in_transaction)
        self.assertIsNone(self.store.readback(300))
        self.assertEqual(self.store.detail_count(), 0)
        self.assertEqual(self.store.publish(300, FIXTURE), "published")
        self.assert_original_published()

    def test_committed_response_loss_is_recovered_by_full_readback_and_stable_retry(self):
        with self.assertRaises(SimulatedResponseLost):
            self.store.publish(300, FIXTURE, lose_response_after_commit=True)
        self.assertFalse(self.store._db.in_transaction)
        self.assert_original_published()
        self.assertEqual(self.store.publish(300, FIXTURE), "already_published")
        self.assert_original_published()


if __name__ == "__main__":
    unittest.main()
