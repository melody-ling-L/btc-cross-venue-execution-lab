"""总名义敞口练习：学习者按给定规则和语法提示完成整段函数重写。

此前规则与分支骨架由助手提供；助手规范排版并提供类型提示和演示。

输入前提：同一账户限定范围、同一时点及估值口径，已核实且无重复的
头寸绝对金额之和；金额和限额为有效非负整数USDT，完整性为标准布尔值。
不实现输入校验、采集或新鲜度检查，也不决定恢复交易。
"""


def gross_risk_state(observed_gross: int, is_complete: bool, limit: int) -> str:
    """只判断该时点、该范围、该项gross；未完整仍可由可信下界证明超限。"""
    if observed_gross > limit:
        return "breach"
    if not is_complete:
        return "unknown"
    return "within_limit"


if __name__ == "__main__":
    print("合成风险判断 | 该时点、该范围、该项gross | 限额1000 USDT")
    print("已核实总额USDT  范围完整  判断")
    for gross, complete in (
        (1500, False),
        (1500, True),
        (700, False),
        (700, True),
        (1000, False),
        (1000, True),
    ):
        print(f"{gross:>14}  {str(complete):>8}  {gross_risk_state(gross, complete, 1000)}")
    print("within_limit仅表示该时点该项指标未超限，不表示当前整体安全或可以恢复交易。")
