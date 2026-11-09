-- 合成扩展案例：成交清单已核实完整，无转账等其他资金变动。
-- 期初场所可以没有成交；仅在这一已确认范围内，无匹配变动表示0。
-- 助手提供CTE、LEFT JOIN和COALESCE骨架；学习者正确解释连接范围并填入三个数值字段。
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
    i.initial_usdt + COALESCE(c.cash_change_usdt, 0) AS ending_usdt,
    i.initial_btc + COALESCE(c.btc_change, 0) AS ending_btc,
    COALESCE(c.total_fees_usdt, 0) AS total_fees_usdt
FROM initial_balances AS i
LEFT JOIN changes AS c
    ON i.venue = c.venue;
