"""学习者经余额富余反例后修正的买入腿资金条件。

助手仅规范排版、移除消息中乘号前的反斜杠，并提供下方演示。
本题输入已合法，手续费以USDT支付；仅检查该腿所需现金。
返回True不表示另一腿库存、行情、精度规则或整体风险均已通过。
"""


def can_buy(cash_usdt, qty_btc, ask, fee_rate):
    return cash_usdt >= qty_btc * ask + qty_btc * ask * fee_rate


if __name__ == "__main__":
    print("合成教学资金检查 | 0.05 BTC、ask100000、USDT费率0.001")
    for cash in (5000, 5005, 6000):
        print(f"可用现金{cash} USDT -> {can_buy(cash, 0.05, 100000, 0.001)}")
