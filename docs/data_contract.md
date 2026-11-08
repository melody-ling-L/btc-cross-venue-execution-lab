# Market data contract

The replay input is JSONL: one object per full snapshot of the source's declared visible depth. It is not an incremental update stream. For a five-level source, “full” means the supplied top five levels, not the entire exchange order book. The replay core reads supplied objects without network access; the optional [Tardis adapter](../crossvenue/tardis.py) downloads or reads sample archives separately.

## Normalized fields

| Field | Meaning |
| --- | --- |
| `venue` | `alpha` or `beta`, each with independent initial balances. |
| `symbol` | `BTCUSDT`; this version models spot BTC and USDT only. |
| `event_ms` | Nonnegative event-time integer in milliseconds, or `null` if unavailable. Presence alone does not prove an independent exchange clock. |
| `recv_ms` | Nonnegative millisecond timestamp at which the declared receive bucket is fully available. |
| `sequence` | Nonnegative per-venue ordering identifier. It must advance to replace accepted or quarantined state; continuity by increments of one is not required. |
| `bids` / `asks` | Arrays of `[price_string, btc_quantity_string]`; positive finite prices and nonnegative finite quantities. Bids descend and asks ascend, with unique prices within a side. |
| `source_kind` | `synthetic` or `tardis_l5_sample`. These labels remain separate in research outputs. |
| `timing_basis` | `synthetic_receive` for fixtures; `vendor_receive` for imported vendor data. |

Synthetic venue names are abstract. Only the real-data adapter maps `alpha` to Binance spot and `beta` to Bybit spot. Its additional provenance fields include `recv_us`, `sequence_kind`, vendor name and session; these do not turn the source row ordinal into an exchange update sequence.

The engine rejects malformed new snapshots and crossed books. The adapter applies additional CSV checks, including consistent exchange/symbol, expected headers, ordered levels and usable two-sided books for valuation. Input validation is not a claim that historical displayed liquidity was executable by this trader.

## Vendor timestamps and ordering

Tardis documents UTC epoch microseconds, reconstructed top-five snapshots, and a `local_timestamp` arrival clock. Its `timestamp` field may use arrival time as a fallback when an exchange timestamp is unavailable. The adapter retains that supplied value; a truly missing value stays `null`. CSV presence cannot independently distinguish a provider fallback from a genuine exchange timestamp. [Official data types](https://docs.tardis.dev/downloadable-csv-files/data-types).

The implemented conversions are:

```text
recv_ms  = ceil(local_timestamp_us / 1000)
event_ms = floor(timestamp_us / 1000), or null
```

Original receive microseconds are retained for deterministic merge ordering. Rounding receipt upward makes all messages in a millisecond bucket available at its endpoint before the engine decides there. It avoids treating a later submillisecond receipt as known at the beginning of that millisecond. The engine does not claim submillisecond execution precision or recreate the user's own network latency.

The importer merges source receive times, not exchange clocks. `sequence` is a source-file row ordinal, not an exchange continuity guarantee. It preserves only a bounded overlapping window, with a prior observed book where needed for warmup. Metadata records examined rows, retained counts, event caps and coverage truncation. Validation covers the consumed prefix and possible lookahead, not every row of an unconsumed archive.

## Freshness and quarantine

With `require_event_time=True`, a missing event clock prevents trading. Explicitly setting it false permits receive-only age/skew assumptions; it does not prove synchronized exchange states. Optional quality gates check snapshot age and cross-venue clock skew. Event times later than receipt, structural damage and quarantine cannot be bypassed by disabling those optional gates.

A new higher-sequence snapshot whose comparable event time moves below the previous trusted watermark records `event_clock_regression` and quarantines that venue. A malformed new full snapshot also quarantines it rather than allowing continued trading from the old book. Recovery requires a newer valid snapshot and, when a trusted event clock exists, a known event time at least as new as that watermark. Duplicate and older sequences are ignored without resetting depth or inferring a lost packet from a sequence jump.

The vendor CSV does not provide disconnect-event messages, and its snapshots are reconstructed representations. This adapter therefore cannot prove uninterrupted feed health or infer all missed updates. A quarantine is a conservative model response, not proof that the exchange malfunctioned. [Official data types](https://docs.tardis.dev/downloadable-csv-files/data-types); [API documentation entry](https://docs.tardis.dev/api).

中文学习提示：文件里有新行，不等于交易所状态一定更新；`event_ms` 非空，也不自动证明它是独立的交易所时钟。

## Storage and publication

Raw vendor archives, normalized JSONL and private source manifests stay under gitignored `data/`. Do not commit or embed those records in issues, documentation, charts or public trace exports. Repository examples may contain constructed synthetic ticks, clearly identified as synthetic.

Public non-synthetic outputs contain rounded aggregate research and provenance, not raw or merely reformatted ticks, decisions, fill traces or event-level curves. They omit per-venue balances, equity, marks, inventory PnL and total PnL. Published execution contribution and fees round to 1 USDT; extra BTC rounds to 0.001 BTC. A rounded zero is not an exact zero. Actual resolved model parameters and aggregate counts remain visible. Exact accounting identities are checked locally before export; public audit entries disclose PASS/FAIL, not exact residuals or an independently reproducible full ledger. Synthetic experiments retain exact ledger evidence.

The project is not a hosted market-data, replay or backtesting service. Its source code does not grant rights to redistribute a provider's data. Check the applicable licence before publication; the project's aggregate-only policy is not a blanket legal determination that every derived output is permitted. [Tardis terms of service](https://docs.tardis.dev/legal/terms-of-service).

No company strategies, private account data, internal alerts or credentials are required by this contract.
