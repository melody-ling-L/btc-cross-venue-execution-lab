"""Code-fixed guarded-model cost/latency grid; aggregate-only exports."""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN
import json
from pathlib import Path
from typing import Sequence

from .data import model_config
from .engine import run_replay


FEE_BPS_VALUES = (0, 1, 2, 5, 10)
LATENCY_MS_VALUES = (0, 25, 100, 250)
_COUNTS = (
    "candidates", "submitted_pairs", "round_trips_complete", "partial_pairs",
    "single_leg_pairs", "no_fill_pairs", "censored_pairs", "execution_unknown_pairs",
    "risk_halts", "max_unhedged_duration_ms",
)
_PERFORMANCE = (
    "execution_contribution_usdt", "fees_usdt", "max_extra_btc", "ending_extra_btc",
    "total_pnl_usdt",
)
_DECIMAL_SETTINGS = ("fee_bps_per_side", "order_qty_btc", "max_unhedged_btc", "min_edge_bps")
_INTEGER_SETTINGS = ("latency_ms", "max_quote_age_ms", "max_event_skew_ms")
_FIELDS = {
    "schema_version", "model", "source_kind", "valuation_status", "pair_budget",
    *_COUNTS, *_PERFORMANCE, *_DECIMAL_SETTINGS, *_INTEGER_SETTINGS,
}


def _decimal(value: object, label: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, str, Decimal)):
        raise ValueError(f"{label} must be a finite decimal")
    try:
        number = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"{label} must be a finite decimal") from exc
    if not number.is_finite():
        raise ValueError(f"{label} must be a finite decimal")
    return number


def _fee_values(values: Sequence) -> list[str]:
    if not values:
        raise ValueError("fee_bps_values must be nonempty")
    numbers = [_decimal(value, "fee_bps_values") for value in values]
    if any(number < 0 or number >= 10_000 for number in numbers) or len(set(numbers)) != len(numbers):
        raise ValueError("fee_bps_values must be unique and between 0 inclusive and 10000 exclusive")
    return [format(number, "f") for number in numbers]


def _latency_values(values: Sequence) -> list[int]:
    if not values or any(type(value) is not int or value < 0 for value in values) or len(set(values)) != len(values):
        raise ValueError("latency_ms_values must contain unique nonnegative integers")
    return list(values)


def run_sensitivity(
    events: list[dict], overrides: dict, *,
    fee_bps_values: Sequence = FEE_BPS_VALUES,
    latency_ms_values: Sequence = LATENCY_MS_VALUES,
) -> list[dict]:
    """Run guarded on one fixed sample for a code-fixed 5-by-4 default grid.

    Grid keywords allow smaller unit experiments. No best cell is selected, no
    annualization is performed, and incomplete performance is returned as null.
    Every other supplied config setting and the event sample remain unchanged.
    """
    if not isinstance(events, list) or not events or any(not isinstance(event, dict) for event in events):
        raise ValueError("events must be a nonempty list of event dictionaries")
    if not isinstance(overrides, dict):
        raise ValueError("overrides must be a dictionary")
    fees, latencies = _fee_values(fee_bps_values), _latency_values(latency_ms_values)
    baseline = model_config("guarded", deepcopy(overrides))
    if baseline.get("venues") != ["alpha", "beta"] or baseline.get("symbol") != "BTCUSDT":
        raise ValueError("sensitivity requires the fixed alpha/beta BTCUSDT venue scope")
    # Validate even baseline settings subsequently replaced by the grid. Empty
    # replay has no submitted orders and does not inspect or disclose data.
    try:
        run_replay([], baseline, scenario="sensitivity_validation", model="guarded")
    except (ValueError, TypeError, KeyError, InvalidOperation) as exc:
        raise ValueError(f"invalid sensitivity configuration: {exc}") from exc
    rows = []
    for fee in fees:
        for latency in latencies:
            config = deepcopy(baseline)
            config.update(fee_bps={venue: fee for venue in config["venues"]}, latency_ms=latency)
            result = run_replay(events, config, scenario="sensitivity", model="guarded")
            complete = result["valuation_status"] == "complete_within_model"
            row = {
                "schema_version": 1, "model": "guarded", "source_kind": result["source_kind"],
                "valuation_status": result["valuation_status"],
                "fee_bps_per_side": fee, "latency_ms": latency,
                "order_qty_btc": str(config["order_qty"]),
                "max_quote_age_ms": config["max_quote_age_ms"],
                "max_event_skew_ms": config["max_event_skew_ms"],
                "max_unhedged_btc": str(config["max_unhedged_btc"]),
                "min_edge_bps": str(config["min_edge_bps"]), "pair_budget": config.get("max_pairs"),
                **{key: result["metrics"][key] for key in _COUNTS},
                **{key: result["metrics"][key] if complete else None for key in _PERFORMANCE},
            }
            rows.append(row)
    return _validated(rows)


