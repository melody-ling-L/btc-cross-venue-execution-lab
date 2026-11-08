"""保留本次合成快照的数据粒度、关联与汇总核对过程。"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "exercises"))
from day04_snapshots import run_query


CHECKS = (
    ("明细总行数", "SELECT COUNT(*) FROM positions", [(9,)]),
    (
        "复合键重复",
        "SELECT snapshot_id, account_id, position_id, COUNT(*) FROM positions "
        "GROUP BY snapshot_id, account_id, position_id HAVING COUNT(*) > 1",
        [],
    ),
    (
        "无元数据的孤立明细",
        "SELECT COUNT(*) FROM positions p LEFT JOIN snapshots s "
        "ON p.snapshot_id=s.snapshot_id WHERE s.snapshot_id IS NULL",
        [(0,)],
    ),
    (
        "关联后行数保持",
        "SELECT COUNT(*) FROM positions p JOIN snapshots s ON p.snapshot_id=s.snapshot_id",
        [(9,)],
    ),
    (
        "混合历史版本的总额",
        "SELECT account_id, SUM(ABS(signed_notional_usdt)) FROM positions "
        "GROUP BY account_id ORDER BY account_id",
        [("A", 2900), ("B", 1600)],
    ),
    (
        "已选历史快照99的总额",
        "SELECT account_id, SUM(ABS(signed_notional_usdt)) FROM positions "
        "WHERE snapshot_id=99 GROUP BY account_id ORDER BY account_id",
        [("A", 500), ("B", 800)],
    ),
)


if __name__ == "__main__":
    for label, sql, expected in CHECKS:
        _, rows = run_query(sql)
        assert rows == expected, (label, rows, expected)
        print(f"通过 | {label}: {rows}")
    print("以上仅核对合成数据的教学设定，不证明最新未完成时点的当前风险安全。")
