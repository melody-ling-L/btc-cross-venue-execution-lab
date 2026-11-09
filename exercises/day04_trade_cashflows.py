"""助手提供的离线运行器：复用第一天已确认的两笔合成成交。

只连接 SQLite 内存库，供练习 SELECT、CASE 和余额连接。
本题 BTC/USDT、费用以 USDT 支付，成交全集已知且无其他资金变动。
SQLite REAL 用于查询教学；精确资金记账参考 examples/ledger_reference.py。
"""
from __future__ import annotations

import argparse
from contextlib import closing
from pathlib import Path
import sqlite3


FILLS = (
    ("F1", "A", "buy", 0.05, 5000.0, 5.0),
    ("F2", "B", "sell", 0.05, 5007.5, 5.0075),
)
INITIAL_BALANCES = (("A", 10000.0, 0.0), ("B", 0.0, 0.1))


def run_query(sql: str) -> tuple[list[str], list[tuple]]:
    """供已知完整的教学成交汇总；不执行行情采集或真实账户查询。"""
    with closing(sqlite3.connect(":memory:")) as db:
        db.execute(
            "CREATE TABLE fills (fill_id TEXT PRIMARY KEY, venue TEXT NOT NULL, "
            "side TEXT NOT NULL CHECK(side IN ('buy','sell')), "
            "qty_btc REAL NOT NULL, notional_usdt REAL NOT NULL, fee_usdt REAL NOT NULL)"
        )
        db.execute(
            "CREATE TABLE initial_balances (venue TEXT PRIMARY KEY, "
            "initial_usdt REAL NOT NULL, initial_btc REAL NOT NULL)"
        )
        db.executemany("INSERT INTO fills VALUES (?,?,?,?,?,?)", FILLS)
        db.executemany("INSERT INTO initial_balances VALUES (?,?,?)", INITIAL_BALANCES)
        # 将已完成的余额查询提供为临时视图，供下一步总权益查询使用。
        # 仍只存在于本次内存数据库，不写入活跃账本。
        balance_sql = Path(__file__).with_name("day04_trade_balances.sql").read_text(encoding="utf-8")
        db.execute("CREATE TEMP VIEW ending_balances AS " + balance_sql)
        cursor = db.execute(sql)
        if cursor.description is None:
            raise ValueError("本练习需要返回结果的 SELECT 查询")
        return [column[0] for column in cursor.description], cursor.fetchall()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sql", type=Path, default=Path(__file__).with_suffix(".sql"))
    args = parser.parse_args()
    columns, rows = run_query(args.sql.read_text(encoding="utf-8"))
    print("第一天合成成交 | 现金与费用单位USDT，数量单位BTC")
    print(" | ".join(columns))
    for row in rows:
        print(" | ".join(str(value) for value in row))
    print("按列名区分本期变化量与期末余额；已计入现金的费用不重复扣除。")


if __name__ == "__main__":
    main()
