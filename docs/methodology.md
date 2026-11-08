# Replay methodology

This project asks how an apparent BTC/USDT cross-venue spread changes when execution costs, visible depth, latency, inventory and evidence quality are modelled explicitly. It combines constructed mechanism experiments with an optional historical sample adapter. Historical prices do not turn simulated fills into observed exchange executions.

The implementation is in [engine.py](../crossvenue/engine.py); presets and constructed scenarios are in [data.py](../crossvenue/data.py). Monetary calculations use Python `Decimal`; JSON exports represent monetary amounts and BTC quantities as strings.

## Five comparison models

| Model | Price and depth assumption | Entry signal | Fees | Extra order latency |
| --- | --- | --- | --- | --- |
| `screen` | Optimistic top price | Gross edge | Zero | Zero |
| `fees` | Optimistic top price | Gross edge | Configured taker fees | Zero |
| `depth` | Quantity-dependent visible L2 | Gross edge | Configured taker fees | Zero |
| `latency` | Quantity-dependent visible L2 | Gross edge | Configured taker fees | Configured |
| `guarded` | Quantity-dependent visible L2 | Net edge | Configured taker fees | Configured |

`guarded` additionally enables quote age/skew gates and a halt after excessive residual BTC. The other presets disable those optional controls. Every preset retains separate venue balances, spot-only inventory constraints, malformed-event rejection and venue quarantine. An execution state that becomes unknown blocks further orders in every preset.

The top-price assumption enlarges each snapshot's best-level capacity to at least one target order, then consumes that capacity. It does not grant an unlimited reusable book. This is an intentionally optimistic comparison, not a claim about available exchange liquidity.

The six constructed scenarios use a one-pair submission budget so their mechanisms can be inspected separately. A replay can use another explicit `max_pairs`; this limits submitted pairs, not candidate observations. Different models may reject different candidates and execute different numbers of trades. Their result differences are therefore not a strict additive attribution of fees, depth and latency to an identical trade list.

## Causal decisions and IOC execution

1. Process snapshots in receive-time order. A same-millisecond batch is available at the end of its declared clock bucket; all its messages are received before decisions at that time.
2. Compare both trading directions once for each new pair of accepted book versions. A candidate requires enough known depth for the target quantity on both legs, enough buying-venue cash including fees, and enough selling-venue BTC.
3. Choose the feasible direction with the best expected edge in basis points of buy notional. The selected gross or net edge must strictly exceed `min_edge_bps`.
4. Fix each IOC limit at the worst decision-time price needed for its leg. After configured latency, attempt both legs against the latest usable snapshots already received by that time.
5. Record each leg independently. A price change or thin book may produce a partial fill, one filled leg or a known zero fill. There is no atomic assumption that both legs succeed.

L2 execution consumes available levels inside the fixed IOC limit. Consumed virtual depth remains depleted until a newer accepted snapshot replaces that venue's book. That reset is itself a replay assumption: later historical snapshots were not generated in response to this simulated trader's earlier orders.

Only one pair can be pending at a time. Repeated or older sequence values do not reset a book. New malformed snapshots and comparable event-clock regressions quarantine the venue; receipt of a newer row is not by itself proof of a newer exchange state. See the [data contract](data_contract.md).

## Residual risk and uncertain outcomes

The residual is total BTC minus initial total BTC. After both same-time leg attempts, the engine records its signed ending value and absolute peak. It does not treat the arbitrary internal bookkeeping order of two simultaneous attempts as an observed intramillisecond exposure path.

When the residual exceeds `max_unhedged_btc`, the enabled risk control halts new pairs. It does not sell existing inventory, hedge the residual or remove its price risk. Unhedged duration measures any nonzero residual through the observed replay horizon; it is separate from the halt threshold.

| Outcome | What the engine knows |
| --- | --- |
| Known `no_fill` | Usable execution-time depth exists within the model, but that IOC leg obtains zero quantity. |
| `execution_unknown` | The pair was submitted, but execution-time evidence fails local quality checks. This does not establish that the exchange cancelled or rejected it. |
| `censored` | Scheduled arrival lies beyond retained data coverage. The execution outcome has not been observed. |

Unknown and censored legs have `execution_ms=null` and unknown quantity/notional/fee fields, while preserving scheduled and last-observed times. They are separate pair categories and are not included in the known no-fill count. The engine does not invent fills or fees for them and does not implement an exchange query to resolve their state.

Any such pair sets `valuation_status=incomplete_execution_evidence`. Remaining balances and PnL then describe only the known-fill ledger; they are not established final portfolio performance or exposure. Without those unknown outcomes the status is `complete_within_model`, which still means a simulated result under the declared assumptions.

中文学习提示：停止新增交易不会自动消除单腿风险。已提交订单遇到行情不可信，也不能被本地程序直接宣布为“未成交”。

## Accounting and the holding benchmark

Let `C0` and `B0` be initial total USDT and BTC; let `CT` and `BT` be final ledger totals. All venue cash balances already include executed fees. `m0` and `mT` are explicit common initial and terminal marks.

```text
initial_equity         = C0 + B0 * m0
ending_equity          = CT + BT * mT
hold_benchmark         = C0 + B0 * mT
execution_contribution = (CT - C0) + (BT - B0) * mT
inventory_pnl          = B0 * (mT - m0)
total_pnl              = inventory_pnl + execution_contribution
```

Fees must not be deducted again from execution contribution. Equal BTC fills with USDT fees preserve total BTC, including the original directional inventory. A profitable execution contribution can coexist with a losing total portfolio when BTC falls.

Intermediate curves use a common received-book midpoint proxy. The terminal mark is explicitly supplied; the historical adapter derives its marks from visible venue midpoints. Neither mark is a liquidation quote. Exit depth, exit fees and venue availability are not included. The identities above remain ledger identities when execution evidence is incomplete, but do not establish the missing economic outcome.

## Interpretation limits

This version has no maker orders, queue model, exchange lot-size validation, transfers, borrowing, funding payments, margin liquidation, credit-loss model or automatic hedge. The halt controls residual BTC rather than implementing a complete institutional risk framework. A short vendor sample and constructed experiments do not establish a persistent arbitrage opportunity, live returns or an annualized Sharpe ratio. Tests check declared execution and accounting behaviour; they do not certify profitability.