def _validated(rows: list[dict]) -> list[dict]:
    if not isinstance(rows, list) or not rows:
        raise ValueError("sensitivity rows must be a nonempty list")
    seen = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != _FIELDS:
            raise ValueError("unexpected or missing sensitivity fields; raw payloads are not exportable")
        if row["schema_version"] != 1 or row["model"] != "guarded":
            raise ValueError("unsupported sensitivity schema/model")
        if row["source_kind"] not in {"synthetic", "tardis_l5_sample", "mixed"}:
            raise ValueError("unsupported sensitivity source")
        status = row["valuation_status"]
        if status not in {"complete_within_model", "incomplete_execution_evidence"}:
            raise ValueError("unsupported sensitivity valuation status")
        for key in _COUNTS + _INTEGER_SETTINGS:
            if type(row[key]) is not int or row[key] < 0:
                raise ValueError(f"{key} must be a nonnegative integer")
        budget = row["pair_budget"]
        if budget is not None and (type(budget) is not int or budget < 0):
            raise ValueError("pair_budget must be null or a nonnegative integer")
        for key in _DECIMAL_SETTINGS:
            if not isinstance(row[key], str):
                raise ValueError(f"{key} must be a finite decimal string")
            number = _decimal(row[key], key)
            if number < 0 or (key == "order_qty_btc" and number == 0):
                raise ValueError(f"{key} has an invalid sign")
        if Decimal(row["fee_bps_per_side"]) >= 10_000:
            raise ValueError("fee_bps_per_side must be below 10000")
        for key in _PERFORMANCE:
            value = row[key]
            if status == "incomplete_execution_evidence":
                if value is not None:
                    raise ValueError("incomplete performance values must be null")
            elif not isinstance(value, str):
                raise ValueError(f"{key} must be a finite decimal string")
            else:
                number = _decimal(value, key)
                if key in {"fees_usdt", "max_extra_btc"} and number < 0:
                    raise ValueError(f"{key} must be nonnegative")
        cell = (Decimal(row["fee_bps_per_side"]), row["latency_ms"])
        if cell in seen:
            raise ValueError("duplicate fee/latency sensitivity cell")
        seen.add(cell)
    return deepcopy(rows)


def write_sensitivity(rows: list[dict], output_dir: Path) -> dict[str, str]:
    """Write public aggregates; licensed-data cash results are rounded, not raw."""
    rows = _validated(rows)
    for row in rows:
        if row["source_kind"] == "synthetic":
            continue
        # Exact one-pair cash movements could reveal a venue execution price.
        # Do not publish total PnL or other balance-derived financial values.
        row.pop("total_pnl_usdt")
        for field in ("execution_contribution_usdt", "fees_usdt"):
            if row[field] is not None:
                rounded = Decimal(row[field]).quantize(Decimal("1"), rounding=ROUND_HALF_EVEN)
                row[field] = format(abs(rounded) if rounded.is_zero() else rounded, "f")
        for field in ("max_extra_btc", "ending_extra_btc"):
            if row[field] is not None:
                rounded = Decimal(row[field]).quantize(Decimal("0.001"), rounding=ROUND_HALF_EVEN)
                row[field] = format(abs(rounded) if rounded.is_zero() else rounded, "f")
        row["public_export"] = "aggregate_rounded"
        row["public_precision"] = "cash: 1 USDT; extra BTC: 0.001 BTC; ROUND_HALF_EVEN"
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path, md_path = output_dir / "sensitivity.json", output_dir / "sensitivity.md"
    json_path.write_text(json.dumps(rows, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    fields = (
        "fee_bps_per_side", "latency_ms", "order_qty_btc", "submitted_pairs",
        "round_trips_complete", "single_leg_pairs", "censored_pairs",
        "execution_unknown_pairs", "fees_usdt", "execution_contribution_usdt",
        "max_extra_btc", "valuation_status",
    )
    headers = (
        "每侧费率bps", "额外延迟ms", "目标BTC", "提交对数", "双腿完成",
        "单腿", "截尾", "执行未知", "费用USDT", "执行贡献USDT", "额外BTC峰值", "估值状态",
    )
    if any(row["source_kind"] != "synthetic" for row in rows):
        headers = tuple(header + "（约）" if index in (8, 9, 10) else header
                        for index, header in enumerate(headers))
    def cell(value):
        return "Unknown" if value is None else str(value)
    lines = [
        "# 固定样本的费用与延迟敏感性", "",
        f"本次共有{len(rows)}个代码固定的假设组合，均使用guarded策略与同一份事件样本。",
        "默认网格为每侧手续费[0, 1, 2, 5, 10]bps × 额外订单延迟[0, 25, 100, 250]ms；其他配置保持不变。",
        "这些是条件假设，不是测量得到的交易所费率或网络延迟。不同组合可能改变入场集合和成交数量，差异不是严格可加的成本归因。", "",
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
        *("| " + " | ".join(cell(row[field]) for field in fields) + " |" for row in rows),
        "", "完整参数和聚合计数见[sensitivity.json](sensitivity.json)。文件不含逐笔报价、订单明细或原始来源文件路径。", "",
        "执行贡献是相对保持初始库存不动的基准；费用已计入现金，仅扣一次。额外BTC是相对初始总BTC的残余，不是原有库存的总风险。",
        "真实或混合来源公开时，执行贡献和费用粗化到1 USDT，额外BTC粗化到0.001 BTC（ROUND_HALF_EVEN）；不公开总损益、权益或逐所余额。精确账本计算在本地进行，公开舍入值不能用于逐分对账。",
        "标记为‘约’的0表示舍入结果，不能据此认定净贡献或残余敞口精确等于0。",
        "执行未知或截尾时，表现、费用和敞口数值均为null，表内显示Unknown；已观测计数不补足缺失成交。",
        "COMPLETE WITHIN MODEL仅表示该组合在模拟器声明的假设内结算完整，不证明真实成交。", "",
        "本表展示代码固定网格的假设敏感性，不是正式事前注册，也不是样本外验证；不从同一样本挑选盈利参数，不年化，不将月首短窗口外推为长期或实盘收益。",
        "原始和标准化真实行情仍受供应商许可约束，只在本地处理；公开文件仅包含允许的汇总研究结果。", "",
    ]
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return {"sensitivity_json": str(json_path), "sensitivity_md": str(md_path)}
