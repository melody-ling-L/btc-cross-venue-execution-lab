"""Optional Tardis BTC spot L5 samples, kept outside published data artifacts.

Nothing is downloaded on import. Tardis sample data is licensed, not open data:
raw and reformatted tick records must remain private. Public fixtures should be
synthetic; public research output should contain only permitted derived results.
"""

from __future__ import annotations

from contextlib import ExitStack
import csv
from datetime import date
from decimal import Decimal, InvalidOperation, localcontext
import gzip
import heapq
from pathlib import Path
import re
from typing import Sequence
from urllib.error import HTTPError
from urllib.request import Request, urlopen


_BASE = "https://datasets.tardis.dev/v1"
_VENUES = {"binance": "alpha", "bybit-spot": "beta"}
_SOURCE_KIND = "tardis_l5_sample"
_TIMING_BASIS = "vendor_receive"
_DOCS = "https://docs.tardis.dev/downloadable-csv-files/data-types"
_TERMS = "https://docs.tardis.dev/legal/terms-of-service"
_FILE_RE = re.compile(
    r"^(binance|bybit-spot)_book_snapshot_5_(\d{4}-\d{2}-\d{2})_BTCUSDT\.csv\.gz$"
)
_FIELDS = ["exchange", "symbol", "timestamp", "local_timestamp"] + [
    f"{side}[{level}].{field}"
    for level in range(5)
    for side in ("asks", "bids")
    for field in ("price", "amount")
]


def _positive_int(value: object, label: str) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError(f"{label} must be a positive integer")
    return value


def _sample_day(day: str) -> date:
    if not isinstance(day, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-01", day):
        raise ValueError("only first-of-month YYYY-MM-01 free samples are supported")
    try:
        return date.fromisoformat(day)
    except ValueError as exc:
        raise ValueError("invalid sample date") from exc


def _url(exchange: str, day: date) -> str:
    return f"{_BASE}/{exchange}/book_snapshot_5/{day:%Y/%m/%d}/BTCUSDT.csv.gz"


def _validate_cached_sample(path: Path, max_bytes: int) -> None:
    if not path.is_file() or path.stat().st_size == 0:
        raise ValueError(f"invalid cached sample (not a nonempty file): {path.name}")
    if path.stat().st_size > max_bytes:
        raise ValueError(f"cached compressed sample exceeds max_bytes: {path.name}")
    try:
        with gzip.open(path, "rb") as compressed:
            while compressed.read(65_536):
                pass
    except (OSError, EOFError, ValueError) as exc:
        raise ValueError(f"invalid cached sample gzip/CRC; file preserved: {path.name}") from exc


def download_samples(
    day: str, output_dir: Path, *, timeout: int = 30, max_bytes: int = 50_000_000
) -> list[Path]:
    """Explicitly download the two fixed first-day BTC spot L5 samples.

    The byte budget is per compressed file. A failed transfer leaves no .part
    file; an earlier completed file is retained if the second transfer fails.
    Existing complete gzip files within budget are reused without modification.
    Corrupt caches and existing .part files fail without deleting those files.
    TLS verification stays enabled; no retry loop or alternate host is used.
    """
    selected_day = _sample_day(day)
    _positive_int(timeout, "timeout")
    _positive_int(max_bytes, "max_bytes")
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    destinations = [
        output_dir / f"{exchange}_book_snapshot_5_{day}_BTCUSDT.csv.gz"
        for exchange in _VENUES
    ]
    cached = set()
    for destination in destinations:
        if destination.with_suffix(destination.suffix + ".part").exists():
            raise FileExistsError(f"partial sample destination already exists: {destination.name}.part")
        if destination.exists():
            _validate_cached_sample(destination, max_bytes)
            cached.add(destination)
    for exchange, destination in zip(_VENUES, destinations):
        if destination in cached:
            continue
        partial = destination.with_suffix(destination.suffix + ".part")
        owns_partial = False
        try:
            request = Request(_url(exchange, selected_day), headers={"User-Agent": "cross-venue-risk-lab/1.0"})
            try:
                response = urlopen(request, timeout=timeout)
            except HTTPError as exc:
                raise RuntimeError(f"Tardis sample request failed with HTTP {exc.code}") from None
            with response, partial.open("xb") as output:
                owns_partial = True
                if response.status != 200:
                    raise RuntimeError(f"Tardis sample request returned HTTP {response.status}")
                length = response.headers.get("Content-Length")
                if length is not None and int(length) > max_bytes:
                    raise ValueError("compressed sample exceeds max_bytes")
                size = 0
                while True:
                    chunk = response.read(min(65_536, max_bytes - size + 1))
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > max_bytes:
                        raise ValueError("compressed sample exceeds max_bytes")
                    output.write(chunk)
                if not size:
                    raise ValueError("empty sample response")
            # Verify gzip framing, trailer and CRC before promoting the file.
            with gzip.open(partial, "rb") as compressed:
                while compressed.read(65_536):
                    pass
            partial.rename(destination)
        except BaseException:
            if owns_partial:
                partial.unlink(missing_ok=True)
            raise
    return destinations


def _integer_us(value: object, label: str, *, optional: bool = False) -> int | None:
    if optional and isinstance(value, str) and not value.strip():
        return None
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]+", value.strip()):
        raise ValueError(f"{label}: expected nonnegative integer microseconds")
    return int(value.strip())


