"""助手提供的连接代码：合成SQL汇总，再调用学习者完成的风险函数。

SQL由学习者提交；风险函数由学习者按既定规则及语法提示完整重写，
循环中的函数调用也由学习者填对。运行器、连接代码与演示由助手提供。
仅使用SQLite内存库；不连接交易所、公司数据或原模拟资金账本。
"""

from pathlib import Path

from day04_exposure import run_query
from day05_gross_risk import gross_risk_state


# 另一个合成批次：同一产品、时点、估值口径，已核实且无重复的独立头寸。
# 默认题设中A、B都可能还有未收到头寸，完整性不从行数推断。
OBSERVED_POSITIONS = (
    ("A", 1, 500),
    ("A", 2, -400),
    ("B", 3, 300),
    ("B", 4, -200),
)


def risk_report(is_complete: bool = False, limit: int = 800) -> list[tuple[str, int, int, str]]:
    """返回账户、净额、已核实总额及风险状态。

    完整性由题设给出，统一适用于本例A/B两个账户的判断范围。
    True只用于假设数据已齐的对比场景，不表示本函数执行了采集校验。
    输入要求完整性为标准布尔值、限额为有效非负整数USDT。
    结果仅指该时点、该范围的gross，不判断新鲜度或整体风险。
    """
    sql = Path(__file__).with_name("day04_exposure.sql").read_text(encoding="utf-8")
    _columns, rows = run_query(sql, positions=OBSERVED_POSITIONS)
    report = []
    for account_id, net, gross in sorted(rows):
        state = gross_risk_state(gross, is_complete, limit)
        report.append((account_id, net, gross, state))
    return report


if __name__ == "__main__":
    print("合成部分持仓 -> SQLite内存SQL汇总 -> 风险函数")
    print("题设A/B均未采齐 | is_complete=False | 限额800 USDT")
    print("account_id | net_usdt | observed_gross_usdt | risk_state")
    for account_id, net, gross, state in risk_report(False, 800):
        print(f"{account_id} | {net} | {gross} | {state}")
    print("net是净额；observed_gross是已核实独立头寸的绝对金额之和。")
    print("状态仅指该时点、该范围、该项gross，不代表当前整体风险或可以恢复交易。")
