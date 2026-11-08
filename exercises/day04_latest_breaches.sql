-- 学习者参照助手组合示范补充 HAVING；助手规范排版及末尾分号。
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
GROUP BY account_id
HAVING SUM(ABS(signed_notional_usdt)) > 1000;
