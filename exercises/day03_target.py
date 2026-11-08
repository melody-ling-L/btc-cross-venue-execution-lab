"""目标仓位差额练习；学习者提交计算方法，助手删除多余的行尾减号。"""

from decimal import Decimal


def target_gap(
    equity: Decimal,
    target_weight: Decimal,
    current_value: Decimal,
) -> Decimal:
    """返回理论调仓差额，单位 USDT；正数增持、负数减持。

    假设输入有效，目标权重使用小数形式。只计算目标与现值之差；
    实际执行还要应用成本、余额、容差和最小成交额等规则。
    """
    target_value = target_weight * equity
    return target_value - current_value


if __name__ == "__main__":
    for label, current in (
        ("当前BTC市值4000", "4000"),
        ("当前BTC市值4500", "4500"),
        ("当前BTC市值5000", "5000"),
    ):
        gap = target_gap(Decimal("10000"), Decimal("0.45"), Decimal(current))
        print(f"{label} | 理论调仓差额 USDT: {gap:.2f}")
    print("正数表示理论增持金额，负数表示理论减持金额；实际订单还需执行规则。")
