"""Causal, receive-time replay of synthetic full-depth snapshots.

All assets and IOC orders are local simulations. Decimal accounting is separate
from common-price valuation; a common mark is never a promised liquidation price.
"""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
from decimal import Decimal, InvalidOperation
from itertools import groupby


D = Decimal
ZERO = D("0")
VENUES = ("alpha", "beta")


def _decimal(value, name, *, positive=False):
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a finite number")
    try:
        number = D(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError(f"{name} must be a finite number") from exc
    if not number.is_finite() or number < 0 or (positive and number == 0):
        raise ValueError(f"{name} must be finite and {'positive' if positive else 'nonnegative'}")
    return number


def _integer(value, name):
    if type(value) is not int or value < 0:
        raise ValueError(f"{name} must be a nonnegative integer")
    return value


def _json(value):
    if isinstance(value, D):
        return format(value, "f")
    if isinstance(value, dict):
        return {str(k): _json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json(v) for v in value]
    return value


class _Replay:
    def __init__(self, events, config, scenario, model):
        if not isinstance(events, list) or not isinstance(config, dict):
            raise ValueError("events must be a list and config an object")
        self.scenario, self.model = scenario, model
        self.qty = _decimal(config.get("order_qty", "0.02"), "order_qty", positive=True)
        self.mark_start = _decimal(config.get("mark_start", "100000"), "mark_start", positive=True)
        self.mark_end = _decimal(config.get("mark_end", self.mark_start), "mark_end", positive=True)
        self.latency = _integer(config.get("latency_ms", 100), "latency_ms")
        self.max_age = _integer(config.get("max_quote_age_ms", 250), "max_quote_age_ms")
        self.max_skew = _integer(config.get("max_event_skew_ms", 100), "max_event_skew_ms")
        self.max_unhedged = _decimal(config.get("max_unhedged_btc", "0.005"), "max_unhedged_btc")
        self.max_pairs = config.get("max_pairs")
        if self.max_pairs is not None:
            _integer(self.max_pairs, "max_pairs")
        self.min_edge = _decimal(config.get("min_edge_bps", "0"), "min_edge_bps")
        self.depth_mode = config.get("depth_mode", "l2")
        self.policy = config.get("signal_policy", "net")
        if self.depth_mode not in {"l2", "top"} or self.policy not in {"net", "gross"}:
            raise ValueError("depth_mode must be l2/top; signal_policy must be net/gross")
        for key in ("quality_gates", "risk_halt", "require_event_time"):
            if type(config.get(key, True)) is not bool:
                raise ValueError(f"{key} must be boolean")
        self.gates = config.get("quality_gates", True)
        self.enable_halt = config.get("risk_halt", True)
        self.require_event_time = config.get("require_event_time", True)
        fees = config.get("fee_bps", {v: "10" for v in VENUES})
        self.rates = {v: _decimal(fees[v], f"fee_bps.{v}") / 10000 for v in VENUES}
        if any(rate >= 1 for rate in self.rates.values()):
            raise ValueError("fee_bps must be below 10000")
        supplied = config.get("initial_balances", config.get("balances", {
            v: {"usdt": "10000", "btc": "0.1"} for v in VENUES
        }))
        self.initial = {v: {asset: _decimal(supplied[v][asset], f"{v}.{asset}")
                            for asset in ("usdt", "btc")} for v in VENUES}
        self.balances = deepcopy(self.initial)
        self.initial_cash = sum((a["usdt"] for a in self.initial.values()), ZERO)
        self.initial_btc = sum((a["btc"] for a in self.initial.values()), ZERO)
        self.books, self.evaluated = {}, set()
        self.sequence_watermarks = {}
        self.event_watermarks = {}
        self.quarantined = {}
        self.pending = None
        self.halted = False
        self.execution_blocked = False
        self.valuation_incomplete = False
        self.risk_start = None
        self.max_duration = 0
        self.max_extra = ZERO
        self.decisions, self.fills, self.curve, self.quality = [], [], [], []
        self.rejected = Counter()
        self.stats = Counter(candidates=0, submitted_pairs=0, round_trips_complete=0,
                             partial_pairs=0, single_leg_pairs=0, no_fill_pairs=0,
                             censored_pairs=0, execution_unknown_pairs=0, risk_halts=0)
        self.fees = ZERO
        self.events = []
        for event in events:
            if not isinstance(event, dict) or type(event.get("recv_ms")) is not int or event["recv_ms"] < 0:
                self._reject(None, event.get("venue") if isinstance(event, dict) else None,
                             "invalid_receive_timestamp")
                continue
            self.events.append(deepcopy(event))
        self.events.sort(key=lambda item: item["recv_ms"])
        kinds = {e.get("source_kind") for e in self.events if isinstance(e.get("source_kind"), str)}
        self.source_kind = next(iter(kinds)) if len(kinds) == 1 else "mixed" if kinds else "synthetic"
        self.end_ms = self.events[-1]["recv_ms"] if self.events else 0
        self.start_ms = self.events[0]["recv_ms"] if self.events else 0

    def _reject(self, now, venue, reason):
        self.rejected[reason] += 1
        self.quality.append({"recv_ms": now, "venue": venue, "reason": reason})

    def _receive(self, event):
        now, venue = event["recv_ms"], event.get("venue")
        try:
            if venue not in VENUES or event.get("symbol") != "BTCUSDT":
                raise ValueError("unsupported_venue_or_symbol")
            sequence = _integer(event.get("sequence"), "sequence")
            previous_sequence = self.sequence_watermarks.get(venue)
            if previous_sequence is not None and sequence <= previous_sequence:
                reason = "duplicate_sequence" if sequence == previous_sequence else "old_sequence"
                self._reject(now, venue, reason)
                return
            self.sequence_watermarks[venue] = sequence
            allowed_timing = {"synthetic": "synthetic_receive", "tardis_l5_sample": "vendor_receive"}
            if allowed_timing.get(event.get("source_kind")) != event.get("timing_basis") or event.get("source_kind") not in allowed_timing:
                raise ValueError("unsupported_source_or_timing_basis")
            event_ms = event.get("event_ms")
            if event_ms is not None:
                _integer(event_ms, "event_ms")
                if event_ms > now:
                    raise ValueError("future_event_timestamp")
                if event_ms < self.event_watermarks.get(venue, event_ms):
                    raise ValueError("event_clock_regression")
            if (venue in self.quarantined and venue in self.event_watermarks
                    and event_ms is None):
                raise ValueError("event_clock_recovery_unverified")
            levels = {}
            for side in ("bids", "asks"):
                raw = event.get(side)
                if not isinstance(raw, list):
                    raise ValueError("invalid_depth_snapshot")
                parsed = []
                for level in raw:
                    if not isinstance(level, (list, tuple)) or len(level) != 2:
                        raise ValueError("invalid_depth_level")
                    parsed.append([_decimal(level[0], "price", positive=True),
                                   _decimal(level[1], "quantity")])
                prices = [p for p, _ in parsed]
                if any((a <= b if side == "bids" else a >= b)
                       for a, b in zip(prices, prices[1:])):
                    raise ValueError("unordered_or_duplicate_price_levels")
                levels[side] = parsed
            if levels["bids"] and levels["asks"] and levels["bids"][0][0] > levels["asks"][0][0]:
                raise ValueError("crossed_book")
            if self.depth_mode == "top":
                # Explicit optimistic assumption: top price covers at least one target order.
                levels = {side: [[rows[0][0], max(rows[0][1], self.qty)]] if rows else []
                          for side, rows in levels.items()}
            self.books[venue] = {"sequence": sequence, "event_ms": event_ms, "recv_ms": now, **levels}
            if event_ms is not None:
                self.event_watermarks[venue] = event_ms
            self.quarantined.pop(venue, None)
        except (ValueError, TypeError, KeyError, InvalidOperation) as exc:
            if venue in VENUES:
                self.quarantined[venue] = str(exc)
            self._reject(now, venue, str(exc))

    def _quality_problem(self, now):
        if self.quarantined:
            return "quarantined_venue"
        if any(v not in self.books for v in VENUES):
            return "missing_venue"
        if self.require_event_time and any(self.books[v]["event_ms"] is None for v in VENUES):
            return "unknown_event_timestamp"
        if self.gates:
            for v in VENUES:
                book = self.books[v]
                clock = book["event_ms"] if book["event_ms"] is not None else book["recv_ms"]
                if now - clock > self.max_age or now - book["recv_ms"] > self.max_age:
                    return "stale_quote"
            clocks = [self.books[v]["event_ms"] if self.books[v]["event_ms"] is not None
                      else self.books[v]["recv_ms"] for v in VENUES]
            if abs(clocks[0] - clocks[1]) > self.max_skew:
                return "cross_venue_time_skew"
        return None

    def _preview(self, venue, side):
        levels = self.books[venue]["asks" if side == "buy" else "bids"]
        filled, notional, worst = ZERO, ZERO, None
        for price, available in levels:
            take = min(self.qty - filled, available)
            if take > 0:
                filled += take
                notional += take * price
                worst = price
            if filled == self.qty:
                break
        return filled, notional, worst

    def _consider(self, now):
        if self.pending is not None:
            return
        if any(v not in self.books for v in VENUES):
            self._reject(now, None, "missing_venue")
            return
        version = tuple(self.books[v]["sequence"] for v in VENUES)
        if version in self.evaluated:
            return
        self.evaluated.add(version)
        self.stats["candidates"] += 1
        if self.execution_blocked:
            self._reject(now, None, "unresolved_execution_state")
            return
        if self.halted:
            self._reject(now, None, "risk_halted")
            return
        if self.max_pairs is not None and self.stats["submitted_pairs"] >= self.max_pairs:
            self._reject(now, None, "pair_budget")
            return
        problem = self._quality_problem(now)
        if problem:
            self._reject(now, None, problem)
            return
        plans = []
        for buy, sell in (("alpha", "beta"), ("beta", "alpha")):
            bqty, cost, buy_limit = self._preview(buy, "buy")
            sqty, proceeds, sell_limit = self._preview(sell, "sell")
            if bqty < self.qty or sqty < self.qty:
                self._reject(now, None, "insufficient_known_depth")
                continue
            fee = cost * self.rates[buy] + proceeds * self.rates[sell]
            cash_ok = cost * (1 + self.rates[buy]) <= self.balances[buy]["usdt"]
            inventory_ok = self.balances[sell]["btc"] >= self.qty
            if not cash_ok:
                self._reject(now, buy, "insufficient_buy_cash")
            if not inventory_ok:
                self._reject(now, sell, "insufficient_sell_inventory")
            if not cash_ok or not inventory_ok:
                continue
            gross = proceeds - cost
            net = gross - fee
            score = gross if self.policy == "gross" else net
            plans.append({"buy_venue": buy, "sell_venue": sell, "qty": self.qty,
                          "expected_gross_usdt": gross, "expected_fees_usdt": fee,
                          "expected_net_usdt": net, "buy_limit": buy_limit,
                          "sell_limit": sell_limit, "score_bps": score / cost * 10000})
        if not plans:
            return
        plan = max(plans, key=lambda p: p["score_bps"])
        decision = {"decision_ms": now, **plan}
        if plan["score_bps"] <= self.min_edge:
            decision.update(action="rejected", reason="edge_not_above_minimum")
            self.rejected["edge_not_above_minimum"] += 1
        else:
            self.stats["submitted_pairs"] += 1
            pair_id = self.stats["submitted_pairs"]
            decision.update(action="submitted", reason="edge_above_minimum", pair_id=pair_id)
            self.pending = {**plan, "pair_id": pair_id, "decision_ms": now,
                            "due_ms": now + self.latency}
        self.decisions.append(decision)

    def _fill_leg(self, pending, venue, side, now, forced_reason=None):
        requested = pending["qty"]
        filled, notional = ZERO, ZERO
        limit = pending["buy_limit" if side == "buy" else "sell_limit"]
        book = self.books.get(venue)
        reason = forced_reason or "no_marketable_depth"
        if not forced_reason and book is not None:
            levels = book["asks" if side == "buy" else "bids"]
            account, rate = self.balances[venue], self.rates[venue]
            for level in levels:
                price, available = level
                if (side == "buy" and price > limit) or (side == "sell" and price < limit):
                    break
                take = min(requested - filled, available)
                if take <= 0:
                    continue
                if side == "buy":
                    take = min(take, account["usdt"] / (price * (1 + rate)))
                    debit = take * price * (1 + rate)
                    if debit > account["usdt"]:
                        take = take.next_minus() if take > 0 else ZERO
                        debit = take * price * (1 + rate)
                    if take <= 0 or debit > account["usdt"]:
                        reason = "insufficient_buy_cash"
                        continue
                    account["usdt"] -= debit
                    account["btc"] += take
                else:
                    take = min(take, account["btc"])
                    if take <= 0:
                        reason = "insufficient_sell_inventory"
                        continue
                    account["btc"] -= take
                    account["usdt"] += take * price * (1 - rate)
                level[1] -= take
                filled += take
                notional += take * price
                if filled == requested:
                    break
        fee = notional * self.rates[venue]
        self.fees += fee
        status = "filled" if filled == requested else "partial" if filled else "no_fill"
        result = {"pair_id": pending["pair_id"], "execution_ms": now,
                  "scheduled_execution_ms": pending["due_ms"], "venue": venue, "side": side,
                  "requested_qty": requested, "qty": filled,
                  "vwap": notional / filled if filled else None,
                  "notional_usdt": notional, "fee_usdt": fee, "limit_price": limit,
                  "status": status, "reason": "filled" if status == "filled" else
                  "ioc_partial" if status == "partial" else reason}
        self.fills.append(result)
        return filled

    def _unobserved_pair(self, pending, observed_until, status, reason, evidence_reason=None):
        """Unknown execution is not a fabricated zero fill or a cancellation."""
        self.valuation_incomplete = True
        self.execution_blocked = True
        metric = "censored_pairs" if status == "censored" else "execution_unknown_pairs"
        self.stats[metric] += 1
        for venue, side in ((pending["buy_venue"], "buy"), (pending["sell_venue"], "sell")):
            self.fills.append({
                "pair_id": pending["pair_id"], "execution_ms": None,
                "scheduled_execution_ms": pending["due_ms"], "observed_until_ms": observed_until,
                "venue": venue, "side": side, "requested_qty": pending["qty"],
                "qty": None, "vwap": None, "notional_usdt": None, "fee_usdt": None,
                "limit_price": pending["buy_limit" if side == "buy" else "sell_limit"],
                "status": status, "reason": reason, "evidence_reason": evidence_reason,
            })
        self.quality.append({"recv_ms": observed_until, "venue": None, "pair_id": pending["pair_id"],
                             "reason": reason, "evidence_reason": evidence_reason})

    def _execute(self, now):
        pending, self.pending = self.pending, None
        problem = self._quality_problem(now)
        if problem:
            self._reject(now, None, "execution_" + problem)
            self._unobserved_pair(pending, now, "execution_unknown", "insufficient_execution_evidence", problem)
            return
        bqty = self._fill_leg(pending, pending["buy_venue"], "buy", now)
        sqty = self._fill_leg(pending, pending["sell_venue"], "sell", now)
        if not bqty and not sqty:
            self.stats["no_fill_pairs"] += 1
        elif not bqty or not sqty:
            self.stats["single_leg_pairs"] += 1
        elif bqty == pending["qty"] and sqty == pending["qty"]:
            self.stats["round_trips_complete"] += 1
        else:
            self.stats["partial_pairs"] += 1
        self._risk(now)

    def _extra(self):
        return sum((a["btc"] for a in self.balances.values()), ZERO) - self.initial_btc

    def _risk(self, now):
        extra = self._extra()
        self.max_extra = max(self.max_extra, abs(extra))
        if extra != 0:
            if self.risk_start is None:
                self.risk_start = now
            self.max_duration = max(self.max_duration, now - self.risk_start)
        elif self.risk_start is not None:
            self.max_duration = max(self.max_duration, now - self.risk_start)
            self.risk_start = None
        if self.enable_halt and abs(extra) > self.max_unhedged and not self.halted:
            self.halted = True
            self.stats["risk_halts"] += 1
            self.quality.append({"recv_ms": now, "venue": None, "reason": "unhedged_limit_halt",
                                 "extra_btc": extra, "limit_btc": self.max_unhedged})

    def _mark(self):
        mids = [(book["bids"][0][0] + book["asks"][0][0]) / 2
                for venue, book in self.books.items()
                if venue not in self.quarantined and book["bids"] and book["asks"]]
        return sum(mids, ZERO) / len(mids) if mids else self.mark_start

    def _point(self, now, mark=None, basis="received_book_mid"):
        mark = self._mark() if mark is None else mark
        cash = sum((a["usdt"] for a in self.balances.values()), ZERO)
        btc = sum((a["btc"] for a in self.balances.values()), ZERO)
        equity = cash + btc * mark
        hold = self.initial_cash + self.initial_btc * mark
        self._risk(now)
        self.curve.append({"recv_ms": now, "mark": mark, "equity_usdt": equity,
                           "hold_benchmark_usdt": hold,
                           "execution_contribution_usdt": equity - hold,
                           "extra_btc": btc - self.initial_btc, "valuation_basis": basis})

    def run(self):
        self._point(self.start_ms, self.mark_start, "explicit_initial_mark")
        for now, group in groupby(self.events, key=lambda e: e["recv_ms"]):
            while self.pending is not None and self.pending["due_ms"] < now:
                due = self.pending["due_ms"]
                self._execute(due)
                self._point(due)
                self._consider(due)
            for event in group:
                self._receive(event)
            if self.pending is not None and self.pending["due_ms"] == now:
                self._execute(now)
            self._consider(now)
            if self.pending is not None and self.pending["due_ms"] == now:
                self._execute(now)
            self._point(now)
        if self.pending is not None:
            pending, self.pending = self.pending, None
            self._unobserved_pair(pending, self.end_ms, "censored", "end_of_data")
        self._point(self.end_ms, self.mark_end, "explicit_terminal_mark_not_liquidation")
        end = self.curve[-1]
        initial_equity = self.initial_cash + self.initial_btc * self.mark_start
        inventory_pnl = self.initial_btc * (self.mark_end - self.mark_start)
        metrics = {**self.stats, "reject_counts": dict(self.rejected), "fees_usdt": self.fees,
                   "initial_equity_usdt": initial_equity,
                   "ending_equity_usdt": end["equity_usdt"],
                   "hold_benchmark_usdt": end["hold_benchmark_usdt"],
                   "total_pnl_usdt": end["equity_usdt"] - initial_equity,
                   "inventory_pnl_usdt": inventory_pnl,
                   "execution_contribution_usdt": end["execution_contribution_usdt"],
                   "max_extra_btc": self.max_extra, "ending_extra_btc": self._extra(),
                   "max_unhedged_duration_ms": self.max_duration}
        assumptions = [
            "Offline supplied depth snapshots only; no exchange account, network, or real orders. Source is labelled separately.",
            "Same-ms batches are available at the end of the declared clock bucket; all bucket receipts precede due execution and decisions.",
            "Initial capital is separate per venue; spot borrowing and cross-venue balance transfers are forbidden.",
            "Decision requires both known books and balances to cover target quantity; later IOC fills may be partial.",
            "IOC limits are the worst decision-time consumed price; execution uses only depth received by its due time.",
            "Virtual depth is depleted until a strictly newer sequence replaces that venue snapshot; sequence gaps are not assumed missing packets.",
            "Malformed new snapshots and regressing comparable event clocks quarantine the venue until a newer valid snapshot restores its trusted event-time watermark.",
            "Edge must strictly exceed min_edge_bps; candidate count is one evaluation per new two-book version pair.",
            "Pair outcome categories are exclusive; risk observations are after both same-time leg attempts.",
            "Unhedged duration tracks any nonzero extra BTC over initial holdings; halt threshold is separate.",
            "Common marks value all BTC and are not executable liquidation prices; terminal mark is explicit.",
            "Fees are already deducted from cash; inventory PnL is initial BTC mark change, not strategy alpha.",
            "No automatic hedge, queue position, borrowing, funding, transfer, or exchange credit-risk model.",
            "End-of-data orders are censored, not known zero fills; inadequate execution evidence blocks new orders rather than cancelling already submitted orders.",
            "When valuation_status is incomplete_execution_evidence, balances, fees and PnL describe only the known-fill ledger and do not establish actual ending exposure or performance.",
        ]
        if self.depth_mode == "top":
            assumptions.append("Optimistic top-price model: each new snapshot top is enlarged to at least one target order, then consumed.")
        if self.policy == "gross":
            assumptions.append("Gross signal ignores fees when screening; the ledger still charges every filled leg.")
        if not self.gates:
            assumptions.append("Age and cross-venue timestamp-skew gates are disabled; malformed/future/old-sequence events remain rejected.")
        if not self.require_event_time:
            assumptions.append("Explicit receive-only timing permission: missing event timestamps use receipt age/skew, without claiming exchange synchronization.")
        if self.source_kind == "tardis_l5_sample":
            assumptions.append("Vendor-received top-five-level samples are limited observed depth, not the entire exchange book or proof of executable fills.")
        return _json({"schema_version": 1, "scenario": self.scenario, "model": self.model,
                      "source_kind": self.source_kind, "assumptions": assumptions,
                      "parameters": {
                          "order_qty": self.qty,
                          "fee_bps": {v: self.rates[v] * 10000 for v in VENUES},
                          "latency_ms": self.latency,
                          "max_quote_age_ms": self.max_age,
                          "max_event_skew_ms": self.max_skew,
                          "max_unhedged_btc": self.max_unhedged,
                          "min_edge_bps": self.min_edge,
                          "max_pairs": self.max_pairs,
                          "depth_mode": self.depth_mode,
                          "signal_policy": self.policy,
                          "quality_gates": self.gates,
                          "risk_halt": self.enable_halt,
                          "require_event_time": self.require_event_time,
                      },
                      "valuation_status": "incomplete_execution_evidence" if self.valuation_incomplete else "complete_within_model",
                      "known_fill_ledger_only": self.valuation_incomplete,
                      "quarantined_venues": self.quarantined,
                      "initial_balances": self.initial, "final_balances": self.balances,
                      "metrics": metrics, "decisions": self.decisions, "fills": self.fills,
                      "equity_curve": self.curve, "quality_events": self.quality})


def run_replay(events: list[dict], config: dict, *, scenario: str = "custom", model: str = "guarded") -> dict:
    """Return a JSON-safe research replay; monetary and BTC values are strings."""
    return _Replay(events, config, scenario, model).run()
