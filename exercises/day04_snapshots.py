"""助手整理的全量快照教学数据与 SQLite 内存运行器。"""

from __future__ import annotations

import argparse
from contextlib import closing
from pathlib import Path
import sqlite3


# 时间均为北京时间；编号仅作标识，不代表时间先后。
SNAPSHOTS = (
    (101, "2026-11-08 10:00:00", 1),
    (99, "2026-11-08 10:01:00", 1),
    (205, "2026-11-08 10:02:00", 0),
)
POSITIONS = (
    (101, "A", 1, 800),
    (101, "A", 2, -700),
    (101, "B", 3, 600),
    (101, "B", 4, -200),
    (99, "A", 1, 300),
    (99, "A", 2, -200),
    (99, "B", 3, 600),
    (99, "B", 4, -200),
    (205, "A", 1, 900),
)


def run_query(sql: str) -> tuple[list[str], list[tuple]]:
    """每次重新创建合成内存表，不连接任何持久账本。"""
    with closing(sqlite3.connect(":memory:")) as db:
        db.execute("PRAGMA foreign_keys = ON")
        db.execute(
            "CREATE TABLE snapshots ("
            "snapshot_id INTEGER PRIMARY KEY, "
            "snapshot_time TEXT NOT NULL, "
            "is_complete INTEGER NOT NULL CHECK(is_complete IN (0,1)))"
        )
        db.execute(
            "CREATE TABLE positions ("
            "snapshot_id INTEGER NOT NULL REFERENCES snapshots(snapshot_id), "
            "account_id TEXT NOT NULL, position_id INTEGER NOT NULL, "
            "signed_notional_usdt INTEGER NOT NULL, "
            "PRIMARY KEY(snapshot_id, account_id, position_id))"
        )
        db.executemany("INSERT INTO snapshots VALUES (?, ?, ?)", SNAPSHOTS)
        db.executemany("INSERT INTO positions VALUES (?, ?, ?, ?)", POSITIONS)
        cursor = db.execute(sql)
        if cursor.description is None:
            raise ValueError("请提供返回结果的 SELECT 查询")
        return [column[0] for column in cursor.description], cursor.fetchall()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sql", type=Path)
    args = parser.parse_args()
    if args.sql:
        sql = args.sql.read_text(encoding="utf-8")
    else:
        sql = "SELECT snapshot_id, snapshot_time, is_complete FROM snapshots ORDER BY snapshot_time"
    columns, rows = run_query(sql)
    print("全量快照合成练习 | 时间为北京时间")
    print(" | ".join(columns))
    if not rows:
        print("（查询返回0行）")
    for row in rows:
        print(" | ".join(str(value) for value in row))
    print("is_complete=1在本题中约定为全量完整；0表示尚未完成。")
    print("最新完整历史快照的结果，不证明更晚不完整时点的当前风险已知。")


if __name__ == "__main__":
    main()
