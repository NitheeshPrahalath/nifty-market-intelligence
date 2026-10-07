"""Event-driven backtest engine (Phase 6).

The engine is a pure function of a prepared :class:`BacktestDataset` and a
:class:`BacktestConfig`, so it can be tested without a database and cannot
accidentally read anything the simulation is not allowed to see.

Two rules keep the results honest:

1. **Decide at the close, fill at the next open.** A signal produced on day D
   from D's data is filled at D+1's open, never at D's close. The dataset
   therefore only ever contains the signal view the strategy would have had on
   that day, and the engine cannot look further ahead than the data it is given.
2. **Orders are checked again when they fill.** The instrument must still be a
   member of the index universe on the fill date (no survivorship), the fill
   price is the next open moved by slippage, and costs are charged on both
   sides. Anything the engine refuses is recorded as a rejection, so a quiet
   simulation is never confused with a broken one.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date
from enum import StrEnum

from nmi.backtest.metrics import (
    EquityPoint,
    PerformanceMetrics,
    TradeResult,
    drawdowns,
    summarize,
)
from nmi.core.enums import BacktestExitReason, BacktestRejectReason


class SignalAction(StrEnum):
    """What the strategy asked for on a given instrument-day."""

    NONE = "NONE"
    ENTER = "ENTER"
    HOLD = "HOLD"
    EXIT = "EXIT"


@dataclass(frozen=True, slots=True)
class Bar:
    """One session's OHLC for an instrument."""

    open: float
    high: float
    low: float
    close: float


@dataclass(frozen=True, slots=True)
class SignalView:
    """One strategy's view for one instrument-day (Phase-4/5 output)."""

    action: SignalAction = SignalAction.NONE
    strategy_code: str = ""
    entry_low: float | None = None
    entry_high: float | None = None
    target_low: float | None = None
    target_high: float | None = None
    stop_price: float | None = None
    strength: float | None = None
    reasons: tuple[str, ...] = ()
    exit_reason: BacktestExitReason | None = None
    exit_detail: str = ""

    @property
    def target_price(self) -> float | None:
        """The first review level the strategy would take profit at."""
        return self.target_low if self.target_low is not None else self.target_high


@dataclass(frozen=True, slots=True)
class BacktestConfig:
    """Execution assumptions. Every field is a real-world cost, not a knob."""

    initial_capital: float = 1_000_000.0
    risk_per_trade: float = 0.01
    max_positions: int = 10
    max_position_weight: float = 0.20
    cost_bps: float = 12.0
    slippage_bps: float = 5.0
    max_holding_days: int | None = None
    order_ttl_days: int = 3
    default_stop_pct: float = 0.05
    risk_free_rate: float = 0.0

    def as_dict(self) -> dict:
        return {
            "initial_capital": self.initial_capital,
            "risk_per_trade": self.risk_per_trade,
            "max_positions": self.max_positions,
            "max_position_weight": self.max_position_weight,
            "cost_bps": self.cost_bps,
            "slippage_bps": self.slippage_bps,
            "max_holding_days": self.max_holding_days,
            "order_ttl_days": self.order_ttl_days,
            "default_stop_pct": self.default_stop_pct,
            "risk_free_rate": self.risk_free_rate,
        }


@dataclass(frozen=True, slots=True)
class HeldPosition:
    """What the engine knows about an open position, exposed to the evaluator.

    The Phase-5 exit engine needs the position it is judging (entry price, entry
    day, the strategy's own levels), so the engine hands exactly that to the
    dataset's evaluator instead of guessing at it.
    """

    instrument_id: int
    strategy_code: str
    entry_signal_date: date
    entry_date: date
    entry_price: float
    stop_price: float
    target_price: float | None
    entry_reasons: tuple[str, ...]
    sessions_held: int
    entry_low: float | None = None
    entry_high: float | None = None
    target_high: float | None = None


ExitEvaluator = Callable[[HeldPosition, date], SignalView | None]