def _decimal(value: str, label: str) -> Decimal:
    if not re.fullmatch(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?", value):
        raise ValueError(f"{label}: invalid decimal")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"{label}: invalid decimal") from exc
    if not parsed.is_finite() or parsed <= 0:
        raise ValueError(f"{label}: expected finite positive decimal")
    return parsed


def _levels(row: dict, side: str, label: str) -> list[list[str]]:
    levels: list[list[str]] = []
    previous: Decimal | None = None
    empty_seen = False
    for level in range(5):
        price = row[f"{side}[{level}].price"]
        amount = row[f"{side}[{level}].amount"]
        if price is None or amount is None:
            raise ValueError(f"{label}: missing {side} value")
        price, amount = price.strip(), amount.strip()
        if not price and not amount:
            empty_seen = True
            continue
        if not price or not amount or empty_seen:
            raise ValueError(f"{label}: incomplete or nontrailing empty {side} level")
        number = _decimal(price, f"{label}.{side}[{level}].price")
        _decimal(amount, f"{label}.{side}[{level}].amount")
        if previous is not None and (
            (side == "asks" and number <= previous) or
            (side == "bids" and number >= previous)
        ):
            raise ValueError(f"{label}: {side} prices are not strictly ordered")
        previous = number
        levels.append([price, amount])
    if not levels:
        raise ValueError(f"{label}: an empty {side} side cannot establish a valuation book")
    return levels


class _Cursor:
    def __init__(self, reader: csv.DictReader, path: Path):
        self.reader, self.path = reader, path
        fields = reader.fieldnames
        if not fields or len(fields) != len(set(fields)) or not set(_FIELDS).issubset(fields):
            raise ValueError(f"{path.name}: missing or duplicate Tardis L5 CSV header fields")
        self.ordinal = 0
        self.exchange: str | None = None
        self.last_us: int | None = None
        self.eof = False

    def next(self) -> dict | None:
        try:
            row = next(self.reader)
        except StopIteration:
            self.eof = True
            return None
        self.ordinal += 1
        label = f"{self.path.name}: row {self.ordinal}"
        if None in row or any(row.get(field) is None for field in _FIELDS):
            raise ValueError(f"{label}: malformed CSV row")
        exchange = row["exchange"]
        if exchange not in _VENUES or row["symbol"] != "BTCUSDT":
            raise ValueError(f"{label}: expected a supported BTCUSDT spot exchange")
        if self.exchange is not None and exchange != self.exchange:
            raise ValueError(f"{label}: exchange changed within file")
        self.exchange = exchange
        received = _integer_us(row["local_timestamp"], f"{label}.local_timestamp")
        assert received is not None
        if self.last_us is not None and received < self.last_us:
            raise ValueError(f"{label}: vendor local_timestamp moved backwards")
        self.last_us = received
        event = _integer_us(row["timestamp"], f"{label}.timestamp", optional=True)
        bids, asks = _levels(row, "bids", label), _levels(row, "asks", label)
        if Decimal(bids[0][0]) >= Decimal(asks[0][0]):
            raise ValueError(f"{label}: crossed or locked reconstructed book")
        return {
            "venue": _VENUES[exchange], "symbol": "BTCUSDT",
            "event_ms": None if event is None else event // 1000,
            "recv_ms": (received + 999) // 1000, "recv_us": received,
            "sequence": self.ordinal, "sequence_kind": "source_row_ordinal",
            "bids": bids, "asks": asks, "source_kind": _SOURCE_KIND,
            "timing_basis": _TIMING_BASIS, "vendor_name": "Tardis.dev",
            "session": f"tardis:{exchange}:{self.path.name}",
        }


def _mark(books: dict[str, dict]) -> str:
    if set(books) != set(_VENUES.values()):
        raise ValueError("both venue books are required at the valuation boundary")
    with localcontext() as context:
        context.prec = 50
        return str(sum(
            (Decimal(book["bids"][0][0]) + Decimal(book["asks"][0][0])) / 2
            for book in books.values()
        ) / 2)


