-- 第一天下的两所合成余额与已确认成交；无其他资金变动。
-- 卖出分支与两个期末余额表达式由学习者正确补充；其余连接骨架由助手提供。
-- 先按场所汇总变化，再连接每所唯一的期初余额，避免重复计入本金。
WITH changes AS (
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
    GROUP BY venue
)
SELECT
    i.venue,
    i.initial_usdt + c.cash_change_usdt AS ending_usdt,
    i.initial_btc + c.btc_change AS ending_btc,
    c.total_fees_usdt
FROM initial_balances AS i
JOIN changes AS c
    ON i.venue = c.venue;

-- 本题A/B均有已知成交。这里的内连接不是包含零成交场所的通用余额查询。
