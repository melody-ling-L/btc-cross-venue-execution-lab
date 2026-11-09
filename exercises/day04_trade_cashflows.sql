-- 合成 BTC/USDT 成交，手续费全部以 USDT 扣除。
-- 查询骨架及买入分支由助手提供；两个卖出分支由学习者正确补充。
-- 输出是本期变化量；费用已经包含在 cash_change_usdt 中。
SELECT
    venue,
    SUM(CASE
        WHEN side = 'buy' THEN -notional_usdt - fee_usdt
        WHEN side = 'sell' THEN notional_usdt - fee_usdt
    END) AS cash_change_usdt,
    SUM(CASE
        WHEN side = 'buy' THEN qty_btc
        WHEN side = 'sell' THEN -qty_btc
    END) AS btc_change,
    SUM(fee_usdt) AS total_fees_usdt
FROM fills
GROUP BY venue;