@dataclass(frozen=True, slots=True)
class BacktestDataset:
    """Everything the simulation may look at, already filtered by date.

    ``signals`` holds one view per (instrument, day, strategy) because different
    strategies legitimately disagree on the same day. ``exit_evaluator`` is the
    Phase-5 exit engine applied to an *open* position; it must be a pure
    function of the position and the day, which is what keeps the simulation
    reproducible.
    """

    dates: tuple[date, ...]
    bars: Mapping[tuple[int, date], Bar]
    signals: Mapping[tuple[int, date], tuple[SignalView, ...]]
    universe: Mapping[date, frozenset[int]]
    symbols: Mapping[int, str] = field(default_factory=dict)
    benchmark: Mapping[date, float] = field(default_factory=dict)
    exit_evaluator: ExitEvaluator | None = None

    def symbol(self, instrument_id: int) -> str:
        return self.symbols.get(instrument_id, f"#{instrument_id}")

    def views(self, instrument_id: int, as_of: date) -> tuple[SignalView, ...]:
        return self.signals.get((instrument_id, as_of), ())


@dataclass(frozen=True, slots=True)
class Trade:
    """A completed round trip."""

    instrument_id: int
    symbol: str
    strategy_code: str
    entry_signal_date: date
    entry_date: date
    entry_price: float
    exit_date: date
    exit_price: float
    quantity: int
    gross_pnl: float
    cost: float
    net_pnl: float
    return_pct: float
    holding_days: int
    stop_price: float | None
    target_price: float | None
    exit_reason: BacktestExitReason
    exit_detail: str
    entry_reasons: tuple[str, ...]
    exit_reasons: tuple[str, ...]
    mae_pct: float | None = None
    mfe_pct: float | None = None

    def as_row(self, run_id: int) -> dict:
        return {
            "run_id": run_id,
            "instrument_id": self.instrument_id,
            "symbol": self.symbol,
            "strategy_code": self.strategy_code,
            "entry_signal_date": self.entry_signal_date,
            "entry_date": self.entry_date,
            "entry_price": self.entry_price,
            "exit_date": self.exit_date,
            "exit_price": self.exit_price,
            "quantity": self.quantity,
            "gross_pnl": self.gross_pnl,
            "cost": self.cost,
            "net_pnl": self.net_pnl,
            "return_pct": self.return_pct,
            "holding_days": self.holding_days,
            "stop_price": self.stop_price,
            "target_price": self.target_price,
            "exit_reason": self.exit_reason.value,
            "exit_detail": self.exit_detail,
            "entry_reasons": list(self.entry_reasons),
            "exit_reasons": list(self.exit_reasons),
            "mae_pct": self.mae_pct,
            "mfe_pct": self.mfe_pct,
        }

    def as_result(self) -> TradeResult:
        return TradeResult(
            net_pnl=self.net_pnl,
            return_pct=self.return_pct,
            holding_days=self.holding_days,
            exit_reason=self.exit_reason.value,
            entry_date=self.entry_date,
            exit_date=self.exit_date,
            mae_pct=self.mae_pct,
            mfe_pct=self.mfe_pct,
        )


@dataclass(frozen=True, slots=True)
class Rejection:
    """An order the engine refused, kept for audit."""

    as_of: date
    instrument_id: int
    symbol: str
    strategy_code: str
    side: str
    reason: BacktestRejectReason
    detail: str

    def as_row(self, run_id: int) -> dict:
        return {
            "run_id": run_id,
            "as_of": self.as_of,
            "instrument_id": self.instrument_id,
            "symbol": self.symbol,
            "strategy_code": self.strategy_code,
            "side": self.side,
            "reason": self.reason.value,
            "detail": self.detail,
        }


@dataclass(slots=True)
class _Order:
    instrument_id: int
    side: str
    strategy_code: str
    signal_date: date
    limit_price: float | None
    stop_price: float | None
    target_price: float | None
    reasons: tuple[str, ...]
    entry_low: float | None = None
    target_high: float | None = None
    exit_reason: BacktestExitReason | None = None
    exit_detail: str = ""
    entry_reasons: tuple[str, ...] = ()
    queued_on: date | None = None
    age: int = 0

    def aged(self) -> _Order:
        return replace(self, age=self.age + 1)


@dataclass(slots=True)
class _Position:
    instrument_id: int
    symbol: str
    strategy_code: str
    quantity: int
    entry_signal_date: date
    entry_date: date
    entry_price: float
    stop_price: float
    target_price: float | None
    entry_reasons: tuple[str, ...]
    entry_low: float | None = None
    entry_high: float | None = None
    target_high: float | None = None
    mae_pct: float = 0.0
    mfe_pct: float = 0.0

    def market_value(self, close: float) -> float:
        return self.quantity * close