def import_l5(
    paths: Sequence[Path], *, window_seconds: int = 600, max_events: int = 100_000
) -> tuple[list[dict], dict]:
    """Import a bounded, overlapping window from one sample per venue.

    Input receive times must be monotone at microsecond precision. Only the
    consumed prefix (including at most one lookahead per source) is validated.
    At equal receive times, source venue order alpha/beta and original row order
    provide a deterministic tie break. Receive milliseconds round up to the end
    of the millisecond bucket, so every message in a decision bucket is already
    known at its decision time. The exchange clocks are not used to sort.
    """
    _positive_int(window_seconds, "window_seconds")
    _positive_int(max_events, "max_events")
    if len(paths) != 2 or max_events < 2:
        raise ValueError("provide exactly two venue files and max_events >= 2")
    paths = [Path(path) for path in paths]
    with ExitStack() as stack:
        cursors: dict[str, _Cursor] = {}
        firsts: dict[str, dict] = {}
        data_urls: list[str] = []
        for path in paths:
            stream = stack.enter_context(gzip.open(path, "rt", encoding="utf-8", newline=""))
            cursor = _Cursor(csv.DictReader(stream), path)
            first = cursor.next()
            if first is None:
                raise ValueError(f"{path.name}: empty sample")
            venue = first["venue"]
            if venue in cursors:
                raise ValueError("one binance and one bybit-spot source are required")
            cursors[venue], firsts[venue] = cursor, first
            match = _FILE_RE.fullmatch(path.name)
            if match and match[1] == cursor.exchange:
                data_urls.append(_url(match[1], _sample_day(match[2])))
        start_us = max(event["recv_us"] for event in firsts.values())
        requested_end_us = start_us + window_seconds * 1_000_000
        heap: list[tuple[int, str, int, dict]] = []
        events: list[dict] = []
        warmup_counts = {venue: 0 for venue in cursors}
        for venue in sorted(cursors):
            cursor, current = cursors[venue], firsts[venue]
            previous = None
            while current is not None and current["recv_us"] < start_us:
                previous = current
                current = cursor.next()
            if current is None:
                raise ValueError("source receive-time coverage does not overlap")
            if previous is not None:
                events.append(previous)
                warmup_counts[venue] = 1
            heapq.heappush(heap, (current["recv_us"], venue, current["sequence"], current))
        events.sort(key=lambda item: (item["recv_us"], item["venue"], item["sequence"]))
        cap_hit = False
        while heap:
            received, venue, _, event = heapq.heappop(heap)
            if received > requested_end_us:
                # Other heap entries cannot be inside the requested window.
                break
            if len(events) >= max_events:
                cap_hit = True
                break
            events.append(event)
            next_event = cursors[venue].next()
            if next_event is not None:
                heapq.heappush(heap, (next_event["recv_us"], venue, next_event["sequence"], next_event))
        end_us = events[-1]["recv_us"] if cap_hit else requested_end_us
        ends = [cursor.last_us for cursor in cursors.values() if cursor.eof]
        coverage_ended = bool(ends) and min(ends) < end_us
        if ends:
            end_us = min(end_us, *ends)
        if end_us < start_us:
            raise ValueError("no retained overlapping receive-time window")
        events = [event for event in events if event["recv_us"] <= end_us]
        start_books: dict[str, dict] = {}
        end_books: dict[str, dict] = {}
        for event in events:
            end_books[event["venue"]] = event
            if event["recv_us"] <= start_us:
                start_books[event["venue"]] = event
        # Never invent a missing initial quote to fit a very small event budget.
        mark_start, mark_end = _mark(start_books), _mark(end_books)
        reasons = []
        if cap_hit:
            reasons.append("max_events")
        if coverage_ended:
            reasons.append("source_coverage_ended")
        metadata = {
            "source_kind": _SOURCE_KIND, "timing_basis": _TIMING_BASIS,
            "clock_quantization": "ceil_to_ms_bucket_end",
            "vendor_name": "Tardis.dev", "source_urls": sorted(data_urls) or [_DOCS],
            "source_files": [str(path) for path in paths], "license_url": _TERMS,
            "start_ms": (start_us + 999) // 1000, "end_ms": (end_us + 999) // 1000,
            "start_us": start_us, "end_us": end_us,
            "requested_end_ms": (requested_end_us + 999) // 1000,
            "requested_window_seconds": window_seconds,
            "actual_window_seconds": str(Decimal(end_us - start_us) / 1_000_000),
            "record_counts": {venue: sum(event["venue"] == venue for event in events) for venue in sorted(cursors)},
            "rows_examined": {venue: cursor.ordinal for venue, cursor in sorted(cursors.items())},
            "warmup_counts": warmup_counts,
            "truncated": bool(reasons), "truncation_reasons": reasons,
            "limited_depth": 5, "sequence_kind": "source_row_ordinal",
            "mark_start": mark_start, "mark_end": mark_end,
            "mark_definition": "mean of each venue's latest visible best-bid/ask midpoint; valuation only, not execution price",
            "limitations": [
                "Vendor receive timestamps describe Tardis capture infrastructure, not the user's network.",
                "Only the known top five levels per side are present; deeper execution cannot be inferred.",
                "Tardis reconstructs snapshots and removes crossed levels; CSV does not include disconnect events.",
                "A missing exchange timestamp stays null; provider timestamp fallback values are retained as supplied.",
                "Sequence is a source row ordinal, not an exchange update ID; equal-millisecond ordering uses recv_us.",
                "Receive times round up to each 1ms bucket end for decisions; recv_us is retained, but no submillisecond execution is claimed.",
                "Only the consumed CSV prefix is validated; event caps and source coverage can shorten the requested window.",
                "Monthly first-day samples cannot establish representative long-run or achievable live performance.",
                "Raw and reformatted records remain private under the provider licence; publish permitted derived research only.",
            ],
        }
        return events, metadata
