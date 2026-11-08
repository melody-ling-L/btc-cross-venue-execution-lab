# Explaining the project in an interview

The replay engine and these materials were drafted with AI assistance. The learner completed earlier fee, BTC quantity, gross/net SQL and data-state exercises with guided review. Those exercises do not establish independent authorship or full understanding of the newer engine. Before presenting it, trace its decisions, explain the assumptions and make a small change without copying an answer.

A defensible project description is: “I am studying when a visible cross-venue spread survives explicit execution and risk assumptions. The project separates inventory returns from execution contribution and keeps unknown order outcomes visible.” Describe which functions and tests you personally understand or changed. Do not present simulated gains as live trading results.

## 1 Why can a positive spread lose money

Start with the actual buy ask, sell bid and target quantity. Subtract both taker fees and use quantity-dependent depth rather than two last-trade prices. Entry filtering and cash accounting are separate: the gross-screening models ignore fees in the signal, but still pay the configured fees in their ledgers.

Inspect [`_preview` and `_consider`](../crossvenue/engine.py), [`fee_trap` and `depth_trap`](../crossvenue/data.py), and the fee/depth regression in [test_replay_engine.py](../tests/test_replay_engine.py).

Exercise: buy 0.02 BTC at 100000 and sell it at 100100, with 0.1% USDT fees on both legs. Explain every cash flow and why a model with no fees produces a different result.

## 2 What prevents future information from entering a decision

Distinguish exchange event time from the source's receive time. The replay follows receipt; all messages in a declared millisecond bucket are available at its endpoint. Changing a later snapshot must not change an earlier decision. A venue row ordinal is not proof that its event clock advanced.

Inspect [`_receive` and `run`](../crossvenue/engine.py), the clock conversion in [tardis.py](../crossvenue/tardis.py), and the future-change and event-clock-regression tests. Explain why an event-time regression quarantines the venue even when its source sequence increases.

中文提示：先说“当时已经收到什么”，再说“当时决定做什么”；不要用下一条报价解释上一条决策。

## 3 What happens if only one leg fills

IOC limits are fixed using decision-time depth, but execution uses the latest usable depth received by arrival time. Each leg can fill a different quantity. Separate venue cash and BTC change independently; no atomic dual-fill guarantee exists.

Inspect [`_fill_leg`, `_execute` and `_risk`](../crossvenue/engine.py). Explain signed residual BTC, its absolute peak, and why the halt merely prevents new pairs. Existing unmatched BTC remains exposed to price changes. The v1 engine does not automatically hedge it.

Exercise: 0.02 BTC buys but only 0.005 BTC sells. What residual remains, and what does a halt fail to solve? Explain why observing the pair after both same-time attempts does not measure intramillisecond leg latency.

## 4 Why is total capital insufficient for a trade

The buyer needs cash including its own fees, and the seller needs BTC at its own venue. BTC held at the buying venue cannot fund a spot sale at the other venue. All five models retain these constraints, including the optimistic screen model.

Inspect [`_consider`](../crossvenue/engine.py) and the [`venue_inventory` fixture](../crossvenue/data.py). Show both rejected directions and their reasons. Explain why an infeasible profitable direction should not disappear from diagnostics merely because its reverse direction is feasible but unprofitable.

## 5 Which part of PnL came from execution

Use the holding benchmark rather than treating the initial BTC inventory as free. With total cash `C`, BTC `B` and terminal common mark `mT`:

```text
execution contribution = (CT - C0) + (BT - B0) * mT
inventory PnL          = B0 * (mT - m0)
total PnL              = inventory PnL + execution contribution
```

Fees are already in cash. Subtracting them again double-counts costs. A common mark is a valuation device rather than a promise that both venues can liquidate there.

Inspect the summary calculation in [`run`](../crossvenue/engine.py), [methodology](methodology.md), and the ledger-identity test. Explain how positive execution contribution and negative total PnL can coexist.

## 6 Why are unknown outcomes different from no fills

A known zero fill requires sufficient execution-time evidence within the model. A submitted pair with stale or otherwise unusable evidence becomes `execution_unknown`; a pair arriving beyond retained data becomes `censored`. Neither proves rejection, cancellation or zero fees.

Inspect [`_unobserved_pair`](../crossvenue/engine.py) and its regression tests. Unknown quantities are `null`, not zero. Explain why v1 blocks subsequent submissions and labels its remaining ledger `incomplete_execution_evidence` instead of calling that ledger confirmed final performance.

Exercise: local quote quality fails after submission. What evidence is missing, and why can a local guard not retroactively cancel the submitted orders? This engine does not implement the exchange query needed to resolve that question.

## 7 What does the evidence support and what remains to learn

The six synthetic scenarios isolate mechanisms; they were constructed to demonstrate them. A limited historical vendor sample supplies real observations but still uses assumed fees, extra latency and simulated fills. Filtering also changes which trades occur, so five model results are not a strict cost waterfall for one fixed order list.

Inspect [data provenance](data_contract.md), [model presets](../crossvenue/data.py) and [tests](../tests/test_replay_engine.py). Explain sampling limits, absent disconnect evidence, limited visible depth and the reset of simulated depth on later snapshots. No short sample establishes persistent alpha, achievable live returns or an annualized Sharpe ratio.

Before claiming mastery, independently modify one assumption, predict the result, run the relevant test and explain a failed prediction. Suitable exercises are changing the edge threshold, delaying one book before IOC arrival, or designing a counterexample to an unsafe no-fill assumption. Keep authorship and learning progress specific; uploading a working repository is not evidence of understanding it.
