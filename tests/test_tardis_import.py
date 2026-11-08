"""Synthetic CSV fixtures test vendor-clock causality without network access."""

import csv
import gzip
from io import BytesIO
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from crossvenue.tardis import download_samples, import_l5


FIELDS = ["exchange", "symbol", "timestamp", "local_timestamp"] + [
    f"{side}[{level}].{field}"
    for level in range(5)
    for side in ("asks", "bids")
    for field in ("price", "amount")
]


def row(exchange, received, *, event=None, bid="99", ask="101"):
    result = {field: "" for field in FIELDS}
    result.update(exchange=exchange, symbol="BTCUSDT", local_timestamp=str(received),
                  timestamp="" if event is None else str(event))
    result.update({"bids[0].price": bid, "bids[0].amount": "2",
                   "asks[0].price": ask, "asks[0].amount": "3"})
    return result


def write_sample(directory, name, rows, fields=FIELDS):
    path = Path(directory) / f"{name}.csv.gz"
    with gzip.open(path, "wt", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    return path


class TardisImportTests(unittest.TestCase):
    def test_units_microsecond_order_warmup_and_marks_are_causal(self):
        with tempfile.TemporaryDirectory() as directory:
            alpha = write_sample(directory, "a", [
                row("binance", 1_000_000, event=999_999),
                row("binance", 2_000_200, bid="101", ask="103"),
                row("binance", 2_000_800, bid="103", ask="105"),
                row("binance", 3_000_000),
            ])
            beta = write_sample(directory, "b", [
                row("bybit-spot", 2_000_100, event=1_900_999, bid="199", ask="201"),
                row("bybit-spot", 2_000_500, bid="201", ask="203"),
                row("bybit-spot", 3_000_000),
            ])
            events, meta = import_l5([beta, alpha], window_seconds=1)
        self.assertEqual([e["venue"] for e in events[:5]], ["alpha", "beta", "alpha", "beta", "alpha"])
        self.assertEqual(events[0]["event_ms"], 999)
        self.assertIsNone(events[2]["event_ms"])
        self.assertEqual(events[1]["recv_ms"], events[2]["recv_ms"])
        self.assertEqual(events[1]["recv_ms"], 2001)
        self.assertTrue(all(event["recv_us"] <= event["recv_ms"] * 1000 for event in events))
        self.assertEqual(events[0]["recv_ms"], 1000)
        self.assertEqual(events[2]["sequence"], 2)
        self.assertEqual(events[0]["bids"], [["99", "2"]])
        self.assertEqual(meta["warmup_counts"], {"alpha": 1, "beta": 0})
        self.assertEqual(meta["start_us"], 2_000_100)
        self.assertEqual(meta["start_ms"], 2001)
        self.assertEqual(meta["requested_end_ms"], 3001)
        self.assertTrue(all(event["recv_ms"] <= meta["end_ms"] for event in events))
        self.assertEqual(meta["mark_start"], "150")
        self.assertEqual(meta["mark_end"], "100")
        self.assertEqual(meta["timing_basis"], "vendor_receive")
        self.assertEqual(meta["clock_quantization"], "ceil_to_ms_bucket_end")
        self.assertEqual(meta["sequence_kind"], "source_row_ordinal")

    def test_backwards_submillisecond_receive_time_and_damaged_prices_fail(self):
        with tempfile.TemporaryDirectory() as directory:
            beta = write_sample(directory, "b", [row("bybit-spot", 1_000_000), row("bybit-spot", 3_000_000)])
            cases = [
                [row("binance", 1_000_900), row("binance", 1_000_800)],
                [row("binance", 1_000_000, bid="NaN")],
                [dict(row("binance", 1_000_000), **{"asks[0].amount": ""})],
                [dict(row("binance", 1_000_000), **{"asks[1].price": "100", "asks[1].amount": "1"})],
            ]
            for index, rows in enumerate(cases):
                with self.subTest(index=index):
                    alpha = write_sample(directory, f"a{index}", rows)
                    with self.assertRaises(ValueError):
                        import_l5([alpha, beta])
            missing = write_sample(directory, "missing", [], fields=FIELDS[:-1])
            with self.assertRaisesRegex(ValueError, "header"):
                import_l5([missing, beta])

    def test_cap_declares_shortened_horizon_and_keeps_both_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            alpha = write_sample(directory, "a", [row("binance", i * 1_000_000) for i in range(1, 8)])
            beta = write_sample(directory, "b", [row("bybit-spot", i * 1_000_000 + 100) for i in range(1, 8)])
            events, meta = import_l5([alpha, beta], window_seconds=5, max_events=4)
        self.assertEqual(len(events), 4)
        self.assertTrue(meta["truncated"])
        self.assertIn("max_events", meta["truncation_reasons"])
        self.assertEqual(meta["end_us"], events[-1]["recv_us"])
        self.assertLess(meta["end_ms"], meta["requested_end_ms"])
        self.assertEqual(set(meta["record_counts"]), {"alpha", "beta"})

    def test_joint_coverage_ends_at_shorter_source_and_nonoverlap_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            alpha = write_sample(directory, "a", [row("binance", i * 1_000_000) for i in range(1, 6)])
            beta = write_sample(directory, "b", [row("bybit-spot", i * 1_000_000) for i in range(2, 4)])
            events, meta = import_l5([alpha, beta], window_seconds=10)
            self.assertEqual(meta["start_us"], 2_000_000)
            self.assertEqual(meta["end_us"], 3_000_000)
            self.assertTrue(meta["truncated"])
            self.assertEqual(meta["truncation_reasons"], ["source_coverage_ended"])
            self.assertLessEqual(max(e["recv_us"] for e in events), 3_000_000)
            beta = write_sample(directory, "later", [row("bybit-spot", 6_000_000)])
            with self.assertRaisesRegex(ValueError, "does not overlap"):
                import_l5([alpha, beta])

    def test_completed_requested_window_is_not_a_complete_day_claim(self):
        with tempfile.TemporaryDirectory() as directory:
            alpha = write_sample(directory, "a", [row("binance", i * 1_000_000) for i in range(1, 6)])
            beta = write_sample(directory, "b", [row("bybit-spot", i * 1_000_000) for i in range(1, 6)])
            events, meta = import_l5([alpha, beta], window_seconds=1)
            self.assertFalse(meta["truncated"])
            self.assertEqual(meta["end_us"], 2_000_000)
            self.assertEqual(meta["limited_depth"], 5)
            self.assertEqual(meta["record_counts"], {"alpha": 2, "beta": 2})
            self.assertEqual(events[0]["source_kind"], "tardis_l5_sample")
            with self.assertRaises(ValueError):
                import_l5([alpha, alpha])

    def test_non_sample_dates_are_rejected_before_any_download(self):
        with tempfile.TemporaryDirectory() as directory:
            for day in ("2026-11-02", "2026-13-01", "2026-1-01", "https://example.com"):
                with self.subTest(day=day), self.assertRaises(ValueError):
                    download_samples(day, Path(directory))
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_download_budget_and_http_failure_leave_no_partial_files(self):
        class Response(BytesIO):
            status = 200
            headers = {}

        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory)
            with patch("crossvenue.tardis.urlopen", return_value=Response(gzip.compress(b"synthetic\n"))):
                with self.assertRaisesRegex(ValueError, "max_bytes"):
                    download_samples("2026-10-01", destination, max_bytes=3)
            self.assertEqual(list(destination.iterdir()), [])
            with patch("crossvenue.tardis.urlopen", side_effect=HTTPError("https://datasets.tardis.dev", 403, "Forbidden", {}, None)):
                with self.assertRaisesRegex(RuntimeError, "HTTP 403"):
                    download_samples("2026-10-01", destination)
            self.assertEqual(list(destination.iterdir()), [])

    def test_completed_first_file_is_reused_after_second_file_timeout(self):
        class Response(BytesIO):
            status = 200
            headers = {}

        first_bytes = gzip.compress(b"synthetic alpha cache\n")
        second_bytes = gzip.compress(b"synthetic beta cache\n")
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory)
            first = destination / "binance_book_snapshot_5_2026-10-01_BTCUSDT.csv.gz"
            second = destination / "bybit-spot_book_snapshot_5_2026-10-01_BTCUSDT.csv.gz"
            with patch("crossvenue.tardis.urlopen", side_effect=[Response(first_bytes), TimeoutError("synthetic timeout")]) as opening:
                with self.assertRaises(TimeoutError):
                    download_samples("2026-10-01", destination)
                self.assertEqual(opening.call_count, 2)
            self.assertEqual(first.read_bytes(), first_bytes)
            self.assertFalse(second.exists())
            self.assertEqual(list(destination.glob("*.part")), [])
            with patch("crossvenue.tardis.urlopen", return_value=Response(second_bytes)) as opening:
                paths = download_samples("2026-10-01", destination, timeout=120)
                opening.assert_called_once()
                request = opening.call_args.args[0]
                self.assertEqual(request.full_url, "https://datasets.tardis.dev/v1/bybit-spot/book_snapshot_5/2026/10/01/BTCUSDT.csv.gz")
                self.assertEqual(opening.call_args.kwargs["timeout"], 120)
            self.assertEqual(paths, [first, second])
            self.assertEqual(first.read_bytes(), first_bytes)
            self.assertEqual(second.read_bytes(), second_bytes)
            with patch("crossvenue.tardis.urlopen") as opening:
                self.assertEqual(download_samples("2026-10-01", destination), paths)
                opening.assert_not_called()

    def test_corrupt_cache_and_existing_partial_are_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory)
            first = destination / "binance_book_snapshot_5_2026-10-01_BTCUSDT.csv.gz"
            corrupt = gzip.compress(b"synthetic alpha\n")[:-4]
            first.write_bytes(corrupt)
            with patch("crossvenue.tardis.urlopen") as opening:
                with self.assertRaisesRegex(ValueError, "gzip/CRC"):
                    download_samples("2026-10-01", destination)
                opening.assert_not_called()
            self.assertEqual(first.read_bytes(), corrupt)
            first.unlink()
            partial = first.with_suffix(first.suffix + ".part")
            partial.write_bytes(b"another writer owns this")
            with patch("crossvenue.tardis.urlopen") as opening:
                with self.assertRaisesRegex(FileExistsError, "partial"):
                    download_samples("2026-10-01", destination)
                opening.assert_not_called()
            self.assertEqual(partial.read_bytes(), b"another writer owns this")


if __name__ == "__main__":
    unittest.main()
