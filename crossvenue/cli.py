"""Reproducible local research commands. No order-routing or account endpoints."""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import platform
import sys

from .data import BASE_CONFIG, MODEL_ORDER, build_scenarios, model_config, read_jsonl, write_fixtures, write_jsonl
from .engine import run_replay


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def _code_hash() -> str:
    digest = hashlib.sha256()
    for path in sorted(Path(__file__).parent.glob("*.py")):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _save_report(results: list[dict], output: Path, provenance: dict) -> dict:
    from .report import write_report
    files = write_report(results, output, public_safe=True)
    # Provenance contains aggregate metadata and hashes, never raw venue rows.
    manifest = {"schema_version": 1, "python": platform.python_version(),
                "code_sha256": _code_hash(), "public_safe_export": True,
                "parameters_by_run": [
                    {"scenario": run["scenario"], "model": run["model"],
                     "parameters": run["parameters"]} for run in results
                ], **provenance}
    output.mkdir(parents=True, exist_ok=True)
    (output / "run_manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    for name, path in sorted(files.items()):
        print(f"{name}: {path}")
    print(f"run_manifest: {output / 'run_manifest.json'}")
    return files


def run_demo(output: Path) -> list[dict]:
    manifest = write_fixtures(output / "fixtures")
    results = []
    for scenario in build_scenarios():
        for model in MODEL_ORDER:
            config = model_config(model, scenario["config_overrides"])
            results.append(run_replay(scenario["events"], config, scenario=scenario["id"], model=model))
    _save_report(results, output, {"source_kind": "synthetic", "dataset_manifest": manifest,
                                 "models": list(MODEL_ORDER), "runs": len(results)})
    return results


def _fixture_overrides(path: Path) -> dict:
    manifest_path = path.parent / "manifest.json"
    if not manifest_path.is_file():
        return {}
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("source_kind") != "synthetic":
        return {}
    for row in manifest.get("scenarios", []):
        if row.get("file") == path.name:
            if row.get("sha256") != _sha(path):
                raise ValueError("fixture checksum differs from its manifest")
            return row.get("config_overrides", {})
    return {}


def run_file(path: Path, output: Path, config_file: Path | None, model: str) -> list[dict]:
    events = read_jsonl(path)
    overrides = _fixture_overrides(path)
    supplied = {}
    if config_file:
        supplied = json.loads(config_file.read_text(encoding="utf-8"))
        if not isinstance(supplied, dict):
            raise ValueError("config must be a JSON object")
        overrides.update(supplied)
    if any(row.get("source_kind") != "synthetic" for row in events):
        if not config_file or not {"mark_start", "mark_end"}.issubset(supplied):
            raise ValueError("non-synthetic replay requires an explicit config with common mark_start/mark_end")
    models = MODEL_ORDER if model == "all" else (model,)
    results = [run_replay(events, model_config(m, overrides), scenario=path.stem, model=m) for m in models]
    _save_report(results, output, {"source_kind": results[0]["source_kind"], "input_sha256": _sha(path),
                                 "models": list(models), "runs": len(results)})
    return results


def run_sample(args) -> list[dict]:
    from .tardis import download_samples, import_l5
    private_dir = Path("data") / "tardis-private" / args.day
    private_dir.mkdir(parents=True, exist_ok=True)
    if args.input_files:
        paths = [Path(p) for p in args.input_files]
    else:
        paths = download_samples(args.day, private_dir, timeout=args.timeout)
    events, meta = import_l5(paths, window_seconds=args.window_seconds, max_events=args.max_events)
    normalized_sha = write_jsonl(private_dir / "normalized.private.jsonl", events)
    # Private evidence remains local and is excluded by .gitignore.
    (private_dir / "source_manifest.private.json").write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    overrides = deepcopy(BASE_CONFIG)
    overrides.update({"order_qty": args.order_qty, "max_pairs": args.pair_budget,
                      "mark_start": meta["mark_start"], "mark_end": meta["mark_end"],
                      "latency_ms": args.latency_ms, "max_quote_age_ms": args.max_quote_age_ms,
                      "max_event_skew_ms": args.max_event_skew_ms})
    results = [run_replay(events, model_config(m, overrides), scenario="tardis_l5_sample", model=m) for m in MODEL_ORDER]
    safe_meta = {key: meta[key] for key in (
        "source_kind", "timing_basis", "clock_quantization", "source_urls", "start_ms", "end_ms",
        "requested_end_ms", "record_counts", "limited_depth", "truncated", "limitations"
    ) if key in meta}
    safe_meta.update({
        "first_retained_recv_ms": min(row["recv_ms"] for row in events),
        "last_retained_recv_ms": max(row["recv_ms"] for row in events),
        "replay_horizon_note": "The selection window may end after the last retained snapshot. The engine ends at that last snapshot; no heartbeat or new liquidity is fabricated.",
    })
    _save_report(results, Path(args.output), {
        "source_kind": "tardis_l5_sample", "requested_sample_day": args.day,
        "input_mode": "provided_files" if args.input_files else "vendor_download_or_cache",
        "models": list(MODEL_ORDER),
        "runs": len(results), "normalized_input_sha256": normalized_sha,
        "compressed_files": [{"name": p.name, "sha256": _sha(p), "bytes": p.stat().st_size} for p in paths],
        "source_metadata": safe_meta,
        "note": "Historical vendor snapshots; simulated execution, assumed fees and extra latency. No live trading result.",
    })
    if args.sweep:
        from .sensitivity import run_sensitivity, write_sensitivity
        paths = write_sensitivity(run_sensitivity(events, overrides), Path(args.output))
        for name, path in sorted(paths.items()):
            print(f"{name}: {path}")
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="crossvenue", description="BTC cross-venue execution research with causal snapshot replay")
    sub = parser.add_subparsers(dest="command", required=True)
    demo = sub.add_parser("demo", help="run six synthetic scenarios across five model assumptions")
    demo.add_argument("--output", type=Path, default=Path("outputs/demo"))
    fixtures = sub.add_parser("fixtures", help="write reproducible synthetic JSONL and checksums")
    fixtures.add_argument("--output", type=Path, default=Path("datasets/synthetic"))
    replay = sub.add_parser("replay", help="replay normalized snapshots")
    replay.add_argument("--input", required=True, type=Path)
    replay.add_argument("--config", type=Path)
    replay.add_argument("--model", choices=(*MODEL_ORDER, "all"), default="guarded")
    replay.add_argument("--output", type=Path, default=Path("outputs/replay"))
    sample = sub.add_parser("sample", help="explicitly fetch/import licensed first-day Tardis BTC L5 samples")
    sample.add_argument("--day", default="2026-10-01")
    sample.add_argument("--input-files", nargs=2, metavar=("BINANCE_GZIP", "BYBIT_SPOT_GZIP"))
    sample.add_argument("--window-seconds", type=int, default=600)
    sample.add_argument("--max-events", type=int, default=100000)
    sample.add_argument("--pair-budget", type=int, default=100)
    sample.add_argument("--order-qty", default="0.005")
    sample.add_argument("--latency-ms", type=int, default=100)
    sample.add_argument("--max-quote-age-ms", type=int, default=250)
    sample.add_argument("--max-event-skew-ms", type=int, default=100)
    sample.add_argument("--timeout", type=int, default=30)
    sample.add_argument("--sweep", action="store_true", help="also run the predefined fee/latency sensitivity grid")
    sample.add_argument("--output", default="outputs/real-sample")
    args = parser.parse_args(argv)
    try:
        if args.command == "demo":
            run_demo(args.output)
        elif args.command == "fixtures":
            manifest = write_fixtures(args.output)
            print(f"wrote {len(manifest['scenarios'])} synthetic fixtures to {args.output}")
        elif args.command == "replay":
            run_file(args.input, args.output, args.config, args.model)
        else:
            run_sample(args)
    except (ValueError, OSError, EOFError, KeyError, ImportError, RuntimeError) as exc:
        parser.exit(2, f"crossvenue: {exc}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
