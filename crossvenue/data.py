"""Deterministic mechanism fixtures, configuration, and normalized JSONL IO.

Fixtures are deliberately constructed experiments, never historical observations.
Each book row is a full snapshot of its *declared* visible depth.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path


SCENARIO_ORDER = (
    "fee_trap", "depth_trap", "latency_race", "partial_leg", "stale_quote", "venue_inventory"
)
MODEL_ORDER = ("screen", "fees", "depth", "latency", "guarded")

BASE_CONFIG = {
    "venues": ["alpha", "beta"],
    "symbol": "BTCUSDT",
    "order_qty": "0.02",
    "fee_bps": {"alpha": "10", "beta": "10"},
    "latency_ms": 100,
    "max_quote_age_ms": 250,
    "max_event_skew_ms": 100,
    "max_unhedged_btc": "0.005",
    "min_edge_bps": "0",
    "max_pairs": 1,
    "depth_mode": "l2",
    "signal_policy": "net",
    "quality_gates": True,
    "risk_halt": True,
    "require_event_time": True,
    "initial_balances": {
        "alpha": {"usdt": "10000", "btc": "0.1"},
        "beta": {"usdt": "10000", "btc": "0.1"},
    },
    "mark_start": "100000",
    "mark_end": "100000",
}

MODEL_DESCRIPTIONS = {
    "screen": "Top price, optimistic depth, zero fees and latency; gross-edge entry.",
    "fees": "Top price, optimistic depth, two-sided fees; gross-edge entry.",
    "depth": "Visible L2 depth and fees, zero latency; gross-edge entry.",
    "latency": "Visible L2, fees and latency; gross-edge entry, no exposure halt.",
    "guarded": "Visible L2, fees, latency, net-edge entry, quote gates and exposure halt.",
}


def model_config(model: str, overrides: dict | None = None) -> dict:
    if model not in MODEL_ORDER:
        raise ValueError(f"unknown model: {model}")
    config = deepcopy(BASE_CONFIG)
    if overrides:
        config.update(deepcopy(overrides))
    # The scenario's inventory, mark and order quantity remain identical across models.
    config.update({"depth_mode": "l2", "signal_policy": "gross", "quality_gates": False, "risk_halt": False})
    if model == "screen":
        config.update({"depth_mode": "top", "latency_ms": 0, "fee_bps": {v: "0" for v in config["venues"]}})
    elif model == "fees":
        config.update({"depth_mode": "top", "latency_ms": 0})
    elif model == "depth":
        config["latency_ms"] = 0
    elif model == "guarded":
        config.update({"signal_policy": "net", "quality_gates": True, "risk_halt": True})
    return config


def snapshot(venue: str, recv_ms: int, sequence: int, bids: list, asks: list,
             *, event_ms: int | None = None) -> dict:
    return {
        "schema_version": 1, "venue": venue, "symbol": "BTCUSDT",
        "event_ms": recv_ms if event_ms is None else event_ms,
        "recv_ms": recv_ms, "sequence": sequence,
        "bids": [[str(p), str(q)] for p, q in bids],
        "asks": [[str(p), str(q)] for p, q in asks],
        "source_kind": "synthetic", "timing_basis": "synthetic_receive",
    }


def _books(recv_ms: int, sequence: int, *, b_bid: str = "100500", b_ask: str = "100510") -> list[dict]:
    return [
        snapshot("alpha", recv_ms, sequence, [("99990", "0.5")], [("100000", "0.5"), ("100010", "0.5")]),
        snapshot("beta", recv_ms, sequence, [(b_bid, "0.5")], [(b_ask, "0.5")]),
    ]


def _neutral(recv_ms: int = 300, sequence: int = 3, *, lower: bool = False) -> list[dict]:
    bid, ask = ("98990", "99010") if lower else ("99990", "100010")
    return [snapshot(v, recv_ms, sequence, [(bid, "0.5")], [(ask, "0.5")]) for v in ("alpha", "beta")]


def build_scenarios() -> list[dict]:
    """Six isolated experiments; max_pairs=1 makes each submitted pair inspectable."""
    fee = _books(0, 1, b_bid="100100", b_ask="100110") + _books(99, 2, b_bid="100100", b_ask="100110") + _neutral()
    depth = []
    for time, seq in ((0, 1), (99, 2)):
        depth.extend([
            snapshot("alpha", time, seq, [("99990", "0.5")], [("100000", "0.005"), ("100200", "0.5")]),
            snapshot("beta", time, seq, [("100300", "0.005"), ("100000", "0.5")], [("100310", "0.5")]),
        ])
    depth += _neutral()
    race = _books(0, 1) + _books(99, 2, b_bid="99000", b_ask="99010") + _neutral(lower=True)
    partial = _books(0, 1) + [
        snapshot("alpha", 99, 2, [("99990", "0.5")], [("100000", "0.5"), ("100010", "0.5")]),
        snapshot("beta", 99, 2, [("100500", "0.005"), ("99900", "0.5")], [("100510", "0.5")]),
    ] + _neutral(lower=True)
    stale = [
        snapshot("alpha", 0, 1, [("99990", "0.5")], [("100000", "0.5")]),
        snapshot("beta", 1000, 1, [("100500", "0.5")], [("100510", "0.5")]),
        snapshot("beta", 1099, 2, [("100500", "0.5")], [("100510", "0.5")]),
        snapshot("beta", 1300, 3, [("99990", "0.5")], [("100010", "0.5")]),
    ]
    poor_inventory = {
        "alpha": {"usdt": "500", "btc": "0.2"},
        "beta": {"usdt": "20000", "btc": "0.005"},
    }
    rows = [
        ("fee_trap", "A positive top-of-book spread becomes negative after two-sided fees.", fee, {}),
        ("depth_trap", "The top level is too small; quantity-dependent VWAP erases the apparent edge.", depth, {}),
        ("latency_race", "A sell quote moves before IOC arrival; only the buy fills before a lower terminal mark.", race, {"mark_end": "99000"}),
        ("partial_leg", "The sell book thins after the decision, leaving unmatched BTC and triggering a halt.", partial, {"mark_end": "99000"}),
        ("stale_quote", "A fresh quote is paired with an old snapshot; guarded entry rejects it.", stale, {}),
        ("venue_inventory", "Total resources are ample, but they are on the wrong venues for this direction.", _books(0, 1) + _books(99, 2) + _neutral(), {"initial_balances": poor_inventory}),
    ]
    return [
        {"id": name, "description": text, "source_kind": "synthetic", "events": events,
         "config_overrides": overrides, "controlled_pair_budget": 1}
        for name, text, events, overrides in rows
    ]


def write_jsonl(path: Path, events: list[dict]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = "".join(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n" for row in events).encode()
    path.write_bytes(encoded)
    return hashlib.sha256(encoded).hexdigest()


def read_jsonl(path: Path) -> list[dict]:
    rows = []
    with path.open(encoding="utf-8") as stream:
        for line_no, line in enumerate(stream, 1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError(f"{path.name}:{line_no}: each row must be an object")
            rows.append(row)
    if not rows:
        raise ValueError("empty replay input")
    return rows


def write_fixtures(directory: Path) -> dict:
    directory.mkdir(parents=True, exist_ok=True)
    entries = []
    for scenario in build_scenarios():
        path = directory / f"{scenario['id']}.jsonl"
        digest = write_jsonl(path, scenario["events"])
        entries.append({"id": scenario["id"], "file": path.name, "sha256": digest,
                        "rows": len(scenario["events"]), "description": scenario["description"],
                        "config_overrides": scenario["config_overrides"]})
    manifest = {"schema_version": 1, "source_kind": "synthetic", "seed": "mechanical-fixtures-v1",
                "note": "Engineered mechanism experiments, not historical observations.", "scenarios": entries}
    (directory / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest
