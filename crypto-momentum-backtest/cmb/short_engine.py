"""“抛物线做空”回测引擎(只做空,USDT 本位永续,纯模拟,不下任何真实订单)。

规则在看结果之前定死(见 ShortParams 默认值和下面的定义):

- 暴涨事件:某合约在 t 日收盘时,过去 3 或 4 天的累计涨幅(close_t / close_{t-k} - 1)
  ≥ pump_threshold,且过去 liquidity_window 天日均成交额 ≥ min_avg_quote_volume。
  第一次满足条件的这天记为“暴涨第 1 天”(D)。之后 max_wait_days 天内不再重复计事件。
- 不在暴涨第 1、2 天做空:最早在 D+2(第 3 天)开仓,最晚在 D+max_wait_days。每个事件最多一笔。
- 走弱信号(两种入场方式):
  daily:s 日收盘价 < s-1 日最低价(s ≥ D+1)→ s+1 日开盘做空;
  4h:当天(≥ D+2)首根 4 小时 K 线(00:00–04:00 UTC)的最低价被之后的 4h K 线跌破 → 在该价位做空
      (若那根 K 线直接低开,则按开盘价成交)。
- 止损:day_high = 信号当天最高价(daily:信号日最高;4h:当天截至触发那根 K 线的最高)
        atr = 成交价 + stop_atr_mult × ATR(14);
  止损距离 > max_stop_atr × ATR 则放弃。
- 止损成交价 = max(止损价, 下一根 K 线开盘价);若当根 K 线直接跳空高开到止损上方,按开盘价。
- 仓位:风险 = 账户 × risk_per_trade;数量 = 风险 ÷ 止损距离;单笔名义 ≤ 账户 × max_position_pct;
  所有空单名义合计 ≤ 账户 × max_gross_exposure(不加额外杠杆)。
- 平仓:价格回落到 10 日均线附近(最低价 ≤ 前一日 MA10 × (1+ma_touch_buffer))先平一半,
  剩余止损移到开仓价;之后收盘价 > MA10 → 次日开盘全部平仓;最长持有 max_hold_days 天(第 N 天收盘平仓)。
- 成本:每次开/平仓收手续费 fee + 滑点 slippage(都按名义价值);资金费率用币安真实历史数据,
  拿不到的时段按每 8 小时 assumed_funding_8h(空头支付)计。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from pathlib import Path

import numpy as np
import pandas as pd

DAY = pd.Timedelta(days=1)
H4 = pd.Timedelta(hours=4)


@dataclass(frozen=True)
class ShortParams:
    start: str = "2020-01-01"
    end: str | None = None
    initial_capital: float = 10_000.0

    # 选币
    pump_threshold: float = 1.0           # 100% / 200% / 300%
    pump_windows: tuple[int, ...] = (3, 4)
    min_avg_quote_volume: float = 5_000_000.0
    liquidity_window: int = 7             # 暴涨币多为新热点,用近 7 天日均成交额
    min_entry_offset: int = 2             # 最早在 D+2(暴涨第 3 天)开仓
    max_wait_days: int = 10               # 检测到暴涨后最多等 10 天出现走弱信号

    # 开仓
    entry_mode: str = "daily"             # daily / 4h

    # 止损
    stop_mode: str = "day_high"           # day_high / atr
    stop_atr_mult: float = 1.0
    atr_period: int = 14
    max_stop_atr: float = 1.5

    # 仓位
    risk_per_trade: float = 0.005
    max_position_pct: float = 0.25
    max_gross_exposure: float = 1.0

    # 平仓
    ma_period: int = 10
    ma_touch_buffer: float = 0.02         # “均线附近” = MA10 上方 2% 以内
    partial_frac: float = 0.5
    max_hold_days: int = 20

    # 成本
    fee: float = 0.001
    slippage: float = 0.002
    funding_mode: str = "real"            # real(真实,缺失时用假设值)/ assumed(全部用假设值)
    # 持仓期间的止损 / 平一半检查用 4h K 线(有数据时);False = 只用日线(下一根 = 次日开盘,更保守)
    intraday_bars: bool = True
    assumed_funding_8h: float = 0.001     # 空头每 8 小时支付 0.1%

    def with_(self, **kw) -> "ShortParams":
        return replace(self, **kw)

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class ShortResult:
    params: ShortParams
    equity: pd.Series
    trades: pd.DataFrame
    funnel: dict = field(default_factory=dict)


# ----------------------------------------------------------------------------
# 暴涨事件
# ----------------------------------------------------------------------------

def pump_events(panel: dict[str, pd.DataFrame], prm: ShortParams) -> pd.DataFrame:
    c = panel["close"]
    qv = panel["quote_volume"]
    liq = qv.rolling(prm.liquidity_window, min_periods=prm.liquidity_window).mean() \
        >= prm.min_avg_quote_volume
    gains = {k: c / c.shift(k) - 1 for k in prm.pump_windows}
    gmax = gains[prm.pump_windows[0]].copy()
    kbest = pd.DataFrame(prm.pump_windows[0], index=c.index, columns=c.columns)
    for k in prm.pump_windows[1:]:
        better = gains[k] > gmax
        gmax = gmax.mask(better, gains[k])
        kbest = kbest.mask(better, k)
    hit = (gmax >= prm.pump_threshold) & liq
    # 事件起点:今天满足,且之前 max_wait_days 天都不满足
    prior = hit.shift(1, fill_value=False).astype(float).rolling(
        prm.max_wait_days, min_periods=1).max().fillna(0).astype(bool)
    start = hit & ~prior
    start = start.loc[pd.Timestamp(prm.start):]
    if prm.end:
        start = start.loc[:pd.Timestamp(prm.end)]
    rows = []
    for d, j in zip(*np.nonzero(start.to_numpy())):
        date, sym = start.index[d], start.columns[j]
        k = int(kbest.loc[date, sym])
        rows.append({"symbol": sym, "detect_date": date, "pump_gain": float(gmax.loc[date, sym]),
                     "pump_days": k, "pump_start": date - (k - 1) * DAY})
    return pd.DataFrame(rows, columns=["symbol", "detect_date", "pump_gain", "pump_days",
                                       "pump_start"])


# ----------------------------------------------------------------------------
# 辅助数据:4h K 线、资金费率(按需从磁盘读取)
# ----------------------------------------------------------------------------

class Aux:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self._h4: dict[str, pd.DataFrame | None] = {}
        self._fund: dict[str, pd.Series | None] = {}

    def h4(self, sym: str) -> pd.DataFrame | None:
        if sym not in self._h4:
            f = self.root / "4h" / f"{sym}.csv.gz"
            self._h4[sym] = (pd.read_csv(f, parse_dates=["time"]).set_index("time")
                             if f.exists() else None)
        return self._h4[sym]

    def h4_day(self, sym: str, day: pd.Timestamp) -> pd.DataFrame | None:
        df = self.h4(sym)
        if df is None:
            return None
        bars = df.loc[day: day + DAY - pd.Timedelta(seconds=1)]
        return bars if len(bars) >= 2 and bars.index[0] == day else None

    def funding(self, sym: str) -> pd.Series | None:
        if sym not in self._fund:
            f = self.root / "funding" / f"{sym}.csv.gz"
            self._fund[sym] = (pd.read_csv(f, parse_dates=["time"]).set_index("time")["rate"]
                               .sort_index() if f.exists() else None)
        return self._fund[sym]


# ----------------------------------------------------------------------------
# 回测
# ----------------------------------------------------------------------------

def _atr(panel, n):
    h, l, c = panel["high"], panel["low"], panel["close"]
    pc = c.shift(1)
    tr = np.maximum(h - l, np.maximum((h - pc).abs(), (l - pc).abs()))
    return tr.rolling(n, min_periods=n).mean()


class ShortBacktester:
    def __init__(self, panel: dict[str, pd.DataFrame], aux: Aux):
        self.p = panel
        self.aux = aux
        self.dates = panel["close"].index
        self.syms = list(panel["close"].columns)
        self._cache = {}

    def _get(self, key, fn):
        if key not in self._cache:
            self._cache[key] = fn()
        return self._cache[key]

    def _manage_4h(self, j, ps, bars, d, day, next_day_open, ma_prev, prm, cover, stop_fill):
        """用当天的 4h K 线按时间顺序检查止损和平一半;每根 K 线内按对空头最不利的顺序
        (先止损;若先触及平一半价位,同一根 K 线内再检查移到开仓价的止损)。返回是否已全部平仓。"""
        target = ma_prev * (1 + prm.ma_touch_buffer) if not np.isnan(ma_prev) else np.nan
        n = len(bars)
        for k in range(n):
            bo, bh, bl = bars.open.iloc[k], bars.high.iloc[k], bars.low.iloc[k]
            nxt = bars.open.iloc[k + 1] if k + 1 < n else next_day_open
            t_end = bars.index[k] + H4
            if bh >= ps["stop"]:
                reason = "breakeven_stop" if ps["partial_done"] else "stop"
                cover(j, ps["qty"], stop_fill(ps["stop"], bo, nxt), d, t_end, reason, final=True)
                return True
            if not ps["partial_done"] and not np.isnan(target) and bl <= target:
                cover(j, ps["qty"] * prm.partial_frac, min(bo, target), d, t_end,
                      "partial", final=False)
                ps["partial_done"] = True
                ps["stop"] = ps["entry_px"]
                if bh >= ps["stop"]:
                    cover(j, ps["qty"], stop_fill(ps["stop"], bo, nxt), d, t_end,
                          "breakeven_stop", final=True)
                    return True
        return False

    def run(self, prm: ShortParams) -> ShortResult:
        p, dates, syms = self.p, self.dates, self.syms
        col = {s: j for j, s in enumerate(syms)}
        O, H, L, C = (p[k].to_numpy() for k in ("open", "high", "low", "close"))
        ATR = self._get(("atr", prm.atr_period), lambda: _atr(p, prm.atr_period).to_numpy())
        MA = self._get(("ma", prm.ma_period),
                       lambda: p["close"].rolling(prm.ma_period).mean().to_numpy())
        ev_key = ("ev", prm.pump_threshold, prm.pump_windows, prm.min_avg_quote_volume,
                  prm.liquidity_window, prm.max_wait_days, prm.start, prm.end)
        events = self._get(ev_key, lambda: pump_events(p, prm))
        # 最后一根“有成交”的 K 线;下架结算后的零成交 K 线不算
        has = ~np.isnan(C) & (np.nan_to_num(p["quote_volume"].to_numpy()) > 0)
        last_valid = np.where(has.any(axis=0), len(C) - 1 - np.argmax(has[::-1], axis=0), -1)

        start = pd.Timestamp(prm.start)
        end = pd.Timestamp(prm.end) if prm.end else dates[-1]
        i0 = max(1, int(dates.searchsorted(start)))
        i1 = int(dates.searchsorted(end, side="right")) - 1
        di = {d: i for i, d in enumerate(dates)}

        ev_by_day: dict[int, list] = {}
        for e in events.itertuples(index=False):
            ev_by_day.setdefault(di[e.detect_date], []).append(e)

        slip, fee = prm.slippage, prm.fee
        cash = prm.initial_capital
        armed: dict[int, tuple] = {}          # 列号 → (事件, D 的行号)
        pos: dict[int, dict] = {}
        pending_entries: list[tuple] = []
        pending_exits: set[int] = set()
        trades, eq_vals = [], []
        equity_prev = cash
        funnel = {"events": 0, "signals": 0, "skip_gap_above_stop": 0, "skip_stop_too_wide": 0,
                  "skip_no_4h_data": 0, "skip_exposure": 0, "filled": 0}

        def funding_cash(j, ps, t0, t1, price):
            """结算 (t0, t1] 内的资金费率;返回对空头的现金流(正 = 收到)。"""
            if t1 <= t0 or ps["qty"] <= 0:
                return 0.0
            notional = ps["qty"] * price
            fr = self.aux.funding(syms[j]) if prm.funding_mode == "real" else None
            if fr is not None and len(fr) and fr.index[0] <= t0 + DAY and fr.index[-1] >= t1 - DAY:
                rates = fr.loc[(fr.index > t0) & (fr.index <= t1)]
                ps["funding_real_n"] += len(rates)
                return float(notional * rates.sum())
            # 假设值:每天 00/08/16 UTC 结算,空头支付
            n = sum(1 for h in pd.date_range(t0.floor("8h"), t1, freq="8h") if t0 < h <= t1)
            ps["funding_assumed_n"] += n
            return -notional * prm.assumed_funding_8h * n

        def settle(j, ps, t, price):
            nonlocal cash
            f = funding_cash(j, ps, ps["fund_t"], t, price)
            cash += f
            ps["funding"] += f
            ps["fund_t"] = max(ps["fund_t"], t)

        def cover(j, qty, raw_px, d, t, reason, final):
            nonlocal cash
            ps = pos[j]
            settle(j, ps, t, raw_px)
            px = raw_px * (1 + slip)
            pnl = qty * (ps["entry_px"] - px) - qty * px * fee
            cash += pnl
            ps["realized"] += pnl
            ps["fees"] += qty * px * fee
            ps["qty"] -= qty
            ps["legs"].append((dates[d], reason, qty, px))
            if final:
                total = ps["realized"] + ps["funding"]
                partial = next((lg for lg in ps["legs"] if lg[1] == "partial"), None)
                trades.append({
                    "symbol": syms[j], "detect_date": ps["detect_date"],
                    "pump_start": ps["pump_start"], "pump_gain": ps["pump_gain"],
                    "signal_date": ps["signal_date"], "entry_date": dates[ps["entry_i"]],
                    "entry_time": ps["entry_t"], "exit_date": dates[d],
                    "entry_price": ps["entry_px"], "stop": ps["init_stop"],
                    "stop_pct": (ps["init_stop"] - ps["entry_px"]) / ps["entry_px"],
                    "qty": ps["init_qty"], "notional": ps["init_qty"] * ps["entry_px"],
                    "risk_usdt": ps["risk"], "risk_pct_equity": ps["risk"] / ps["eq_at_entry"],
                    "pnl": total, "R": total / ps["risk"],
                    "price_pnl": ps["realized"] + ps["fees"] + ps["entry_fee"],
                    "fees": ps["fees"] + ps["entry_fee"], "funding": ps["funding"],
                    "funding_R": ps["funding"] / ps["risk"],
                    "funding_real_n": ps["funding_real_n"],
                    "funding_assumed_n": ps["funding_assumed_n"],
                    "hold_days": (dates[d] - dates[ps["entry_i"]]).days + 1,
                    "partial_taken": partial is not None,
                    "partial_date": partial[0] if partial else pd.NaT,
                    "partial_price": partial[3] if partial else np.nan,
                    "exit_price": px, "exit_reason": reason,
                })
                del pos[j]

        def open_short(j, d, raw_px, stop, atr, t, ev, sig_date):
            nonlocal cash
            funnel["signals"] += 1
            if raw_px >= stop:
                funnel["skip_gap_above_stop"] += 1
                return False
            entry_px = raw_px * (1 - slip)
            if stop - raw_px > prm.max_stop_atr * atr:
                funnel["skip_stop_too_wide"] += 1
                return False
            qty = equity_prev * prm.risk_per_trade / (stop - entry_px)
            notional = min(qty * entry_px, equity_prev * prm.max_position_pct)
            gross = sum(ps["qty"] * C[d - 1, k] if not np.isnan(C[d - 1, k]) else
                        ps["qty"] * ps["entry_px"] for k, ps in pos.items())
            notional = min(notional, equity_prev * prm.max_gross_exposure - gross)
            if notional <= 0:
                funnel["skip_exposure"] += 1
                return False
            funnel["filled"] += 1
            qty = notional / entry_px
            efee = notional * fee
            cash -= efee
            pos[j] = {"qty": qty, "init_qty": qty, "entry_px": entry_px, "stop": stop,
                      "init_stop": stop, "risk": qty * (stop - entry_px), "entry_i": d,
                      "entry_t": t, "fund_t": t, "funding": 0.0, "realized": -efee,
                      "fees": 0.0, "entry_fee": efee, "partial_done": False, "legs": [],
                      "eq_at_entry": equity_prev, "detect_date": ev.detect_date,
                      "pump_start": ev.pump_start, "pump_gain": ev.pump_gain,
                      "signal_date": sig_date, "funding_real_n": 0, "funding_assumed_n": 0}
            return True

        def stop_fill(stop, bar_open, next_open):
            if bar_open >= stop:
                return bar_open
            return max(stop, next_open) if not np.isnan(next_open) else stop

        for d in range(i0, i1 + 1):
            day = dates[d]
            o, h, l, c = O[d], H[d], L[d], C[d]
            nxt_o = O[d + 1] if d + 1 < len(dates) else np.full(len(syms), np.nan)

            # 新事件
            for e in ev_by_day.get(d, []):
                j = col[e.symbol]
                if j not in pos:
                    armed[j] = (e, d)
                    funnel["events"] += 1

            # (a) 开盘:均线离场 / 到期离场
            for j in list(pending_exits):
                if j in pos:
                    px = o[j] if not np.isnan(o[j]) else C[d - 1, j]
                    cover(j, pos[j]["qty"], px, d, day, "ma_exit", final=True)
            pending_exits.clear()

            # (b) 开盘:daily 模式的新空单
            for (e, j, stop_ref, atr, sig_d) in pending_entries:
                if j in pos or np.isnan(o[j]):
                    continue
                stop = stop_ref if prm.stop_mode == "day_high" else o[j] + prm.stop_atr_mult * atr
                if open_short(j, d, o[j], stop, atr, day, e, dates[sig_d]):
                    armed.pop(j, None)
            pending_entries = []

            # (c) 4h 模式:盘中跌破首根 4h K 线最低点
            if prm.entry_mode == "4h":
                for j, (e, dd) in list(armed.items()):
                    if j in pos or d < dd + prm.min_entry_offset:
                        continue
                    bars = self.aux.h4_day(syms[j], day)
                    if bars is None:
                        funnel["skip_no_4h_data"] += 1
                        continue
                    lo1 = bars.low.iloc[0]
                    trig = next((k for k in range(1, len(bars)) if bars.low.iloc[k] < lo1), None)
                    if trig is None:
                        continue
                    raw = min(lo1, bars.open.iloc[trig])
                    atr = ATR[d - 1, j]
                    if np.isnan(atr):
                        continue
                    stop = (bars.high.iloc[: trig + 1].max() if prm.stop_mode == "day_high"
                            else raw + prm.stop_atr_mult * atr)
                    if not open_short(j, d, raw, stop, atr, bars.index[trig], e, day):
                        continue
                    armed.pop(j, None)
                    ps = pos[j]
                    ps["entry_t"] = bars.index[trig]
                    # 当天剩余的 4h K 线里检查止损
                    for k in range(trig + 1, len(bars)):
                        if bars.high.iloc[k] >= ps["stop"]:
                            no = bars.open.iloc[k + 1] if k + 1 < len(bars) else nxt_o[j]
                            px = stop_fill(ps["stop"], bars.open.iloc[k], no)
                            cover(j, ps["qty"], px, d, bars.index[k] + H4, "stop", final=True)
                            break

            # (d) 持仓:止损 → 均线附近平一半 → 收盘管理
            for j in list(pos):
                ps = pos[j]
                if ps["entry_i"] == d and prm.entry_mode == "4h":
                    settle(j, ps, day + DAY, c[j] if not np.isnan(c[j]) else ps["entry_px"])
                    continue
                if d > last_valid[j]:
                    cover(j, ps["qty"], C[last_valid[j], j], d, day, "delisted", final=True)
                    continue
                if np.isnan(h[j]):
                    continue
                bars = self.aux.h4_day(syms[j], day) if prm.intraday_bars else None
                if bars is not None:
                    if self._manage_4h(j, ps, bars, d, day, nxt_o[j], MA[d - 1, j], prm,
                                       cover, stop_fill):
                        continue
                elif h[j] >= ps["stop"]:
                    px = stop_fill(ps["stop"], o[j], nxt_o[j])
                    reason = "breakeven_stop" if ps["partial_done"] else "stop"
                    cover(j, ps["qty"], px, d, day + pd.Timedelta(hours=12), reason, final=True)
                    continue
                elif not ps["partial_done"] and not np.isnan(MA[d - 1, j]):
                    target = MA[d - 1, j] * (1 + prm.ma_touch_buffer)
                    if l[j] <= target:
                        cover(j, ps["qty"] * prm.partial_frac, min(o[j], target), d,
                              day + pd.Timedelta(hours=12), "partial", final=False)
                        ps["partial_done"] = True
                        ps["stop"] = ps["entry_px"]
                held = d - ps["entry_i"] + 1
                if held >= prm.max_hold_days:
                    cover(j, ps["qty"], c[j], d, day + DAY, "max_hold", final=True)
                    continue
                settle(j, ps, day + DAY, c[j])
                if ps["partial_done"] and c[j] > MA[d, j]:
                    pending_exits.add(j)

            # (e) 过期事件
            for j, (e, dd) in list(armed.items()):
                if d >= dd + prm.max_wait_days:
                    del armed[j]

            # (f) 盯市
            mtm = sum(ps["qty"] * (ps["entry_px"] - (c[j] if not np.isnan(c[j]) else ps["entry_px"]))
                      for j, ps in pos.items())
            equity_prev = cash + mtm
            eq_vals.append(equity_prev)

            # (g) daily 模式:收盘走弱信号 → 次日开盘做空
            if prm.entry_mode == "daily" and d < i1:
                for j, (e, dd) in armed.items():
                    if j in pos or d < dd + prm.min_entry_offset - 1:
                        continue
                    if d + 1 > dd + prm.max_wait_days:
                        continue
                    if not np.isnan(c[j]) and c[j] < L[d - 1, j] and not np.isnan(ATR[d, j]):
                        pending_entries.append((e, j, h[j], ATR[d, j], d))

        for j in list(pos):
            cover(j, pos[j]["qty"], C[min(i1, last_valid[j]), j], i1, dates[i1] + DAY,
                  "open_at_end", final=True)
        if eq_vals:
            eq_vals[-1] = cash
        idx = dates[i0:i1 + 1]
        return ShortResult(prm, pd.Series(eq_vals, idx, name="equity"),
                           pd.DataFrame(trades), funnel)
