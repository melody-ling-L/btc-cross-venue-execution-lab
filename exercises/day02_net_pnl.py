"""第二天现金流与 BTC 数量练习。核心表达式来自学习者提交。"""

from decimal import Decimal


def net_execution_pnl(
    qty: Decimal,
    buy_ask: Decimal,
    sell_bid: Decimal,
    buy_fee_rate: Decimal,
    sell_fee_rate: Decimal,
) -> Decimal:
    """返回同数量买入和卖出、双方以USDT收手续费后的净执行损益。

    假设两边都已足额成交。qty、price为正，fee_rate非负。
    结果不包含延迟、冲击、转账、退出成本或初始库存的涨跌。
    继续练习：解释为什么报价有正价差也会亏钱。
    """
    buy_amount = qty * buy_ask
    buy_fee = buy_amount * buy_fee_rate
    sell_amount = sell_bid * qty
    sell_fee = sell_amount * sell_fee_rate

    return sell_amount - buy_amount - (sell_fee + buy_fee)


def net_btc_change(
    buy_qty: Decimal,
    sell_qty: Decimal,
    buy_fee_rate: Decimal,
) -> Decimal:
    """返回本次交易带来的 BTC 数量变化，增加为正、减少为负。

    仅买入手续费从 BTC 扣除；卖出手续费以 USDT 扣除。
    假设输入有效、买卖数量是扣费前的已确认成交量，且没有其他 BTC 变动。
    不返回账户总持仓或 USDT 损益；负变化不表示账户已经做空。
    """
    return buy_qty - buy_qty * buy_fee_rate - sell_qty


if __name__ == "__main__":
    result = net_execution_pnl(
        Decimal("0.03"),
        Decimal("100000"),
        Decimal("100200"),
        Decimal("0.001"),
        Decimal("0.001"),
    )
    print("场景1 双边费用以USDT扣除 | 净执行损益 USDT:", result)
    btc_change = net_btc_change(Decimal("0.03"), Decimal("0.03"), Decimal("0.001"))
    print("场景2 买入费用以BTC扣除 卖出费用以USDT扣除 | BTC数量变化:", btc_change)
    print("两个场景使用不同费用约定，BTC数量变化不是USDT损益。")
