-- 学习者根据已给字段及估值公式提交的完整汇总查询；仅调整排版。
-- ending_balances为教学运行器提供的两所期末余额视图。
-- 100000是合成共同估值价格，不代表清算成交价。
-- 费用已经进入ending_usdt，total_fees_usdt仅单独披露，不再扣除。
SELECT
    SUM(ending_usdt) AS total_usdt,
    SUM(ending_btc) AS total_btc,
    SUM(total_fees_usdt) AS total_fees_usdt,
    SUM(ending_usdt) + SUM(ending_btc) * 100000 AS total_equity_usdt
FROM ending_balances;
