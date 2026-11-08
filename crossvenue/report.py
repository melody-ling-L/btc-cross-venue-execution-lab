"""Offline research reporting; matplotlib is an optional, report-only dependency.

Input is the simulator's result contract. Missing measurements are rejected rather
than silently converted to zero. Report arithmetic uses Decimal, and fees already
recorded in ending cash are never deducted again.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext
from hashlib import sha256
from html import escape
import json
from pathlib import Path
import re


MODELS = ("screen", "fees", "depth", "latency", "guarded")
SCENARIOS = ("fee_trap", "depth_trap", "latency_race", "partial_leg", "stale_quote", "venue_inventory")
SCENARIO_LABELS = {
    "fee_trap": "Fee trap", "depth_trap": "Depth trap", "latency_race": "Latency race",
    "partial_leg": "Partial leg", "stale_quote": "Stale quote", "venue_inventory": "Venue inventory",
}
COUNT_METRICS = (
    "candidates", "submitted_pairs", "round_trips_complete", "partial_pairs",
    "single_leg_pairs", "no_fill_pairs", "censored_pairs", "execution_unknown_pairs",
    "max_unhedged_duration_ms", "risk_halts",
)
DECIMAL_METRICS = (
    "fees_usdt", "initial_equity_usdt", "ending_equity_usdt", "hold_benchmark_usdt",
    "total_pnl_usdt", "inventory_pnl_usdt", "execution_contribution_usdt",
    "max_extra_btc", "ending_extra_btc",
)
TOP_FIELDS = (
    "schema_version", "scenario", "model", "source_kind", "assumptions",
    "initial_balances", "final_balances", "metrics", "decisions", "fills",
    "equity_curve", "quality_events", "valuation_status", "parameters",
)
PARAMETER_KEYS = (
    "order_qty", "fee_bps", "latency_ms", "max_quote_age_ms", "max_event_skew_ms",
    "max_unhedged_btc", "min_edge_bps", "max_pairs", "depth_mode", "signal_policy",
    "quality_gates", "risk_halt", "require_event_time",
)
PUBLIC_REJECTION_REASONS = {
    "invalid_receive_timestamp", "unsupported_venue_or_symbol", "unsupported_source_or_timing_basis",
    "duplicate_sequence", "old_sequence", "future_event_timestamp", "event_clock_regression",
    "event_clock_recovery_unverified", "invalid_depth_snapshot", "invalid_depth_level",
    "unordered_or_duplicate_price_levels", "crossed_book", "missing_venue", "quarantined_venue",
    "unknown_event_timestamp", "stale_quote", "cross_venue_time_skew", "unresolved_execution_state",
    "risk_halted", "pair_budget", "insufficient_known_depth", "insufficient_buy_cash",
    "insufficient_sell_inventory", "edge_not_above_minimum",
    "sequence must be a nonnegative integer", "event_ms must be a nonnegative integer",
    "price must be a finite number", "price must be finite and positive",
    "quantity must be a finite number", "quantity must be finite and nonnegative",
}
PUBLIC_REJECTION_REASONS |= {"execution_" + reason for reason in (
    "missing_venue", "quarantined_venue", "unknown_event_timestamp", "stale_quote", "cross_venue_time_skew"
)}


def _parameters(result: dict) -> dict:
    """Validate actual resolved engine knobs, never infer them from model defaults."""
    parameters = result["parameters"]
    if not isinstance(parameters, dict) or any(key not in parameters for key in PARAMETER_KEYS):
        raise ValueError("parameters must contain the complete resolved engine configuration")
    for name in ("order_qty", "max_unhedged_btc", "min_edge_bps"):
        value = _decimal(parameters[name], "parameters." + name)
        if value < 0 or (name == "order_qty" and value == 0):
            raise ValueError(f"parameters.{name}: invalid nonnegative configuration")
    fees = parameters["fee_bps"]
    if not isinstance(fees, dict) or set(fees) != {"alpha", "beta"}:
        raise ValueError("parameters.fee_bps must contain exactly alpha and beta")
    for venue, value in fees.items():
        rate = _decimal(value, "parameters.fee_bps." + venue)
        if rate < 0 or rate >= 10000:
            raise ValueError("parameters.fee_bps must be within [0, 10000)")
    for name in ("latency_ms", "max_quote_age_ms", "max_event_skew_ms"):
        if type(parameters[name]) is not int or parameters[name] < 0:
            raise ValueError(f"parameters.{name}: expected a nonnegative integer")
    if parameters["max_pairs"] is not None and (type(parameters["max_pairs"]) is not int or parameters["max_pairs"] < 0):
        raise ValueError("parameters.max_pairs: expected a nonnegative integer or null")
    for name in ("quality_gates", "risk_halt", "require_event_time"):
        if type(parameters[name]) is not bool:
            raise ValueError(f"parameters.{name}: expected a boolean")
    if parameters["depth_mode"] not in {"l2", "top"} or parameters["signal_policy"] not in {"gross", "net"}:
        raise ValueError("parameters: unsupported depth_mode or signal_policy")
    return {key: parameters[key] for key in PARAMETER_KEYS}


def _public_rejections(counts: dict) -> dict:
    """Reason identifiers are a fixed vocabulary, not arbitrary raw error payloads."""
    safe = {}
    for reason, count in counts.items():
        name = reason if reason in PUBLIC_REJECTION_REASONS else "redacted_unrecognized_reason"
        safe[name] = safe.get(name, 0) + count
    return safe


def _scenario_order(scenario: str) -> tuple[int, str]:
    return (SCENARIOS.index(scenario), "") if scenario in SCENARIOS else (len(SCENARIOS), scenario)


def _public_runs(results: list[dict], public_safe: bool) -> list[dict]:
    """Allowlist non-synthetic exports; never copy arbitrary nested trace metadata."""
    if not public_safe:
        return results
    exported = []
    for result in results:
        if result["source_kind"] == "synthetic":
            exported.append(result)
            continue
        raw = json.dumps(result, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
        def rounded(name, quantum):
            return format(Decimal(result["metrics"][name]).quantize(Decimal(quantum), rounding=ROUND_HALF_UP), "f")
        metrics = {key: result["metrics"][key] for key in COUNT_METRICS if key != "max_unhedged_duration_ms"}
        metrics.update({name: rounded(name, "1") for name in ("execution_contribution_usdt", "fees_usdt")})
        metrics.update({name: rounded(name, "0.001") for name in ("max_extra_btc", "ending_extra_btc")})
        metrics["reject_counts"] = _public_rejections(result["metrics"]["reject_counts"])
        audit = [{"identity": name, "status": "PASS" if residual == 0 else "FAIL"}
                 for name, residual in _checks(result)]
        exported.append({
            "schema_version": result["schema_version"], "scenario": result["scenario"],
            "model": result["model"], "source_kind": result["source_kind"],
            "valuation_status": result["valuation_status"],
            "parameters": _parameters(result),
            "assumptions": {
                "configuration": "The parameters field contains validated resolved engine settings, not inferred defaults.",
                "scope": "Historical displayed depth and simulated IOC fills; no live orders or guaranteed liquidity.",
                "valuation": "Common marks are not liquidation prices; fees are already included in cash once.",
                "publication": "Rounded aggregate export: money to 1 USDT and extra BTC to 0.001 BTC. Exact ledger checked before export.",
            },
            "metrics": metrics,
            "ledger_audit": {"scope": "Exact known-fill ledger checked before export; no exact values or residuals published.",
                             "checks": audit},
            "publication": {"mode": "rounded_aggregate_only", "money_quantum_usdt": "1",
                            "btc_quantum": "0.001", "rounding": "ROUND_HALF_UP"},
            "provenance": {"input_sha256": sha256(raw.encode("utf-8")).hexdigest(), "export": "aggregate_only",
                           "note": "Raw market data, traces, per-venue balances, equity, marks, exact financial aggregates and unapproved metadata are excluded."},
        })
    return exported


def _decimal(value: object, label: str) -> Decimal:
    if not isinstance(value, str):
        raise ValueError(f"{label}: expected a finite decimal string, got {value!r}")
    try:
        number = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"{label}: invalid decimal string") from exc
    if not number.is_finite():
        raise ValueError(f"{label}: non-finite value")
    return number


def _validate(results: list[dict]) -> list[dict]:
    if not isinstance(results, list) or not results:
        raise ValueError("results must be a nonempty list of run dictionaries")
    seen = set()
    for index, result in enumerate(results):
        if not isinstance(result, dict):
            raise ValueError(f"result {index}: expected a dictionary")
        missing = [key for key in TOP_FIELDS if key not in result]
        if missing:
            raise ValueError(f"result {index}: missing fields {', '.join(missing)}")
        if type(result["schema_version"]) is not int or result["schema_version"] != 1:
            raise ValueError(f"result {index}: unsupported schema_version")
        key = (result["scenario"], result["model"])
        if not isinstance(key[0], str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,96}", key[0]) or key[1] not in MODELS:
            raise ValueError(f"result {index}: unsupported scenario/model {key!r}")
        if key in seen:
            raise ValueError(f"duplicate scenario/model result: {key!r}")
        seen.add(key)
        if not isinstance(result["source_kind"], str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", result["source_kind"]):
            raise ValueError(f"{key}: missing source_kind")
        if result["valuation_status"] not in {"complete_within_model", "incomplete_execution_evidence"}:
            raise ValueError(f"{key}: missing/invalid valuation_status")
        metrics = result["metrics"]
        if not isinstance(metrics, dict):
            raise ValueError(f"{key}: metrics must be a dictionary")
        for name in COUNT_METRICS:
            if name not in metrics or type(metrics[name]) is not int or metrics[name] < 0:
                raise ValueError(f"{key}: missing/invalid count metric {name}")
        uncertain = metrics["censored_pairs"] > 0 or metrics["execution_unknown_pairs"] > 0
        if uncertain and result["valuation_status"] != "incomplete_execution_evidence":
            raise ValueError(f"{key}: uncertain executions cannot have complete valuation_status")
        _parameters(result)
        for name in DECIMAL_METRICS:
            if name not in metrics:
                raise ValueError(f"{key}: missing metric {name}")
            number = _decimal(metrics[name], f"{key}.{name}")
            if name in {"fees_usdt", "max_extra_btc"} and number < 0:
                raise ValueError(f"{key}.{name}: must be nonnegative")
        reasons = metrics.get("reject_counts")
        if not isinstance(reasons, dict) or any(
            not isinstance(reason, str) or type(count) is not int or count < 0
            for reason, count in reasons.items()
        ):
            raise ValueError(f"{key}: missing/invalid reject_counts")
        for field in ("initial_balances", "final_balances"):
            balances = result[field]
            if not isinstance(balances, dict) or not balances:
                raise ValueError(f"{key}: {field} must map venues to balances")
            for venue, balance in balances.items():
                if not isinstance(venue, str) or not isinstance(balance, dict):
                    raise ValueError(f"{key}: invalid {field} entry")
                for asset in ("usdt", "btc"):
                    if asset not in balance:
                        raise ValueError(f"{key}: missing {field}.{venue}.{asset}")
                    if _decimal(balance[asset], f"{field}.{venue}.{asset}") < 0:
                        raise ValueError(f"{key}: negative spot balance in {field}")
        if set(result["initial_balances"]) != set(result["final_balances"]):
            raise ValueError(f"{key}: initial/final venue scopes differ")
        for field in ("decisions", "fills", "equity_curve", "quality_events"):
            if not isinstance(result[field], list):
                raise ValueError(f"{key}: {field} must be a list")
    try:
        json.dumps(results, allow_nan=False)
    except (ValueError, TypeError) as exc:
        raise ValueError("results must contain only finite JSON-compatible data") from exc
    return sorted(results, key=lambda r: (_scenario_order(r["scenario"]), MODELS.index(r["model"])))


def _checks(result: dict) -> list[tuple[str, Decimal]]:
    """Return exact accounting residuals; zero is PASS, never a tolerance shortcut."""
    m = {name: Decimal(result["metrics"][name]) for name in DECIMAL_METRICS}
    all_values = list(m.values()) + [
        Decimal(balance[asset]) for field in ("initial_balances", "final_balances")
        for balance in result[field].values() for asset in ("btc", "usdt")
    ]
    # Avoid silently rounding long, but valid, simulator decimal strings.
    with localcontext() as context:
        context.prec = max(50, sum(len(v.as_tuple().digits) + abs(v.as_tuple().exponent) for v in all_values) + 10)
        initial_btc = sum((Decimal(b["btc"]) for b in result["initial_balances"].values()), Decimal(0))
        final_btc = sum((Decimal(b["btc"]) for b in result["final_balances"].values()), Decimal(0))
        return [
            ("Ending equity − initial equity = total PnL", m["ending_equity_usdt"] - m["initial_equity_usdt"] - m["total_pnl_usdt"]),
            ("Hold benchmark − initial equity = inventory PnL", m["hold_benchmark_usdt"] - m["initial_equity_usdt"] - m["inventory_pnl_usdt"]),
            ("Ending equity − hold benchmark = execution contribution", m["ending_equity_usdt"] - m["hold_benchmark_usdt"] - m["execution_contribution_usdt"]),
            ("Total PnL = inventory PnL + execution contribution", m["total_pnl_usdt"] - m["inventory_pnl_usdt"] - m["execution_contribution_usdt"]),
            ("Final BTC − initial BTC = ending extra BTC", final_btc - initial_btc - m["ending_extra_btc"]),
        ]


def _number(value: object) -> str:
    if isinstance(value, str):
        try:
            number = Decimal(value)
            if number.is_finite():
                rendered = format(number, "f")
                return rendered.rstrip("0").rstrip(".") if "." in rendered else rendered
        except InvalidOperation:
            pass
    return str(value)


def _pretty(value: object) -> str:
    return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False)


def _table(headers: list[str], rows: list[list[object]]) -> str:
    cells = "".join(f"<th scope='col'>{escape(header)}</th>" for header in headers)
    body = "".join("<tr>" + "".join(f"<td>{escape(_number(cell))}</td>" for cell in row) + "</tr>" for row in rows)
    return f"<div class='table-scroll'><table><thead><tr>{cells}</tr></thead><tbody>{body}</tbody></table></div>"


def _markdown_table(headers: list[str], rows: list[list[object]]) -> str:
    def cell(value: object) -> str:
        return escape(_number(value)).replace("|", "&#124;").replace("\n", " ")
    return "\n".join([
        "| " + " | ".join(cell(h) for h in headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
        *("| " + " | ".join(cell(v) for v in row) + " |" for row in rows),
    ])


def _figure(results: list[dict], figures: Path) -> tuple[Path, Path]:
    try:
        import matplotlib
        matplotlib.use("Agg", force=True)
        import matplotlib.pyplot as plt
        from matplotlib.patches import Patch
        from matplotlib.ticker import FuncFormatter, MaxNLocator
    except ImportError as exc:
        raise RuntimeError("Report figures require matplotlib; the simulator itself does not.") from exc

    scenarios = sorted({r["scenario"] for r in results}, key=_scenario_order)
    models = [m for m in MODELS if any(r["model"] == m for r in results)]
    lookup = {(r["scenario"], r["model"]): r for r in results}
    source_kinds = {r["source_kind"] for r in results}
    if source_kinds == {"synthetic"}:
        figure_source = "Synthetic mechanism experiments"
    elif "mixed" in source_kinds or ("synthetic" in source_kinds and len(source_kinds) > 1):
        figure_source = "Mixed or source-declared inputs · simulated execution"
    elif source_kinds == {"tardis_l5_sample"}:
        figure_source = "Historical vendor snapshots · simulated execution"
    else:
        figure_source = "Source-declared snapshots · simulated execution"
    if any(r.get("publication", {}).get("mode") == "rounded_aggregate_only" for r in results):
        figure_source += " · rounded: 1 USDT / 0.001 BTC"
    colors = {"screen": "#89939e", "fees": "#285f9c", "depth": "#667d2d", "latency": "#c18c20", "guarded": "#b35e89"}
    hatches = {"screen": "", "fees": "//", "depth": "..", "latency": "\\\\", "guarded": "xx"}
    with plt.rc_context({
        "font.family": "DejaVu Sans", "font.size": 11,
        "axes.spines.top": False, "axes.spines.right": False,
        "axes.edgecolor": "#b9c1ca", "axes.labelcolor": "#263441",
        "text.color": "#263441", "xtick.color": "#435362", "ytick.color": "#435362",
        "svg.fonttype": "none", "svg.hashsalt": "crossvenue-report-v1",
        "figure.facecolor": "white", "axes.facecolor": "white",
    }):
        fig, axes = plt.subplots(2, 1, figsize=(13.8, 8.8), sharex=True)
        fig.subplots_adjust(top=.79, bottom=.14, left=.09, right=.98, hspace=.40)
        fig.suptitle("Execution contribution and unmatched inventory", x=.09, y=.97, ha="left", fontsize=18, fontweight="bold")
        fig.text(.09, .925, figure_source, fontsize=11, color="#596b7a")
        fig.text(.09, .895, "Complete within-model runs only · ? = Unknown execution evidence (not zero)", fontsize=10, color="#596b7a")
        width = .76 / len(models)
        for ax, metric, label, title in (
            (axes[0], "execution_contribution_usdt", "USDT vs initial-inventory hold", "A   Execution contribution (fees included once)"),
            (axes[1], "max_extra_btc", "BTC · peak absolute extra inventory", "B   Maximum unmatched inventory"),
        ):
            numeric_bars = 0
            for j, model in enumerate(models):
                positions, values, bar_scenarios = [], [], []
                for i, scenario in enumerate(scenarios):
                    if (scenario, model) in lookup:
                        position = i + (j - (len(models) - 1) / 2) * width
                        if lookup[(scenario, model)]["valuation_status"] != "complete_within_model":
                            unknown = ax.text(position, .04, "?", transform=ax.get_xaxis_transform(),
                                              ha="center", va="bottom", color=colors[model], fontweight="bold", fontsize=11)
                            unknown.set_gid(f"unknown-{metric}-{scenario}-{model}")
                            continue
                        value = float(Decimal(lookup[(scenario, model)]["metrics"][metric]))
                        if not __import__("math").isfinite(value):
                            raise ValueError(f"{scenario}/{model}/{metric}: value cannot be plotted finitely")
                        positions.append(position)
                        values.append(value)
                        bar_scenarios.append(scenario)
                bars = ax.bar(positions, values, width=width*.9, label=model, color=colors[model],
                              hatch=hatches[model], edgecolor="white", linewidth=.6, zorder=3)
                numeric_bars += len(values)
                for bar, scenario in zip(bars.patches, bar_scenarios):
                    bar.set_gid(f"run-bar-{metric}-{scenario}-{model}")
            ax.axhline(0, color="#46535f", linewidth=.9, zorder=2)
            ax.set_title(title, loc="left", fontsize=12, pad=12)
            ax.set_ylabel(label, labelpad=10)
            ax.grid(axis="y", color="#e5e9ec", linewidth=.7, zorder=0)
            ax.yaxis.set_major_locator(MaxNLocator(nbins=5))
            ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:,.6f}".rstrip("0").rstrip(".") if v else "0"))
            ax.margins(x=.035, y=.18)
            if metric == "max_extra_btc":
                ax.set_ylim(bottom=0)
            if not numeric_bars:
                ax.set_ylim(0, 1)
                ax.set_yticks([])
                ax.text(.5, .52, "Unknown — execution evidence incomplete", transform=ax.transAxes,
                        ha="center", va="center", color="#596b7a", fontsize=12)
        handles = [Patch(facecolor=colors[m], edgecolor="white", hatch=hatches[m], label=m) for m in models]
        fig.legend(handles=handles, ncol=len(models), loc="upper left", bbox_to_anchor=(.085, .865), frameon=False)
        axes[1].set_xticks(range(len(scenarios)), [SCENARIO_LABELS.get(s, s.replace("_", " ")).replace(" ", "\n", 1) for s in scenarios])
        fig.text(.09, .035, "Cash includes fees once. Extra BTC is relative to initial holdings. Unknown executions have no numeric bars. Mark is not liquidation value.", fontsize=9, color="#596b7a")
        svg = figures / "execution_contribution.svg"
        png = figures / "execution_contribution.png"
        try:
            fig.savefig(svg, metadata={"Date": None, "Creator": "Cross-Venue Execution Lab"})
            fig.savefig(png, dpi=180, metadata={"Software": "Cross-Venue Execution Lab"})
        finally:
            plt.close(fig)
    return svg, png


def write_report(results: list[dict], output_dir: Path, *, public_safe: bool = True) -> dict[str, str]:
    """Write portable reports, static figures and source-aware exported JSON.

    Input order does not affect the report; every scenario/model pair is unique.
    Accounting failures remain visible as FAIL, rather than being rewritten.
    Non-synthetic traces and unapproved metadata are excluded by default.
    """
    if type(public_safe) is not bool:
        raise ValueError("public_safe must be an explicit boolean")
    results = _public_runs(_validate(results), public_safe)
    output_dir = Path(output_dir)
    source_kinds = sorted({r["source_kind"] for r in results})
    synthetic = source_kinds == ["synthetic"]
    source_label = "Synthetic Mechanism Experiments" if synthetic else "Snapshot-Replay / Source-Declared Experiments"
    evidence_note = (
        "All inputs are synthetic. These are deterministic mechanism tests, not historical market results, real trading profits, production incidents, or evidence of a profitable strategy."
        if synthetic else
        "Source kinds are declared by the input, not independently certified by this report. Snapshot replays retain simulated execution, timing, depth and fill assumptions; results do not establish live profitability or production deployment. Inspect each run's source and assumptions."
    )
    redacted_count = sum("provenance" in r and r["provenance"].get("export") == "aggregate_only" for r in results)
    export_note = (
        f"Public-safe export: {redacted_count} non-synthetic run(s) expose rounded aggregate metrics and resolved parameters only. "
        "Money is rounded to 1 USDT and extra BTC to 0.001 BTC; approximate zero is not proof of exactly zero exposure. "
        "Exact ledger checked before export: only PASS/FAIL audit results are published, not exact residuals. "
        "Balances, equity, inventory PnL, marks, decisions, fills, event-level curves, raw books and file paths are redacted. "
        "This is an offline aggregate research report, not a publicly redistributable market-data replay."
        if redacted_count else "Synthetic traces are inspectable below." if synthetic else
        "Restricted full-trace export requested (public_safe=False). Review market-data redistribution permissions before sharing."
    )
    canonical = json.dumps(results, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    digest = sha256(canonical.encode("utf-8")).hexdigest()
    figures = output_dir / "figures"
    figures.mkdir(parents=True, exist_ok=True)
    svg_path, png_path = _figure(results, figures)
    json_path = output_dir / "results.json"
    json_path.write_text(_pretty(results) + "\n", encoding="utf-8")

    summary_headers = (["Scenario", "Model", "Valuation", "Execution USDT", "Inventory PnL USDT", "Total PnL USDT", "Known fees USDT", "Peak extra BTC", "Unhedged ms", "Accounting"]
                       if synthetic else ["Scenario", "Model", "Valuation", "Execution USDT", "Known fees USDT", "Peak extra BTC", "Ending extra BTC", "Private ledger audit"])
    summary_rows = []
    failure_count = 0
    incomplete_count = 0
    details_html, details_md = [], []
    for result in results:
        m = result["metrics"]
        rounded_public = result.get("publication", {}).get("mode") == "rounded_aggregate_only"
        checks = None if rounded_public else _checks(result)
        audit = result["ledger_audit"]["checks"] if rounded_public else [
            {"identity": name, "status": "PASS" if residual == 0 else "FAIL"} for name, residual in checks]
        passed = all(check["status"] == "PASS" for check in audit)
        failure_count += not passed
        complete = result["valuation_status"] == "complete_within_model"
        incomplete_count += not complete
        def observed(name):
            if name not in m:
                return "Withheld from public export"
            value = ("≈" if rounded_public and name in DECIMAL_METRICS else "") + _number(m[name])
            return value if complete else f"Unknown (known ledger: {value})"
        leading = [result["scenario"], result["model"], "COMPLETE WITHIN MODEL" if complete else "INCOMPLETE"]
        if synthetic:
            values = [observed("execution_contribution_usdt"), m["inventory_pnl_usdt"], observed("total_pnl_usdt"),
                      observed("fees_usdt"), observed("max_extra_btc"), observed("max_unhedged_duration_ms")]
        else:
            values = [observed("execution_contribution_usdt"), observed("fees_usdt"), observed("max_extra_btc"), observed("ending_extra_btc")]
        summary_rows.append(leading + values + ["PASS" if passed else "FAIL"])
        heading = f"{result['scenario']} / {result['model']}"
        count_rows = [[name, m[name]] for name in COUNT_METRICS if name in m]
        money_rows = [[name, ("≈" if rounded_public else "") + _number(m[name])] for name in DECIMAL_METRICS if name in m]
        # Preserve any future metrics visibly, rather than silently discarding them.
        extras = [[name, _pretty(value)] for name, value in m.items() if name not in COUNT_METRICS + DECIMAL_METRICS + ("reject_counts",)]
        balance_rows = [] if rounded_public else [[stage, venue, balance["usdt"], balance["btc"]]
                        for stage, field in (("initial", "initial_balances"), ("final", "final_balances"))
                        for venue, balance in sorted(result[field].items())]
        check_rows = ([[check["identity"], check["status"]] for check in audit] if rounded_public else
                      [[name, "PASS" if residual == 0 else "FAIL", str(residual)] for name, residual in checks])
        rejected = [[name, count] for name, count in sorted(m["reject_counts"].items())]
        sections = [("Rounded public metrics" if rounded_public else "Metrics", ["Metric", "Value"], count_rows + money_rows + extras)]
        if not rounded_public:
            sections.append(("Venue settlement", ["Stage", "Venue", "USDT", "BTC"], balance_rows))
        sections.append(("Exact ledger checked before export" if rounded_public else "Accounting identities",
                         ["Identity", "Private exact status"] if rounded_public else ["Identity", "Status", "Exact residual"], check_rows))
        inner = f"<p class='source'>Source: {escape(result['source_kind'])} · Schema: {escape(str(result['schema_version']))}</p>"
        safe_source = escape(result["source_kind"]).replace("`", "&#96;").replace("\n", " ")
        markdown = [f"### {heading}", f"Source: {safe_source}."]
        evidence_status = (
            "COMPLETE WITHIN MODEL: executions are resolved under the simulator's assumptions; this does not prove real-world fills."
            if complete else
            "INCOMPLETE: execution outcomes are unresolved or censored. Numeric settlement metrics below describe only the known-fill ledger; they are not complete performance or actual peak-exposure estimates. Arithmetic PASS does not resolve unknown executions."
        )
        inner += "<p class='notice'>" + escape(evidence_status) + "</p>"
        markdown.append(evidence_status)
        if rounded_public:
            note = "Rounded aggregate export. Exact ledger checked before export; displayed rounded values are not expected to reproduce exact identities. Per-venue balances and exact financial aggregates are withheld."
            inner += "<p class='notice'>" + note + "</p>"
            markdown.append(note)
        for title, headers, rows in sections:
            inner += f"<h3>{title}</h3>" + _table(headers, rows)
            markdown.extend([f"#### {title}", _markdown_table(headers, rows)])
        inner += "<h3>Rejection reasons</h3>" + (_table(["Reason", "Count"], rejected) if rejected else "<p>No recorded rejection reasons.</p>")
        markdown.extend(["#### Rejection reasons", _markdown_table(["Reason", "Count"], rejected) if rejected else "No recorded rejection reasons."])
        for field in ("parameters", "assumptions", "decisions", "fills", "equity_curve", "quality_events"):
            if field not in result:
                continue
            text = _pretty(result[field])
            inner += f"<details class='raw'><summary>{escape(field)}</summary><pre>{escape(text)}</pre></details>"
            # Indented code blocks remain safe even when supplied strings contain fences.
            markdown.extend([f"#### {field}", "\n".join("    " + line for line in text.splitlines())])
        if "provenance" in result:
            text = _pretty(result["provenance"])
            inner += "<p class='notice'>Rounded aggregate-only export: exact financial detail and raw traces are redacted.</p><pre>" + escape(text) + "</pre>"
            markdown.extend(["#### Public export provenance", "\n".join("    " + line for line in text.splitlines())])
        run_badge = "INCOMPLETE" if not complete else "PASS" if passed else "FAIL"
        details_html.append(f"<details class='run'><summary>{escape(heading)} <span class='check {'ok' if passed and complete else 'fail'}'>{run_badge}</span></summary><div class='run-body'>{inner}</div></details>")
        details_md.append("\n\n".join(markdown))

    methods = [
        "Initial BTC is a real directional inventory exposure in this model. The hold benchmark retains the exact initial BTC and USDT at the same final common mark.",
        "Execution contribution = ending equity − hold benchmark = Δcash + ΔBTC × final mark. Total PnL = initial-inventory PnL + execution contribution.",
        "Fees are already deducted in cash and ending equity. The fee metric is a disclosure, not a second deduction from execution contribution.",
        "Common-mark equity and the equity curve are valuations, not realizable liquidation proceeds. Exit depth, fees and venue availability can change realizable value.",
        "Models change execution assumptions, filters and potentially fill quantities. Their differences are counterfactual comparisons, not strict additive cost attribution.",
        "Orders use separate prefunded venue inventories; spot borrowing and instantaneous cross-venue transfers are not assumed. Peak extra BTC is max(abs(total BTC − initial total BTC)); ending extra BTC is signed.",
        "Exposure peaks are sampled after both same-time leg attempts, not a measurement of intramillisecond leg risk. Parameters list actual resolved fees, latency, order size, thresholds and model switches.",
        "Decision-time information and subsequent execution must be separated by the simulator. This report displays supplied results and accounting checks; it does not independently certify causal replay correctness.",
        "round_trips_complete is the simulator's paired-leg completion counter, not proof that initial BTC inventory was liquidated or that capital completed a transfer cycle.",
        "Censored pairs and execution_unknown_pairs are separate from confirmed no-fill pairs. Incomplete runs expose only the known-fill ledger; complete performance, ending actual exposure and peak actual exposure remain unknown. The five arithmetic checks validate that ledger only.",
    ]
    warning = f"{failure_count} run(s) have nonzero accounting residuals. Inspect FAIL rows before interpreting results." if failure_count else "All supplied runs reconcile the five displayed accounting identities exactly. This is arithmetic validation, not evidence of realistic fills or profitability."
    if redacted_count:
        warning = (f"{failure_count} exact private known-fill ledger(s) failed accounting checks before export." if failure_count else
                   "Exact ledger checked before export: all five private known-fill accounting identities PASS.") + " Public monetary metrics are rounded aggregates; no exact balances or residuals are published."
    if incomplete_count:
        warning += f" {incomplete_count} run(s) have INCOMPLETE execution evidence and are excluded from numeric performance and exposure bars; ledger reconciliation does not make their outcomes known."
    svg = svg_path.read_text(encoding="utf-8")
    svg = svg[svg.index("<svg"):]
    css = """
    :root{color-scheme:light;--ink:#182b3a;--muted:#587080;--line:#dce4e9;--bg:#f4f7f9;--accent:#285f9c}
    *{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.65 -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif}
    main{max-width:1240px;margin:auto;padding:56px 36px 72px}header{border-bottom:1px solid var(--line);padding-bottom:26px;margin-bottom:30px}
    .eyebrow{font-size:12px;letter-spacing:.14em;text-transform:uppercase;color:var(--accent);font-weight:700}h1{font-size:42px;line-height:1.1;letter-spacing:-.035em;margin:12px 0 20px}h2{font-size:25px;margin:38px 0 12px}h3{font-size:17px;margin:25px 0 10px}
    p{max-width:94ch;margin:10px 0}.muted,.source{color:var(--muted)}.badge{display:inline-block;background:#e5edf6;padding:5px 12px;border-radius:4px;font-weight:600;font-size:13px}
    .facts{display:flex;gap:32px;flex-wrap:wrap;margin:22px 0 10px}.facts strong{display:block;font-size:24px;font-variant-numeric:tabular-nums}.facts span{font-size:12px;color:var(--muted);text-transform:uppercase;letter-spacing:.06em}
    .notice{border-left:3px solid var(--accent);padding:10px 18px;background:#edf3f8;font-size:14px}.notice.fail{border-color:#a0445f;background:#fbf0f3}.chart{background:white;padding:12px;margin:20px 0}.chart svg{width:100%;height:auto;display:block}.caption{font-size:13px;color:var(--muted)}
    .table-scroll{overflow-x:auto;max-width:100%;background:white;border:1px solid var(--line);border-radius:5px}table{border-collapse:collapse;width:100%;font-size:13px;font-variant-numeric:tabular-nums}th,td{text-align:right;padding:10px 12px;border-bottom:1px solid #edf0f3;white-space:nowrap}th{background:#edf2f6;color:#40586b;font-weight:600}td:first-child,th:first-child{text-align:left}tr:last-child td{border-bottom:0}tbody tr:hover{background:#f4f8fb}
    .run{background:white;border:1px solid var(--line);border-radius:5px;margin:10px 0}.run>summary{padding:14px 18px;cursor:pointer;font-weight:600}.run-body{padding:0 20px 22px}.check{float:right;font-size:11px;letter-spacing:.04em}.ok{color:#386134}.fail{color:#a0445f}.raw{margin-top:10px;border-top:1px solid var(--line);padding-top:10px}.raw summary{cursor:pointer;color:var(--accent);font-size:14px}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f4f7f9;padding:16px;border-radius:4px;font:12px/1.6 ui-monospace,SFMono-Regular,Consolas,monospace}code{font:12px ui-monospace,SFMono-Regular,Consolas,monospace;overflow-wrap:anywhere}li{padding:4px 0}footer{margin-top:38px;border-top:1px solid var(--line);padding-top:18px;color:var(--muted);font-size:12px}
    @media(max-width:700px){main{padding:28px 16px}h1{font-size:32px}.facts{gap:20px}.chart{padding:0;overflow-x:auto}.chart svg{min-width:760px}.run-body{padding:0 10px 16px}th,td{padding:8px}.check{float:none;margin-left:10px}}
    @media print{body{background:white}main{max-width:none;padding:0}.run{break-inside:avoid}.chart{padding:0}details:not([open])>.run-body{display:block}}
    """
    chinese_note = "固定初始库存与估值口径，对照费用、深度、延迟及单腿风险；合成实验不代表实采收益。" if synthetic else "固定初始库存与估值口径；快照回放依赖模拟撮合假设，不代表实盘收益。"
    html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Cross-Venue Execution Lab</title><style>{css}</style></head>
<body><main><header><div class="eyebrow">Cross-Venue Execution Lab</div><h1>What survives the spread?</h1><span class="badge">{escape(source_label)}</span><p>{escape(evidence_note)}</p>
<p class="muted">{chinese_note}</p><p class="caption">{escape(export_note)}</p>
<div class="facts"><div><strong>{len(results)}</strong><span>Scenario / model runs</span></div><div><strong>{len({r['scenario'] for r in results})}</strong><span>Scenarios</span></div><div><strong>{len({r['model'] for r in results})}</strong><span>Execution models</span></div><div><strong>{failure_count}</strong><span>Accounting failures</span></div></div></header>
<p class="notice{' fail' if failure_count else ''}">{escape(warning)}</p><h2>Execution assumptions change the result</h2><p>Each bar is one supplied scenario/model run. This is a finite mechanism comparison, not a statistical sample of market opportunities. Missing scenario/model combinations are omitted rather than filled with zero.</p>
<figure class="chart" aria-label="Grouped bar charts comparing execution contribution in USDT and peak absolute extra BTC by scenario and execution model">{svg}<figcaption class="caption">A common model order and color map is used in both panels; hatching provides a second model encoding. ? = Unknown execution evidence; no numeric bar is plotted for that run. Published values and evidence status follow below.</figcaption></figure>
<h2>Comparable settlement metrics</h2><p class="caption">Amounts are USDT unless BTC or milliseconds are specified. No return annualization, Sharpe ratio or significance claim is computed.</p>{_table(summary_headers, summary_rows)}
<h2>Accounting and interpretation</h2><ul>{''.join('<li>'+escape(note)+'</li>' for note in methods)}</ul>
<h2>Inspect every run</h2><p>Expand a run for metrics, rejection reasons, resolved parameters and accounting status. Synthetic runs include exact balances, residuals and traces. Non-synthetic public exports show rounded aggregates and private-ledger PASS/FAIL only.</p>{''.join(details_html)}
<footer><p>Source kinds: {escape(', '.join(source_kinds))}. Canonical exported-result SHA-256:<br><code>{digest}</code></p><p>Deterministic content: no generated-as-of timestamp or market freshness claim. Charts are embedded; this HTML file can be read fully offline without its neighboring files.</p></footer></main></body></html>"""
    html_path = output_dir / "research_report.html"
    html_path.write_text(html, encoding="utf-8")
    md = "\n\n".join([
        "# Cross-Venue Execution Lab", f"**{source_label}**", evidence_note, export_note,
        f"{len(results)} runs · {len({r['scenario'] for r in results})} scenarios · {len({r['model'] for r in results})} models.",
        warning, "## Execution assumptions change the result",
        "![Execution contribution and peak absolute extra BTC](figures/execution_contribution.svg)",
        "Each numeric bar represents one complete-within-model run. ? = Unknown execution evidence; incomplete runs have no numeric performance or exposure bar. Missing combinations are not zero. Published values and evidence status follow.",
        "## Settlement comparison", _markdown_table(summary_headers, summary_rows),
        "## Accounting and interpretation", "\n".join("- " + note for note in methods),
        "## Run evidence", *details_md,
        "## Reproducibility", f"Canonical exported-result SHA-256: `{digest}`.",
        "Exported results: [results.json](results.json). Non-synthetic public exports are aggregate-only. No generated-as-of timestamp or market freshness claim.",
    ]) + "\n"
    md_path = output_dir / "research_report.md"
    md_path.write_text(md, encoding="utf-8")
    return {"report_html": str(html_path.resolve()), "report_md": str(md_path.resolve()),
            "results_json": str(json_path.resolve()), "figure_svg": str(svg_path.resolve()),
            "figure_png": str(png_path.resolve())}
