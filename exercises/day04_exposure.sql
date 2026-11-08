-- 学习者提交的查询，助手仅整理排版。
-- 同一标的 同一快照 每行一个独立头寸，金额已按方向换算为 USDT。
SELECT
    account_id,
    SUM(signed_notional_usdt) AS net_usdt,
    SUM(ABS(signed_notional_usdt)) AS gross_usdt
FROM positions
GROUP BY account_id;
