-- 学习者提交的账户聚合后超限查询，助手仅整理排版。
SELECT
    account_id,
    SUM(signed_notional_usdt) AS net_usdt,
    SUM(ABS(signed_notional_usdt)) AS gross_usdt
FROM positions
GROUP BY account_id
HAVING SUM(ABS(signed_notional_usdt)) > 1000;
