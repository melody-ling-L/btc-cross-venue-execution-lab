"""完整性与数据年龄练习；助手修正学习者初稿的语法和闭区间边界。"""


def data_usable(
    is_complete: bool,
    age_seconds: int,
    max_age_seconds: int,
) -> bool:
    """只判断快照是否满足本题的完整性和时效条件。

    假设输入已标准化：完整性为布尔值、时间差单位为秒，最大允许年龄非负。
    本题采用统一时钟，负年龄视为时间异常；0与最大允许年龄均包含。
    返回True不表示账户未超限，也不触发恢复交易。
    返回False也不撤销已由其他可信证据确认的风险事实。
    """
    return is_complete and 0 <= age_seconds <= max_age_seconds


if __name__ == "__main__":
    cases = (
        ("完整且新鲜", True, 30),
        ("完整但过期", True, 240),
        ("尚未完整", False, 30),
        ("恰好达到允许年龄", True, 60),
        ("数据年龄为0", True, 0),
        ("数据时间来自未来", True, -1),
    )
    print("完整性与时效合成练习 | 本题给定最大允许年龄60秒")
    for label, complete, age in cases:
        print(f"{label} | complete={complete}, age={age}s | 条件满足: {data_usable(complete, age, 60)}")
    print("此处True只表示两项数据条件满足，不代表敞口未超限。")
