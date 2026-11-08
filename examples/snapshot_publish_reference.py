"""助手提供的单产品合成批次发布参考，只使用 SQLite 内存数据库。

题设已经确认输入来自一个采集完整的批次。本例不实现采集、分页、时间
一致性或新鲜度检查，也不判断账户风险。它只演示全部明细和完成标记的
原子提交、同 ID 整组内容核对，以及两种明确注入的教学故障。

不连接真实数据库、不访问网络；不覆盖生产并发、网络故障或隔离行为。
"""

from __future__ import annotations

from dataclasses import dataclass
import sqlite3
from typing import Iterable


Record = tuple[str, int, int]
FIXTURE: tuple[Record, ...] = (
    ("A", 1, 800),
    ("A", 2, -700),
    ("B", 1, 600),
    ("C", 1, -100),
)


class SnapshotConflict(ValueError):
    """同一已发布批次 ID 对应了不同的整组键或数值。"""


class IncompleteSnapshot(RuntimeError):
    """已有未完成状态，不能静默补写或认定成功。"""


class InjectedBeforeCommit(RuntimeError):
    """教学注入：事务内完成标记已更新，但 COMMIT 尚未执行。"""


class SimulatedResponseLost(RuntimeError):
    """教学注入：COMMIT 已成功，但故意不把成功结果返回给调用方。"""


@dataclass(frozen=True)
class SnapshotView:
    snapshot_id: int
    is_complete: bool
    records: tuple[Record, ...]


def _canonical_records(records: Iterable[Record]) -> tuple[Record, ...]:
    """校验教学输入并排序，不将重复键静默去重。"""
    result: list[Record] = []
    seen: set[tuple[str, int]] = set()
    for row in records:
        if not isinstance(row, (tuple, list)) or len(row) != 3:
            raise ValueError("each record needs account_id, position_id, signed_notional_usdt")
        account_id, position_id, value = row
        if not isinstance(account_id, str) or not account_id.strip():
            raise ValueError("account_id must be nonempty text")
        if type(position_id) is not int or type(value) is not int:
            raise ValueError("position_id and signed_notional_usdt must be integers")
        key = (account_id, position_id)
        if key in seen:
            raise ValueError(f"duplicate input key: {key!r}")
        seen.add(key)
        result.append((account_id, position_id, value))
    return tuple(sorted(result))


