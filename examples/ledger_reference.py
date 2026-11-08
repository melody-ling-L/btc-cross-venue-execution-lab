"""离线合成案例的记账参考，不连接交易所，不判断真实订单状态。"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from decimal import Decimal


D = Decimal


@dataclass(frozen=True)
class Account:
    usdt: Decimal
    btc: Decimal


@dataclass(frozen=True)
class FillResult:
    account: Account
    fee_usdt: Decimal


def apply_fill(
    account: Account,
    side: str,
    price: Decimal,
    qty: Decimal,
    fee_rate: Decimal,
) -> FillResult:
    """记入一笔已确认成交；现货不允许负余额，手续费以USDT扣除。"""
    if side not in {"buy", "sell"}:
        raise ValueError("side must be buy or sell")
    values = (account.usdt, account.btc, price, qty, fee_rate)
    if not all(value.is_finite() for value in values):
        raise ValueError("all inputs must be finite Decimal values")
    if account.usdt < 0 or account.btc < 0 or price <= 0 or qty <= 0 or fee_rate < 0:
        raise ValueError("invalid balance, price, quantity or fee rate")
    notional = price * qty
    fee = notional * fee_rate
    if side == "buy":
        updated = Account(account.usdt - notional - fee, account.btc + qty)
    else:
        updated = Account(account.usdt + notional - fee, account.btc - qty)
    if updated.usdt < 0 or updated.btc < 0:
        raise ValueError("insufficient venue balance; spot borrowing is not modeled")
    return FillResult(updated, fee)


def equity(accounts: tuple[Account, ...], mark: Decimal) -> Decimal:
    if not mark.is_finite() or mark <= 0:
        raise ValueError("mark must be finite and positive")
    return sum((a.usdt + a.btc * mark for a in accounts), D("0"))


def run_case(scenario: str, mark: Decimal = D("100000")) -> dict:
    if scenario not in {"both_filled", "buy_only", "sell_only", "no_fill"}:
        raise ValueError("unknown scenario")
    initial = (Account(D("10000"), D("0")), Account(D("0"), D("0.1")))
    a, b = initial
    total_fees = D("0")
    if scenario in {"both_filled", "buy_only"}:
        result = apply_fill(a, "buy", D("100000"), D("0.05"), D("0.001"))
        a = result.account
        total_fees += result.fee_usdt
    if scenario in {"both_filled", "sell_only"}:
        result = apply_fill(b, "sell", D("100150"), D("0.05"), D("0.001"))
        b = result.account
        total_fees += result.fee_usdt
    after = (a, b)
    return {
        "A": a,
        "B": b,
        "mark": mark,
        "total_usdt": a.usdt + b.usdt,
        "total_btc": a.btc + b.btc,
        "fees_usdt": total_fees,
        "initial_equity": equity(initial, D("100000")),
        "ending_equity": equity(after, mark),
        "hold_benchmark": equity(initial, mark),
        "execution_contribution": equity(after, mark) - equity(initial, mark),
        "extra_btc": a.btc + b.btc - D("0.1"),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scenario",
        choices=["both_filled", "buy_only", "sell_only", "no_fill"],
        default="both_filled",
    )
    parser.add_argument("--mark", default="100000", help="共同期末估值，默认100000")
    args = parser.parse_args()
    try:
        report = run_case(args.scenario, D(args.mark))
    except (ValueError, ArithmeticError) as exc:
        parser.error(str(exc))
    print("合成教学数据 | 假定成交状态:", args.scenario)
    print("场所     USDT              BTC")
    for venue in ("A", "B"):
        account = report[venue]
        print(f"{venue:<6} {account.usdt:>14.4f}    {account.btc:.8f}")
    for label, key in (
        ("手续费 USDT", "fees_usdt"),
        ("期末总资产 USDT", "ending_equity"),
        ("保持初始库存的期末权益 USDT", "hold_benchmark"),
        ("相对持有基准的执行贡献 USDT", "execution_contribution"),
        ("相对初始权益的损益 USDT", None),
        ("新增 BTC 敞口", "extra_btc"),
    ):
        value = report[key] if key else report["ending_equity"] - report["initial_equity"]
        print(f"{label}: {value:.4f}")
    print("共同估值并非清算成交价；尚未计入退出成本。")


if __name__ == "__main__":
    main()