@dataclass(frozen=True, slots=True)
class BacktestResult:
    """The complete, reproducible output of one simulation."""

    config: BacktestConfig
    points: tuple[EquityPoint, ...]
    trades: tuple[Trade, ...]
    rejections: tuple[Rejection, ...]
    benchmark_curve: tuple[float, ...]
    metrics: PerformanceMetrics
    traded_notional: float

    @property
    def final_equity(self) -> float:
        return self.points[-1].equity if self.points else self.config.initial_capital

    def trade_rows(self, run_id: int) -> list[dict]:
        return [t.as_row(run_id) for t in self.trades]

    def point_rows(self, run_id: int) -> list[dict]:
        return [
            {
                "run_id": run_id,
                "as_of": p.as_of,
                "equity": p.equity,
                "cash": p.cash,
                "positions": p.positions,
                "exposure_pct": p.exposure_pct,
                "drawdown_pct": p.drawdown_pct,
            }
            for p in self.points
        ]


def _cost_of(notional: float, cost_bps: float) -> float:
    return abs(notional) * cost_bps / 10_000.0


def _slip(price: float, slippage_bps: float, side: str) -> float:
    factor = slippage_bps / 10_000.0
    return price * (1 + factor) if side == "BUY" else price * (1 - factor)


def _position_size(
    cash: float, equity: float, fill_price: float, stop_price: float, config: BacktestConfig
) -> int:
    """Risk-based size, capped by cash and by the per-name weight limit."""
    if fill_price <= 0:
        return 0
    risk_per_share = fill_price - stop_price
    if risk_per_share > 0:
        by_risk = math.floor(equity * config.risk_per_trade / risk_per_share)
    else:
        by_risk = 0
    by_weight = math.floor(cash * config.max_position_weight / fill_price)
    return max(0, min(by_risk, by_weight))