class SnapshotStore:
    """单连接内存教材，不接受数据库文件路径或外部连接。"""

    def __init__(self) -> None:
        self._db = sqlite3.connect(":memory:", isolation_level=None)
        self._db.execute("PRAGMA foreign_keys = ON")
        self._db.executescript("""
            CREATE TABLE snapshots (
                snapshot_id INTEGER PRIMARY KEY,
                is_complete INTEGER NOT NULL CHECK (is_complete IN (0, 1))
            );
            CREATE TABLE positions (
                snapshot_id INTEGER NOT NULL REFERENCES snapshots(snapshot_id),
                account_id TEXT NOT NULL,
                position_id INTEGER NOT NULL,
                signed_notional_usdt INTEGER NOT NULL,
                PRIMARY KEY (snapshot_id, account_id, position_id)
            );
        """)

    def __enter__(self) -> SnapshotStore:
        return self

    def __exit__(self, *_args) -> None:
        self.close()

    def close(self) -> None:
        self._db.close()

    def readback(self, snapshot_id: int) -> SnapshotView | None:
        """回读本连接中的整批明细与完成标记。

        None 仅表示本次成功查询未找到该批次；本函数不会据此返回
        “原事务已经回滚”。本教材的单连接也不模拟真实数据库的未决事务。
        """
        batch = self._db.execute(
            "SELECT is_complete FROM snapshots WHERE snapshot_id = ?", (snapshot_id,)
        ).fetchone()
        if batch is None:
            return None
        rows = self._db.execute(
            "SELECT account_id, position_id, signed_notional_usdt FROM positions "
            "WHERE snapshot_id = ? ORDER BY account_id, position_id",
            (snapshot_id,),
        ).fetchall()
        return SnapshotView(snapshot_id, bool(batch[0]), tuple(rows))

    def detail_count(self) -> int:
        """本内存数据库全部明细行数，供演示重复处理是否额外增行。"""
        return self._db.execute("SELECT COUNT(*) FROM positions").fetchone()[0]

    def publish(
        self,
        snapshot_id: int,
        records: Iterable[Record],
        *,
        fail_before_commit: bool = False,
        lose_response_after_commit: bool = False,
    ) -> str:
        """返回 published 或 already_published；冲突和未完成状态明确报错。

        前提：调用方已按本题约定确认输入采集完整。稳定 snapshot_id 指向
        同一批次；本方法比较整个键和值集合，不只检查一条已有明细。
        fail_before_commit 只在首次写入时，于更新完成标记后注入异常。
        lose_response_after_commit 在本例已确认提交后故意丢失返回结果。
        """
        if type(snapshot_id) is not int:
            raise ValueError("snapshot_id must be an integer")
        expected = _canonical_records(records)
        self._db.execute("BEGIN")
        try:
            existing = self.readback(snapshot_id)
            if existing is not None:
                if not existing.is_complete:
                    raise IncompleteSnapshot("已有未完成批次；本参考不自动补写或认定成功。")
                if existing.records != expected:
                    raise SnapshotConflict("同 ID 的整组键或数值不同；保留原已发布内容。")
                status = "already_published"
            else:
                self._db.execute(
                    "INSERT INTO snapshots(snapshot_id, is_complete) VALUES (?, 0)",
                    (snapshot_id,),
                )
                self._db.executemany(
                    "INSERT INTO positions "
                    "(snapshot_id, account_id, position_id, signed_notional_usdt) "
                    "VALUES (?, ?, ?, ?)",
                    ((snapshot_id, account, position, value) for account, position, value in expected),
                )
                self._db.execute(
                    "UPDATE snapshots SET is_complete = 1 WHERE snapshot_id = ?",
                    (snapshot_id,),
                )
                if fail_before_commit:
                    raise InjectedBeforeCommit("教学注入：完成标记已在事务内更新，提交前抛出异常。")
                status = "published"
            self._db.execute("COMMIT")
        except Exception:
            if self._db.in_transaction:
                self._db.execute("ROLLBACK")
            raise
        if lose_response_after_commit:
            raise SimulatedResponseLost("教学注入：本例已经提交成功，故意丢失成功响应。")
        return status


def main() -> None:
    print("助手提供的离线参考 | 合成单产品 A/B/C | 所有数据库均为 :memory:")
    print("前提：题设已确认采集完整；本例不实现真实采集完整性判断。")

    print("\n场景1 首次提交成功 后按同一ID原样重试")
    with SnapshotStore() as store:
        print("首次:", store.publish(300, FIXTURE))
        print("首次明细数:", store.detail_count())
        print("重试:", store.publish(300, FIXTURE))
        print("重试后明细数:", store.detail_count())
        print("整批回读:", store.readback(300))

    print("\n场景2 同一ID把A头寸1从800改成900")
    with SnapshotStore() as store:
        store.publish(300, FIXTURE)
        changed = (("A", 1, 900),) + FIXTURE[1:]
        try:
            store.publish(300, changed)
        except SnapshotConflict as exc:
            print("明确冲突:", exc)
        view = store.readback(300)
        assert view is not None
        print("原A头寸1仍为:", next(value for account, position, value in view.records
                                      if (account, position) == ("A", 1)))
        print("原批次完成标记:", view.is_complete)

    print("\n场景3 更新完成标记后 提交前注入异常")
    with SnapshotStore() as store:
        try:
            store.publish(300, FIXTURE, fail_before_commit=True)
        except InjectedBeforeCommit as exc:
            print("模拟异常:", exc)
        print("本例已明确执行回滚；全部明细数:", store.detail_count())
        print("批次及完成标记回读:", store.readback(300))
        print("这里知道回滚来自明确执行路径，不能把单独的未查到记录当作通用回滚证明。")

    print("\n场景4 提交成功后 教学注入响应丢失")
    with SnapshotStore() as store:
        try:
            store.publish(300, FIXTURE, lose_response_after_commit=True)
        except SimulatedResponseLost:
            print("调用方没有收到成功响应，先回读核实同一批次。")
        print("整批回读:", store.readback(300))
        print("同ID原样重试:", store.publish(300, FIXTURE))
        print("最终明细数:", store.detail_count())

    print("\n这是故障注入教材，不覆盖真实DB网络故障、隔离或并发。")
    print("发布成功不表示账户安全，也不清除已独立核实的风险。")


if __name__ == "__main__":
    main()
