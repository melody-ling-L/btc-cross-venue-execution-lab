"""助手整理的 SQLite 内存练习运行器，执行学习者提交的合成持仓查询。"""

from __future__ import annotations

import argparse
from contextlib import closing
from pathlib import Path
import sqlite3


POSITIONS = (
    ("A", 1, 800),
    ("A", 2, -700),
    ("B", 3, 600),
    ("B", 4, -200),
)


def run_query(
    sql: str,
    positions: tuple[tuple[str, int, int], ...] = POSITIONS,
) -> tuple[list[str], list[tuple]]:
    """只连接内存中的教学表；不连接原模拟账本或公司数据库。"""
    with closing(sqlite3.connect(":memory:")) as db:
        db.execute(
            "CREATE TABLE positions ("
            "account_id TEXT NOT NULL, "
            "position_id INTEGER PRIMARY KEY, "
            "signed_notional_usdt INTEGER NOT NULL)"
        )
        db.executemany("INSERT INTO positions VALUES (?, ?, ?)", positions)
        cursor = db.execute(sql)
        if cursor.description is None:
            raise ValueError("本练习需要返回结果的 SELECT 查询")
        columns = [column[0] for column in cursor.description]
        return columns, cursor.fetchall()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sql", type=Path, default=Path(__file__).with_suffix(".sql"))
    args = parser.parse_args()
    columns, rows = run_query(args.sql.read_text(encoding="utf-8"))
    print("合成持仓 | 同一标的 同一快照 | 金额单位USDT")
    print(" | ".join(columns))
    for row in rows:
        print(" | ".join(str(value) for value in row))
    print("net_usdt为带方向净额；gross_usdt为各独立头寸绝对值之和。")


if __name__ == "__main__":
    main()