class _Engine:
    """Mutable state for one simulation; the entry point is a pure function."""

    def __init__(self, dataset: BacktestDataset, config: BacktestConfig) -> None:
        self.dataset = dataset
        self.config = config
        self.cash = config.initial_capital
        self.positions: dict[int, _Position] = {}
        self.pending: list[_Order] = []
        self.trades: list[Trade] = []
        self.rejections: list[Rejection] = []
        self.equity_values: list[float] = []
        self.traded_notional = 0.0

    # ------------------------------------------------------------- helpers
    def _bar(self, instrument_id: int, as_of: date) -> Bar | None:
        return self.dataset.bars.get((instrument_id, as_of))

    def _close(self, instrument_id: int, as_of: date) -> float | None:
        bar = self._bar(instrument_id, as_of)
        return bar.close if bar else None

    def _equity(self, as_of: date) -> float:
        total = self.cash
        for position in self.positions.values():
            close = self._close(position.instrument_id, as_of)
            total += position.market_value(close if close is not None else position.entry_price)
        return total

    def _reject(self, order: _Order, as_of: date, reason: BacktestRejectReason, detail: str):
        self.rejections.append(
            Rejection(
                as_of=as_of,
                instrument_id=order.instrument_id,
                symbol=self.dataset.symbol(order.instrument_id),
                strategy_code=order.strategy_code,
                side=order.side,
                reason=reason,
                detail=detail,
            )
        )

    # --------------------------------------------------------------- fills
    def _fill_entry(self, order: _Order, as_of: date) -> None:
        if order.instrument_id in self.positions:
            self._reject(order, as_of, BacktestRejectReason.ALREADY_HELD, "already in the book")
            return
        if order.instrument_id not in self.dataset.universe.get(as_of, frozenset()):
            self._reject(
                order,
                as_of,
                BacktestRejectReason.NOT_A_MEMBER,
                "no longer a member of the index universe on the fill date",
            )
            return
        bar = self._bar(order.instrument_id, as_of)
        if bar is None or bar.open is None or bar.open <= 0:
            self._reject(order, as_of, BacktestRejectReason.NO_BAR, "no session bar to fill on")
            return
        limit = order.limit_price
        if limit is not None and bar.open > limit:
            # A limit buy only fills at or below the zone; keep it alive a little.
            if order.age < self.config.order_ttl_days - 1:
                self.pending.append(order.aged())
            else:
                self._reject(
                    order,
                    as_of,
                    BacktestRejectReason.LIMIT_PRICE_MISSED,
                    f"open {bar.open:.2f} above the {limit:.2f} limit after "
                    f"{order.age + 1} sessions",
                )
            return

        fill = _slip(bar.open, self.config.slippage_bps, "BUY")
        stop = order.stop_price
        reasons = list(order.entry_reasons)
        if stop is None or stop >= fill:
            stop = fill * (1 - self.config.default_stop_pct)
            reasons.append(
                f"No usable invalidation price; assumed a "
                f"{self.config.default_stop_pct:.0%} risk stop."
            )
        equity = self._equity(as_of)
        quantity = _position_size(self.cash, equity, fill, stop, self.config)
        if quantity < 1:
            self._reject(
                order,
                as_of,
                BacktestRejectReason.BELOW_LOT,
                f"risk budget and weight cap allow fewer than one share at {fill:.2f}",
            )
            return
        notional = quantity * fill
        cost = _cost_of(notional, self.config.cost_bps)
        if notional + cost > self.cash:
            self._reject(
                order,
                as_of,
                BacktestRejectReason.NO_CASH,
                f"{notional + cost:,.0f} needed, {self.cash:,.0f} available",
            )
            return
        self.cash -= notional + cost
        self.traded_notional += notional + cost
        self.positions[order.instrument_id] = _Position(
            instrument_id=order.instrument_id,
            symbol=self.dataset.symbol(order.instrument_id),
            strategy_code=order.strategy_code,
            quantity=quantity,
            entry_signal_date=order.signal_date,
            entry_date=as_of,
            entry_price=fill,
            stop_price=stop,
            target_price=order.target_price,
            entry_reasons=tuple(reasons),
        )

    def _close_position(
        self,
        position: _Position,
        as_of: date,
        price: float,
        reason: BacktestExitReason,
        detail: str,
        reasons: Sequence[str] = (),
    ) -> None:
        exit_price = _slip(price, self.config.slippage_bps, "SELL")
        notional = position.quantity * exit_price
        cost = _cost_of(notional, self.config.cost_bps)
        proceeds = notional - cost
        gross = (exit_price - position.entry_price) * position.quantity
        entry_cost = _cost_of(
            position.quantity * position.entry_price, self.config.cost_bps
        )
        self.cash += proceeds
        self.traded_notional += notional + cost
        self.positions.pop(position.instrument_id, None)
        holding = (as_of - position.entry_date).days
        self.trades.append(
            Trade(
                instrument_id=position.instrument_id,
                symbol=position.symbol,
                strategy_code=position.strategy_code,
                entry_signal_date=position.entry_signal_date,
                entry_date=position.entry_date,
                entry_price=position.entry_price,
                exit_date=as_of,
                exit_price=exit_price,
                quantity=position.quantity,
                gross_pnl=gross,
                cost=cost + entry_cost,
                net_pnl=proceeds - (position.quantity * position.entry_price + entry_cost),
                return_pct=(
                    (exit_price / position.entry_price - 1) * 100
                    if position.entry_price
                    else 0.0
                ),
                holding_days=holding,
                stop_price=position.stop_price,
                target_price=position.target_price,
                exit_reason=reason,
                exit_detail=detail,
                entry_reasons=position.entry_reasons,
                exit_reasons=tuple(reasons) or (detail,),
                mae_pct=position.mae_pct,
                mfe_pct=position.mfe_pct,
            )
        )

    def _fill_exit(self, order: _Order, as_of: date) -> None:
        position = self.positions.get(order.instrument_id)
        if position is None:
            return
        bar = self._bar(order.instrument_id, as_of)
        if bar is None or bar.open is None or bar.open <= 0:
            self._reject(order, as_of, BacktestRejectReason.NO_BAR, "no session bar to fill on")
            return
        self._close_position(
            position,
            as_of,
            bar.open,
            order.exit_reason or BacktestExitReason.STRATEGY_EXIT,
            order.exit_detail or "strategy exit",
            order.reasons,
        )

    # ------------------------------------------------------ intraday risk
    def _manage_positions(self, as_of: date) -> None:
        for instrument_id, position in list(self.positions.items()):
            bar = self._bar(instrument_id, as_of)
            if bar is None:
                continue
            # Excursions are measured before any exit, using the day's range.
            if bar.low:
                position.mae_pct = min(
                    position.mae_pct, (bar.low / position.entry_price - 1) * 100
                )
            if bar.high:
                position.mfe_pct = max(
                    position.mfe_pct, (bar.high / position.entry_price - 1) * 100
                )
            if bar.low and bar.low <= position.stop_price:
                # A gap through the stop fills at the open, not at the stop.
                fill = min(bar.open, position.stop_price)
                self._close_position(
                    position,
                    as_of,
                    fill,
                    BacktestExitReason.STOP_LOSS,
                    f"Stop {position.stop_price:.2f} hit (fill {fill:.2f}).",
                )
                continue
            if (
                position.target_price
                and bar.high
                and bar.high >= position.target_price
            ):
                self._close_position(
                    position,
                    as_of,
                    position.target_price,
                    BacktestExitReason.TARGET,
                    f"Review level {position.target_price:.2f} reached.",
                )
                continue
            if (
                self.config.max_holding_days is not None
                and (as_of - position.entry_date).days >= self.config.max_holding_days
            ):
                self._close_position(
                    position,
                    as_of,
                    bar.close,
                    BacktestExitReason.TIME_STOP,
                    f"Held {(as_of - position.entry_date).days} days; time stop.",
                )

    def _drop_removed_members(self, as_of: date) -> None:
        """Exit a name the day it leaves the index, as a real portfolio must."""
        members = self.dataset.universe.get(as_of)
        if members is None:
            return
        for instrument_id, position in list(self.positions.items()):
            if instrument_id not in members:
                bar = self._bar(instrument_id, as_of)
                self._close_position(
                    position,
                    as_of,
                    bar.close if bar else position.entry_price,
                    BacktestExitReason.LIQUIDATION,
                    "Removed from the index universe.",
                )

    # ------------------------------------------------------------ decisions
    def _decide(self, as_of: date) -> None:
        """Queue orders from today's data; they fill at the next session's open."""
        members = self.dataset.universe.get(as_of, frozenset())
        queued: set[int] = set()
        for order in self.pending:
            queued.add(order.instrument_id)
        free_slots = self.config.max_positions - len(self.positions) - len(
            [o for o in self.pending if o.side == "BUY"]
        )

        candidates: list[tuple[float, int, SignalView]] = []
        for instrument_id in sorted(members):
            if instrument_id in self.positions or instrument_id in queued:
                continue
            for view in self.dataset.views(instrument_id, as_of):
                if view.action is SignalAction.ENTER:
                    candidates.append((view.strength or 0.0, instrument_id, view))
        candidates.sort(
            key=lambda item: (-item[0], self.dataset.symbol(item[1]), item[2].strategy_code)
        )

        for _strength, instrument_id, view in candidates:
            order = _Order(
                instrument_id=instrument_id,
                side="BUY",
                strategy_code=view.strategy_code,
                signal_date=as_of,
                limit_price=view.entry_high,
                stop_price=view.stop_price,
                target_price=view.target_price,
                reasons=view.reasons,
                entry_reasons=view.reasons,
                entry_low=view.entry_low,
                target_high=view.target_high,
                queued_on=as_of,
            )
            if free_slots <= 0:
                self._reject(
                    order,
                    as_of,
                    BacktestRejectReason.NO_FREE_SLOT,
                    f"portfolio already holds {len(self.positions)} of "
                    f"{self.config.max_positions} names",
                )
                continue
            self.pending.append(order)
            free_slots -= 1

        for instrument_id, position in self.positions.items():
            view = self._exit_view(position, as_of)
            if view is not None and view.action is SignalAction.EXIT:
                self.pending.append(
                    _Order(
                        instrument_id=instrument_id,
                        side="SELL",
                        strategy_code=position.strategy_code,
                        signal_date=as_of,
                        limit_price=None,
                        stop_price=None,
                        target_price=None,
                        reasons=view.reasons,
                        exit_reason=view.exit_reason or BacktestExitReason.STRATEGY_EXIT,
                        exit_detail=view.exit_detail,
                        queued_on=as_of,
                    )
                )

    def _exit_view(self, position: _Position, as_of: date) -> SignalView | None:
        """The strongest exit case for an open position on this day.

        The Phase-5 evaluator judges the actual position (its fill price, how
        long it has been held, the thesis it was opened on). If it is not
        available, the day's own strategy view for the same strategy is used, so
        a backtest still honours the Phase-4 exit rules.
        """
        if self.dataset.exit_evaluator is not None:
            held = HeldPosition(
                instrument_id=position.instrument_id,
                strategy_code=position.strategy_code,
                entry_signal_date=position.entry_signal_date,
                entry_date=position.entry_date,
                entry_price=position.entry_price,
                stop_price=position.stop_price,
                target_price=position.target_price,
                entry_reasons=position.entry_reasons,
                sessions_held=self._sessions_held(position, as_of),
                entry_low=position.entry_low,
                entry_high=position.entry_high,
                target_high=position.target_high,
            )
            evaluated = self.dataset.exit_evaluator(held, as_of)
            if evaluated is not None:
                return evaluated
        for view in self.dataset.views(position.instrument_id, as_of):
            if (
                view.action is SignalAction.EXIT
                and view.strategy_code == position.strategy_code
            ):
                return view
        return None

    def _sessions_held(self, position: _Position, as_of: date) -> int:
        dates = self.dataset.dates
        return sum(1 for day in dates if position.entry_date <= day <= as_of)

    def _fill_pending(self, as_of: date) -> None:
        queued, self.pending = self.pending, []
        for order in queued:
            if order.side == "BUY":
                self._fill_entry(order, as_of)
            else:
                self._fill_exit(order, as_of)

    # ----------------------------------------------------------------- run
    def run(self) -> BacktestResult:
        points: list[EquityPoint] = []
        for as_of in self.dataset.dates:
            self._fill_pending(as_of)
            self._manage_positions(as_of)
            self._drop_removed_members(as_of)
            equity = self._equity(as_of)
            invested = sum(
                p.market_value(self._close(p.instrument_id, as_of) or p.entry_price)
                for p in self.positions.values()
            )
            self.equity_values.append(equity)
            points.append(
                EquityPoint(
                    as_of=as_of,
                    equity=equity,
                    cash=self.cash,
                    positions=len(self.positions),
                    exposure_pct=(invested / equity * 100) if equity else 0.0,
                    drawdown_pct=0.0,
                )
            )
            self._decide(as_of)

        # Anything still open at the end of the window is marked out at the
        # last close; leaving it open would hide both the cost and the result.
        last = self.dataset.dates[-1] if self.dataset.dates else None
        if last is not None:
            for position in list(self.positions.values()):
                close = self._close(position.instrument_id, last) or position.entry_price
                self._close_position(
                    position,
                    last,
                    close,
                    BacktestExitReason.END_OF_TEST,
                    "End of the test window.",
                )
            equity = self._equity(last)
            self.equity_values[-1] = equity
            points[-1] = replace(
                points[-1],
                equity=equity,
                cash=self.cash,
                positions=0,
                exposure_pct=0.0,
            )

        curve_dd = drawdowns(self.equity_values)
        points = [
            replace(point, drawdown_pct=drawdown * 100)
            for point, drawdown in zip(points, curve_dd, strict=False)
        ]
        benchmark_curve = self._benchmark_curve()
        metrics = summarize(
            points,
            [t.as_result() for t in self.trades],
            traded_notional=self.traded_notional,
            risk_free_rate=self.config.risk_free_rate,
            benchmark_curve=benchmark_curve,
        )
        return BacktestResult(
            config=self.config,
            points=tuple(points),
            trades=tuple(self.trades),
            rejections=tuple(self.rejections),
            benchmark_curve=benchmark_curve,
            metrics=metrics,
            traded_notional=self.traded_notional,
        )

    def _benchmark_curve(self) -> tuple[float, ...]:
        """Benchmark rebased to the starting capital, gaps carried forward."""
        if not self.dataset.benchmark or not self.dataset.dates:
            return ()
        curve: list[float] = []
        first = self.dataset.benchmark.get(self.dataset.dates[0])
        if not first:
            first = next(
                (v for v in self.dataset.benchmark.values() if v), None
            )
        if not first:
            return ()
        last_known = None
        for as_of in self.dataset.dates:
            value = self.dataset.benchmark.get(as_of) or last_known
            if value:
                last_known = value
                curve.append(self.config.initial_capital * value / first)
            else:
                curve.append(self.config.initial_capital)
        return tuple(curve)


def run_backtest(dataset: BacktestDataset, config: BacktestConfig) -> BacktestResult:
    """Simulate one strategy over one dataset.

    Pure: the same dataset and config always produce the same trades, equity
    curve and metrics, which is what makes a backtest reproducible.
    """
    if not dataset.dates:
        raise ValueError("a backtest needs at least one session")
    return _Engine(dataset, config).run()
