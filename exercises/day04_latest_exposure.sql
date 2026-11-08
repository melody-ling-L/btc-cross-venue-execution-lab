-- 选择和聚合片段来自学习者此前提交；本次组合关系由助手示范。
SELECT
    account_id,
    SUM(signed_notional_usdt) AS net_usdt,
    SUM(ABS(signed_notional_usdt)) AS gross_usdt
FROM positions
WHERE snapshot_id = (
    SELECT snapshot_id
    FROM snapshots
    WHERE is_complete = 1
    ORDER BY snapshot_time DESC
    LIMIT 1
)
GROUP BY account_id;
