"""Risk engine: signals + vols -> target lots + order intents (+ rolls)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

import numpy as np

from .config import RiskConfig
from .roll import LegOrder, RollPlan, build_roll_orders, merge_roll_with_rebalance
from .universe import ProductSpec, load_universe
from .vol import clip_vol


@dataclass
class SignalInput:
    """策略信号输入（品种级，不关心具体月份）。"""

    symbol: str
    direction: int              # +1 / -1 / 0
    strength: float = 1.0       # 0~1，信号强度
    price: float | None = None  # 用于估值；空则用 ProductSpec.last_price
    daily_vol: float | None = None  # 日收益率标准差；空则用 vol_floor
    contract: str | None = None     # 当前持仓/交易合约（具体月）
    current_lots: int = 0           # 当前持仓手数（带方向：>0 多，<0 空）
    # 风控扩展：止损价 / 开仓均价 / 已实现+浮动盈亏（元，负为亏）
    entry_price: float | None = None
    stop_price: float | None = None
    unrealized_pnl: float | None = None  # 若提供：≤ -max_symbol_loss 则强制平仓


@dataclass
class TargetPosition:
    symbol: str
    name: str
    sector: str
    direction: int
    target_lots: int
    price: float
    daily_vol: float
    notional: float
    margin: float
    risk_weight: float          # 分配后风险权重
    raw_risk_weight: float      # 约束前
    contract: str | None = None
    current_lots: int = 0
    delta_lots: int = 0         # target_signed - current_signed
    # 硬止损
    force_stopped: bool = False
    max_loss_cny: float = 0.0
    stop_risk_cny: float = 0.0  # 若打到 stop 的预估亏损


@dataclass
class OrderIntent:
    """普通调仓意图（非换月）。换月见 RollPlan.legs。"""

    symbol: str
    contract: str | None
    side: str
    offset: str
    lots: int
    comment: str = ""


@dataclass
class RiskSnapshot:
    capital: float
    target_vol_daily: float
    n_targets: int
    total_margin: float
    margin_usage: float
    gross_notional: float
    positions: list[TargetPosition] = field(default_factory=list)
    orders: list[OrderIntent] = field(default_factory=list)
    rolls: list[RollPlan] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def position_pnl_cny(
    direction: int,
    lots: int,
    entry_price: float,
    mark_price: float,
    multiplier: float,
) -> float:
    """持仓浮动盈亏（元）。lots 用绝对值，direction +1 多 / -1 空。"""
    if lots == 0 or direction == 0:
        return 0.0
    return (mark_price - entry_price) * float(direction) * abs(int(lots)) * float(multiplier)


def loss_per_lot_to_stop(
    direction: int,
    entry_price: float,
    stop_price: float,
    multiplier: float,
) -> float:
    """单手打到止损的亏损金额（正数表示亏损额度）。"""
    # 多：entry - stop；空：stop - entry
    move = (entry_price - stop_price) * float(direction)
    # 若 stop 在有利侧，move 为负 → 无「止损亏损」
    return max(0.0, move * float(multiplier))


class RiskEngine:
    def __init__(self, cfg: RiskConfig | None = None):
        self.cfg = cfg or RiskConfig()
        self.universe = {p.symbol: p for p in load_universe(self.cfg)}

    def size(self, signals: Iterable[SignalInput]) -> RiskSnapshot:
        cfg = self.cfg
        loss_cap = cfg.max_symbol_loss_cny
        sigs = list(signals)
        notes: list[str] = []

        # --- 0) 硬规则：单品种亏损 ≥ 总资金×0.95% → 无条件止损（目标 0 手）---
        force_flat: dict[str, SignalInput] = {}
        active: list[SignalInput] = []
        for s in sigs:
            if s.direction == 0 and not s.current_lots:
                continue
            prod = self.universe.get(s.symbol)
            px = s.price if s.price is not None else (prod.last_price if prod else None)
            entry = s.entry_price
            # 优先用显式 unrealized_pnl；否则用 entry+现价估算
            pnl = s.unrealized_pnl
            if pnl is None and entry is not None and px is not None and prod is not None:
                lots_now = abs(int(s.current_lots)) or 0
                # 若无持仓但有方向意图，不因浮亏强平
                if lots_now > 0:
                    d = 1 if s.current_lots > 0 else (-1 if s.current_lots < 0 else s.direction)
                    pnl = position_pnl_cny(d, lots_now, float(entry), float(px), prod.multiplier)
            has_pos = abs(int(s.current_lots or 0)) > 0
            if has_pos and pnl is not None and pnl <= -loss_cap:
                notes.append(
                    f"HARD STOP {s.symbol}: pnl={pnl:,.0f} ≤ "
                    f"-{loss_cap:,.0f} ({cfg.max_symbol_loss_frac:.2%} capital) "
                    f"→ force flatten"
                )
                force_flat[s.symbol] = s
                continue
            if s.direction != 0 and s.strength > 0:
                active.append(s)

        # --- 1) 有效波动 & 风险权重原值 ∝ strength / vol ---
        rows: list[dict] = []
        for s in active:
            prod = self.universe.get(s.symbol)
            if prod is None:
                notes.append(f"skip {s.symbol}: not in top40 universe")
                continue
            px = s.price if s.price is not None else prod.last_price
            if px is None or px <= 0:
                notes.append(f"skip {s.symbol}: no price")
                continue
            vol = s.daily_vol
            if vol is None or not np.isfinite(vol) or vol <= 0:
                vol = cfg.vol_floor_daily
                notes.append(f"{s.symbol}: missing vol -> floor {vol:.4f}")
            vol = clip_vol(vol, cfg.vol_floor_daily, cfg.vol_cap_daily)
            strength = float(min(max(s.strength, 0.0), 1.0))
            # 单位风险权重：强度/波动
            raw_w = strength / vol
            rows.append(
                {
                    "signal": s,
                    "prod": prod,
                    "price": float(px),
                    "vol": float(vol),
                    "raw_w": float(raw_w),
                    "strength": strength,
                }
            )

        if not rows and not force_flat:
            return RiskSnapshot(
                capital=cfg.capital,
                target_vol_daily=cfg.target_vol_daily * cfg.calendar_risk_mult,
                n_targets=0,
                total_margin=0.0,
                margin_usage=0.0,
                gross_notional=0.0,
                notes=notes or ["no active signals"],
            )

        if not rows and force_flat:
            # 仅有强平单
            positions_fs = self._force_stop_positions(force_flat, loss_cap)
            orders_fs: list[OrderIntent] = []
            for p in positions_fs:
                orders_fs.extend(self._delta_to_orders(p))
            return RiskSnapshot(
                capital=cfg.capital,
                target_vol_daily=cfg.target_vol_daily * cfg.calendar_risk_mult,
                n_targets=0,
                total_margin=0.0,
                margin_usage=0.0,
                gross_notional=0.0,
                positions=positions_fs,
                orders=orders_fs,
                notes=notes,
            )

        # --- 2) 归一化到组合风险预算，再施加单品种/板块 cap ---
        raw_sum = sum(r["raw_w"] for r in rows)
        for r in rows:
            r["w"] = r["raw_w"] / raw_sum

        # 单品种 cap
        for r in rows:
            r["w"] = min(r["w"], cfg.max_symbol_risk_weight)

        # 板块 cap：超限则板块内等比例压缩
        sector_sum: dict[str, float] = {}
        for r in rows:
            sec = r["prod"].sector
            sector_sum[sec] = sector_sum.get(sec, 0.0) + r["w"]
        scale_by_sector: dict[str, float] = {}
        for sec, total in sector_sum.items():
            cap = cfg.sector_cap.get(sec, 0.25)
            scale_by_sector[sec] = min(1.0, cap / total) if total > 0 else 1.0
        for r in rows:
            r["w"] *= scale_by_sector[r["prod"].sector]

        # 重新归一（若总权重被压到 <1，剩余风险不使用 = 自动降杠杆）
        w_sum = sum(r["w"] for r in rows)
        if w_sum <= 0:
            return RiskSnapshot(
                capital=cfg.capital,
                target_vol_daily=cfg.target_vol_daily,
                n_targets=0,
                total_margin=0.0,
                margin_usage=0.0,
                gross_notional=0.0,
                notes=notes + ["all weights capped to zero"],
            )
        # 不强制加回 1.0：保留 cap 带来的降杠杆
        # 但若 >1（理论上不会），再压回 1
        if w_sum > 1.0:
            for r in rows:
                r["w"] /= w_sum
            w_sum = 1.0

        # 持仓数上限：按权重保留前 N
        rows.sort(key=lambda x: x["w"], reverse=True)
        if len(rows) > cfg.max_positions:
            dropped = rows[cfg.max_positions :]
            rows = rows[: cfg.max_positions]
            for d in dropped:
                notes.append(f"drop {d['signal'].symbol}: max_positions={cfg.max_positions}")
            # 再归一保留集合内部相对比例，总量仍 ≤ 原 w_sum
            keep_sum = sum(r["w"] for r in rows)
            if keep_sum > 0:
                scale = min(1.0, w_sum) / keep_sum * keep_sum  # noqa: keep absolute
                # 压缩掉的权重不再分配
                pass

        target_vol = cfg.target_vol_daily * cfg.calendar_risk_mult
        equity = cfg.capital

        # --- 3) 波动率目标手数 ---
        # 品种 i 的目标美元波动 ≈ equity * target_vol * w_i
        # lots * mult * price * vol ≈ equity * target_vol * w_i
        positions: list[TargetPosition] = []
        for r in rows:
            s: SignalInput = r["signal"]
            prod: ProductSpec = r["prod"]
            px = r["price"]
            vol = r["vol"]
            w = r["w"]
            dollar_risk = equity * target_vol * w
            per_lot_risk = prod.multiplier * px * vol
            if per_lot_risk <= 0:
                notes.append(f"skip {s.symbol}: non-positive per-lot risk")
                continue
            lots_f = dollar_risk / per_lot_risk
            lots = int(max(0, round(lots_f)))
            if lots == 0 and lots_f > 0:
                notes.append(
                    f"{s.symbol}: theoretical lots={lots_f:.2f} -> 0 "
                    f"(lot granularity / capital too small for this weight)"
                )

            # 单品种保证金 cap
            mrate = cfg.margin_rate(s.symbol)
            margin_1 = prod.margin_per_lot(px, mrate)
            max_lots_margin = (
                int(equity * cfg.max_symbol_margin_frac // margin_1) if margin_1 > 0 else 0
            )
            if lots > max_lots_margin:
                notes.append(
                    f"{s.symbol}: lots {lots} -> {max_lots_margin} (symbol margin cap)"
                )
                lots = max_lots_margin

            # 单品种最大亏损 0.95% 资金：按止损距离（或波动估算）钳制手数
            entry_px = float(s.entry_price) if s.entry_price is not None else px
            stop_risk_1 = 0.0
            if s.stop_price is not None and entry_px > 0:
                stop_risk_1 = loss_per_lot_to_stop(
                    int(s.direction), entry_px, float(s.stop_price), prod.multiplier
                )
            elif cfg.loss_cap_vol_mult > 0:
                # 无止损价：用 N×日波动 × 合约价值 作最坏单手风险
                stop_risk_1 = (
                    prod.multiplier * px * vol * float(cfg.loss_cap_vol_mult)
                )
            if stop_risk_1 > 0 and loss_cap > 0:
                max_lots_loss = int(loss_cap // stop_risk_1)
                if lots > max_lots_loss:
                    notes.append(
                        f"{s.symbol}: lots {lots} -> {max_lots_loss} "
                        f"(max loss {cfg.max_symbol_loss_frac:.2%} capital="
                        f"{loss_cap:,.0f}; risk/lot={stop_risk_1:,.0f})"
                    )
                    lots = max(0, max_lots_loss)

            signed_current = int(s.current_lots)
            # current_lots 约定：带符号
            target_signed = lots * int(s.direction)
            delta = target_signed - signed_current

            positions.append(
                TargetPosition(
                    symbol=s.symbol,
                    name=prod.name,
                    sector=prod.sector,
                    direction=int(s.direction),
                    target_lots=lots,
                    price=px,
                    daily_vol=vol,
                    notional=lots * prod.notional_per_lot(px),
                    margin=lots * margin_1,
                    risk_weight=w,
                    raw_risk_weight=r["raw_w"] / raw_sum,
                    contract=s.contract,
                    current_lots=signed_current,
                    delta_lots=delta,
                    max_loss_cny=loss_cap,
                    stop_risk_cny=stop_risk_1 * lots,
                )
            )

        # 追加强制止损品种（目标 0）
        positions.extend(self._force_stop_positions(force_flat, loss_cap))

        # --- 4) 组合保证金 cap：超限全局等比例降手数 ---
        total_margin = sum(p.margin for p in positions)
        max_margin = equity * cfg.max_portfolio_margin_frac
        if total_margin > max_margin and total_margin > 0:
            scale = max_margin / total_margin
            notes.append(f"portfolio margin scale x{scale:.3f}")
            for p in positions:
                new_lots = int(max(0, round(p.target_lots * scale)))
                p.target_lots = new_lots
                prod = self.universe[p.symbol]
                mrate = cfg.margin_rate(p.symbol)
                p.notional = new_lots * prod.notional_per_lot(p.price)
                p.margin = new_lots * prod.margin_per_lot(p.price, mrate)
                p.delta_lots = new_lots * p.direction - p.current_lots
            total_margin = sum(p.margin for p in positions)

        # --- 5) 调仓阈值过滤 + 生成 OrderIntent ---
        orders: list[OrderIntent] = []
        for p in positions:
            if p.contract is None and p.delta_lots != 0:
                # 无具体合约时仍给出品种级意图
                pass
            if not self._should_rebalance(p):
                p.delta_lots = 0
                continue
            orders.extend(self._delta_to_orders(p))

        gross = sum(p.notional for p in positions)
        return RiskSnapshot(
            capital=equity,
            target_vol_daily=target_vol,
            n_targets=sum(1 for p in positions if p.target_lots > 0),
            total_margin=total_margin,
            margin_usage=total_margin / equity if equity else 0.0,
            gross_notional=gross,
            positions=positions,
            orders=orders,
            notes=notes,
        )

    def plan_rolls(
        self,
        rolls: list[tuple[str, str, str, int, int]],
        snapshot: RiskSnapshot | None = None,
    ) -> list[RollPlan]:
        """批量生成换月计划。

        rolls: iterable of (symbol, from_contract, to_contract, lots, direction)
        若提供 snapshot，则按目标手数合并加减仓（换月后在新合约上补差）。
        """
        cfg = self.cfg
        target_by_sym = {}
        if snapshot:
            for p in snapshot.positions:
                target_by_sym[p.symbol] = p

        plans: list[RollPlan] = []
        for symbol, frm, to, lots, direction in rolls:
            plan = build_roll_orders(
                symbol=symbol,
                from_contract=frm,
                to_contract=to,
                lots=lots,
                direction=direction,
                simultaneous=cfg.roll_simultaneous,
            )
            if snapshot and symbol in target_by_sym:
                tp = target_by_sym[symbol]
                plan.legs = merge_roll_with_rebalance(
                    plan,
                    new_target_lots=tp.target_lots,
                    new_direction=tp.direction,
                )
            plans.append(plan)
        if snapshot is not None:
            snapshot.rolls = plans
        return plans

    def _force_stop_positions(
        self, force_flat: dict[str, SignalInput], loss_cap: float
    ) -> list[TargetPosition]:
        """亏损触线品种：目标手数 0，生成平仓 delta。"""
        out: list[TargetPosition] = []
        for sym, s in force_flat.items():
            prod = self.universe.get(sym)
            px = s.price if s.price is not None else (prod.last_price if prod else 0.0)
            px = float(px or 0.0)
            cur = int(s.current_lots)
            d = 1 if cur > 0 else (-1 if cur < 0 else int(s.direction or 1))
            name = prod.name if prod else sym
            sector = prod.sector if prod else "other"
            out.append(
                TargetPosition(
                    symbol=sym,
                    name=name,
                    sector=sector,
                    direction=d,
                    target_lots=0,
                    price=px,
                    daily_vol=float(s.daily_vol or 0.0),
                    notional=0.0,
                    margin=0.0,
                    risk_weight=0.0,
                    raw_risk_weight=0.0,
                    contract=s.contract,
                    current_lots=cur,
                    delta_lots=-cur,
                    force_stopped=True,
                    max_loss_cny=loss_cap,
                )
            )
        return out

    def check_symbol_hard_stop(
        self,
        symbol: str,
        direction: int,
        lots: int,
        entry_price: float,
        mark_price: float,
        multiplier: float | None = None,
        unrealized_pnl: float | None = None,
    ) -> tuple[bool, float, float]:
        """判断是否触发「单品种亏损 ≥ 总资金×max_symbol_loss_frac」。

        Returns
        -------
        (triggered, pnl_cny, loss_cap_cny)
        """
        cfg = self.cfg
        cap = cfg.max_symbol_loss_cny
        if unrealized_pnl is not None:
            pnl = float(unrealized_pnl)
        else:
            mult = multiplier
            if mult is None:
                prod = self.universe.get(symbol)
                mult = prod.multiplier if prod else 1.0
            pnl = position_pnl_cny(direction, lots, entry_price, mark_price, mult)
        return pnl <= -cap, pnl, cap

    def _should_rebalance(self, p: TargetPosition) -> bool:
        cfg = self.cfg
        # 强制止损：必须执行平仓，忽略调仓阈值
        if p.force_stopped and p.delta_lots != 0:
            return True
        target_signed = p.target_lots * p.direction if p.target_lots > 0 else 0
        # target_lots=0 时目标为 0
        if p.target_lots == 0:
            target_signed = 0
        else:
            target_signed = p.target_lots * p.direction
        cur = p.current_lots
        delta = abs(target_signed - cur)
        if delta < cfg.min_lot_change:
            return False
        # 相对阈值：相对 max(|target|, |current|, 1)
        base = max(abs(target_signed), abs(cur), 1)
        if delta / base < cfg.rebalance_threshold and abs(target_signed) > 0 and abs(cur) > 0:
            return False
        return True

    def _delta_to_orders(self, p: TargetPosition) -> list[OrderIntent]:
        """把 signed delta 转成开平意图（简化：同向用 OPEN/CLOSE，反向先平后开）。"""
        cur = p.current_lots
        tgt = p.target_lots * p.direction if p.target_lots > 0 else 0
        orders: list[OrderIntent] = []
        contract = p.contract

        if cur == 0 and tgt != 0:
            side = "BUY" if tgt > 0 else "SELL"
            orders.append(
                OrderIntent(p.symbol, contract, side, "OPEN", abs(tgt), "new position")
            )
            return orders

        if tgt == 0 and cur != 0:
            side = "SELL" if cur > 0 else "BUY"
            why = "HARD_STOP loss>=0.95% capital" if p.force_stopped else "flatten"
            orders.append(
                OrderIntent(p.symbol, contract, side, "CLOSE", abs(cur), why)
            )
            return orders

        # 同号：加减仓
        if cur * tgt > 0:
            diff = tgt - cur
            if diff > 0:
                side = "BUY" if tgt > 0 else "SELL"
                orders.append(
                    OrderIntent(p.symbol, contract, side, "OPEN", abs(diff), "add")
                )
            else:
                side = "SELL" if cur > 0 else "BUY"
                orders.append(
                    OrderIntent(p.symbol, contract, side, "CLOSE", abs(diff), "reduce")
                )
            return orders

        # 反向：先平后开
        side_close = "SELL" if cur > 0 else "BUY"
        orders.append(
            OrderIntent(p.symbol, contract, side_close, "CLOSE", abs(cur), "flip close")
        )
        side_open = "BUY" if tgt > 0 else "SELL"
        orders.append(
            OrderIntent(p.symbol, contract, side_open, "OPEN", abs(tgt), "flip open")
        )
        return orders
