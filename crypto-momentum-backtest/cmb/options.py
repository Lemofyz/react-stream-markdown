"""期权实验(纯模拟,不下任何真实订单):

A. “彩票期权”:每天到期前 24 小时买最便宜的一档虚值看涨 + 看跌(|Delta| 0.02–0.05),
   各花账户 0.2%;价值达到成本 10 倍卖一半,其余持有到期。
B. 暴跌后追买看跌:4h 线 24 小时内跌 ≥ X% 且收盘 < MA20 → 买下一个周五到期、Delta≈−0.3 的看跌,
   花账户 1%;价值翻倍卖一半;其余在 4h 收盘站回 MA20 或到期前 1 天平仓。另做“马丁对照”。

两种数据:
- real:币安期权逐小时真实快照(买一/卖一/标记价/IV/Delta),只公开了 2023-05-18 ~ 2023-10-23;
  买入按卖一价、卖出按买一价成交,没有买一价就卖不掉。
- model:2023-06-20 起用币安 BVOL 指数(真实 30 天隐含波动率)× 用真实快照校准的“偏斜系数”,
  再用 Black–Scholes 定价;买卖价 = 理论价 × 用真实快照校准的卖一/买一系数。属于模型估算,不是真实成交。

每笔交易先按“每 1 USDT 权利金”算出 R(净盈亏 ÷ 权利金含手续费),再按资金比例做组合模拟。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np
import pandas as pd

H = pd.Timedelta(hours=1)
SQ2 = math.sqrt(2.0)
_erf = np.vectorize(math.erf, otypes=[float])


def ncdf(x):
    return 0.5 * (1.0 + _erf(np.asarray(x, dtype=float) / SQ2))


def bs(S, K, T, sig, cp):
    """Black–Scholes(r = 0)。返回 (价格, Delta);T ≤ 0 时为内在价值。"""
    S, K, T, sig = (np.asarray(v, dtype=float) for v in (S, K, T, sig))
    call = np.asarray(cp) == "C"
    intrinsic = np.where(call, np.maximum(S - K, 0), np.maximum(K - S, 0))
    T = np.maximum(T, 1e-12)
    sd = np.maximum(sig, 1e-6) * np.sqrt(T)
    d1 = (np.log(S / K) + 0.5 * sd ** 2) / sd
    d2 = d1 - sd
    nd1, nd2 = ncdf(d1), ncdf(d2)
    price = np.where(call, S * nd1 - K * nd2, K * (1 - nd2) - S * (1 - nd1))
    delta = np.where(call, nd1, nd1 - 1)
    tiny = T <= 1e-11
    return np.where(tiny, intrinsic, price), delta


@dataclass(frozen=True)
class Costs:
    fee_rate: float = 0.0003           # 每次买/卖:0.03% × 标的价格 × 数量
    exercise_fee_rate: float = 0.00015  # 到期行权:0.015% × 结算价 × 数量
    fee_cap: float | None = 0.10       # 手续费上限 = 权利金(或行权价值)的 10%;None = 不封顶

    def trade_fee(self, S, px):
        f = self.fee_rate * S
        return min(f, self.fee_cap * px) if self.fee_cap is not None else f

    def exercise_fee(self, ST, intrinsic):
        if intrinsic <= 0:
            return 0.0
        f = self.exercise_fee_rate * ST
        return min(f, self.fee_cap * intrinsic) if self.fee_cap is not None else f


@dataclass(frozen=True)
class LotteryParams:
    budget: float = 0.002
    delta_lo: float = 0.02
    delta_hi: float = 0.05
    hours_before: int = 24
    take_mult: float = 10.0
    take_frac: float = 0.5
    costs: Costs = Costs()

    def with_(self, **kw):
        return replace(self, **kw)


@dataclass(frozen=True)
class PutParams:
    drop_thr: float = 0.08
    lookback_bars: int = 6             # 6 根 4h = 24 小时
    ma: int = 20
    target_delta: float = -0.30
    min_hours_to_expiry: int = 48      # “下一个周五”= 至少 48 小时后的最近一个周五 08:00 到期
    budget: float = 0.01
    take_mult: float = 2.0
    take_frac: float = 0.5
    exit_hours_before: int = 24
    costs: Costs = Costs()

    def with_(self, **kw):
        return replace(self, **kw)


# ----------------------------------------------------------------------------
# 数据
# ----------------------------------------------------------------------------

class OptData:
    def __init__(self, root: str | Path = "data/options"):
        self.root = Path(root)
        self._chain, self._px, self._bvol, self._bars = {}, {}, {}, {}

    def chain(self, u: str) -> pd.DataFrame:
        if u not in self._chain:
            d = pd.read_csv(self.root / "eoh" / f"{u}.csv.gz", low_memory=False)
            parts = d.symbol.str.split("-")
            out = pd.DataFrame({
                "t": pd.to_datetime(d.date) + pd.to_timedelta(d.hour.astype(int) + 1, unit="h"),
                "exp": pd.to_datetime(parts.str[1], format="%y%m%d") + pd.Timedelta(hours=8),
                "K": parts.str[2].astype(float), "cp": d["type"], "symbol": d.symbol,
                "bid": pd.to_numeric(d.best_bid_price, errors="coerce").fillna(0.0),
                "ask": pd.to_numeric(d.best_ask_price, errors="coerce").fillna(0.0),
                "mark": pd.to_numeric(d.mark_price, errors="coerce"),
                "iv": pd.to_numeric(d.mark_iv, errors="coerce"),
                "delta": pd.to_numeric(d.delta, errors="coerce"),
            })
            self._chain[u] = out.sort_values(["symbol", "t"]).reset_index(drop=True)
        return self._chain[u]

    def px(self, u: str) -> pd.Series:
        """时刻 τ 的标的价格 = 以 τ 结束的 1h K 线收盘价。"""
        if u not in self._px:
            d = pd.read_csv(self.root / "spot1h" / f"{u}.csv.gz", parse_dates=["time"])
            self._px[u] = pd.Series(d.close.to_numpy(float), index=d.time + H).sort_index()
        return self._px[u]

    def bvol(self, u: str) -> pd.Series | None:
        """BVOL(%):每小时最后一个值,时间戳 = 该小时结束。"""
        if u not in self._bvol:
            f = self.root / "bvol" / f"{u}.csv.gz"
            if not f.exists():
                self._bvol[u] = None
            else:
                d = pd.read_csv(f, parse_dates=["time"])
                self._bvol[u] = pd.Series(d.bvol.to_numpy(float), index=d.time + H).sort_index()
        return self._bvol[u]

    def bars4h(self, u: str, ma: int, lookback: int) -> pd.DataFrame:
        key = (u, ma, lookback)
        if key not in self._bars:
            d = pd.read_csv(self.root / "spot1h" / f"{u}.csv.gz", parse_dates=["time"]).set_index("time")
            b = d.resample("4h").agg({"open": "first", "high": "max", "low": "min",
                                      "close": "last"}).dropna()
            b.index = b.index + pd.Timedelta(hours=4)      # 时间戳 = 收盘时刻
            b["sma"] = b.close.rolling(ma).mean()
            b["ret24"] = b.close / b.close.shift(lookback) - 1
            self._bars[key] = b
        return self._bars[key]


# ----------------------------------------------------------------------------
# 用真实快照校准模型(仅在 BVOL 与真实快照重叠的时段)
# ----------------------------------------------------------------------------

def calibrate(data: OptData, u: str, lp: LotteryParams, pp: PutParams) -> dict:
    ch = data.chain(u)
    bv = data.bvol(u)
    px = data.px(u)
    out = {"u": u}
    # 行权价间距(相对标的价格)、最小报价单位
    snap = ch[(ch.exp - ch.t) == lp.hours_before * H]
    steps = snap.groupby("t").K.apply(lambda k: np.median(np.diff(np.sort(k.unique())))
                                      if k.nunique() > 1 else np.nan)
    S_at = px.reindex(steps.index)
    out["step_pct"] = float((steps / S_at).median())
    pos_ask = ch.ask[ch.ask > 0]
    out["tick"] = float(pos_ask.min())
    if bv is None:
        return out
    # 彩票:按真实规则选中的期权
    lot = lottery_candidates(data, u, lp)
    lot = lot[lot.t >= bv.index[0]]
    if len(lot):
        atm = bv.reindex(lot.t).to_numpy() / 100
        for cp in ("C", "P"):
            m = (lot.cp == cp).to_numpy() & ~np.isnan(atm)
            out[f"lot_iv_ratio_{cp}"] = float(np.median(lot.iv.to_numpy()[m] / atm[m]))
        out["lot_ask_ratio"] = float(np.median(lot.ask / lot.mark))
        out["lot_bid_ratio"] = float(np.median(lot.first_bid / lot.mark))
        out["lot_n"] = int(len(lot))
    # 看跌:Delta −0.25 ~ −0.35、剩余 2–9 天
    tenor = (ch.exp - ch.t) / pd.Timedelta(days=1)
    pm = ch[(ch.cp == "P") & ch.delta.between(-0.35, -0.25) & tenor.between(2, 9)
            & (ch.t >= bv.index[0]) & (ch.mark > 0) & (ch.ask > 0)]
    atm = bv.reindex(pm.t).to_numpy() / 100
    ok = ~np.isnan(atm)
    out["put_iv_ratio"] = float(np.median(pm.iv.to_numpy()[ok] / atm[ok]))
    out["put_ask_ratio"] = float(np.median(pm.ask / pm.mark))
    out["put_bid_ratio"] = float(np.median(pm.bid / pm.mark))
    out["put_n"] = int(ok.sum())
    return out


# ----------------------------------------------------------------------------
# A. 彩票期权
# ----------------------------------------------------------------------------

def lottery_candidates(data: OptData, u: str, lp: LotteryParams) -> pd.DataFrame:
    """真实快照:每个到期日,到期前 hours_before 小时选出看涨/看跌各一档(只选,不算盈亏)。"""
    ch = data.chain(u)
    px = data.px(u)
    snap = ch[(ch.exp - ch.t) == lp.hours_before * H]
    rows = []
    for (t, exp), g in snap.groupby(["t", "exp"]):
        if t not in px.index:
            continue
        S0 = px[t]
        for cp in ("C", "P"):
            otm = g[(g.cp == cp) & ((g.K > S0) if cp == "C" else (g.K < S0))]
            band = otm[otm.delta.abs().between(lp.delta_lo, lp.delta_hi) & (otm.ask > 0)]
            if band.empty:
                continue
            pick = band.assign(ad=band.delta.abs()).sort_values(["ask", "ad"]).iloc[0]
            rows.append({"u": u, "cp": cp, "t": t, "exp": exp, "symbol": pick.symbol,
                         "K": pick.K, "S0": S0, "ask": pick.ask, "mark": pick.mark,
                         "first_bid": pick.bid, "iv": pick.iv, "delta": pick.delta})
    return pd.DataFrame(rows)


def lottery_real(data: OptData, u: str, lp: LotteryParams) -> pd.DataFrame:
    cand = lottery_candidates(data, u, lp)
    ch = data.chain(u).set_index("symbol")
    px = data.px(u)
    c = lp.costs
    out = []
    for r in cand.itertuples(index=False):
        if r.exp not in px.index:
            continue
        ST = px[r.exp]
        path = ch.loc[[r.symbol]]
        path = path[(path.t > r.t) & (path.t < r.exp)].sort_values("t")
        out.append(_lottery_settle(r._asdict(), ST, path.t.to_numpy(), path.bid.to_numpy(),
                                   px, c, lp))
    return pd.DataFrame(out)


def lottery_model(data: OptData, u: str, lp: LotteryParams, cal: dict,
                  start=None, end=None) -> pd.DataFrame:
    """BVOL 模型:每天 08:00 到期,前 24 小时按模型选最便宜的一档(|Delta| ≥ delta_lo 的最远虚值)。"""
    bv, px = data.bvol(u), data.px(u)
    c = lp.costs
    tick, step_pct = cal["tick"], cal["step_pct"]
    days = pd.date_range((start or bv.index[0]).normalize() + pd.Timedelta(days=1),
                         (end or bv.index[-1]).normalize(), freq="D") + pd.Timedelta(hours=8)
    out = []
    T0 = lp.hours_before / (365 * 24)
    for exp in days:
        t = exp - lp.hours_before * H
        if t not in px.index or exp not in px.index or t not in bv.index:
            continue
        S0, ST, atm = px[t], px[exp], bv[t] / 100
        step = _nice_step(S0 * step_pct)
        for cp in ("C", "P"):
            sig = atm * cal[f"lot_iv_ratio_{cp}"]
            n = np.arange(1, 400)
            K = (np.ceil(S0 / step) + n - 1) * step if cp == "C" else (np.floor(S0 / step) - n + 1) * step
            K = K[K > 0]
            price, delta = bs(S0, K, T0, sig, cp)
            ok = (np.abs(delta) >= lp.delta_lo) & (np.abs(delta) <= lp.delta_hi)
            if not ok.any():
                continue
            i = np.nonzero(ok)[0][-1]                        # 最远的那一档 = 最便宜
            mark = float(price[i])
            ask = max(tick, math.ceil(mark * cal["lot_ask_ratio"] / tick - 1e-9) * tick)
            # 持有期间每小时估一次买一价,用于 10 倍卖一半
            hours = pd.date_range(t + H, exp - H, freq="h")
            hours = hours[hours.isin(px.index) & hours.isin(bv.index)]
            if len(hours):
                Tl = ((exp - hours) / pd.Timedelta(days=365)).to_numpy()
                pb, _ = bs(px.reindex(hours).to_numpy(), K[i], Tl,
                           bv.reindex(hours).to_numpy() / 100 * cal[f"lot_iv_ratio_{cp}"], cp)
                bids = np.floor(pb * cal["lot_bid_ratio"] / tick) * tick
            else:
                bids = np.array([])
            rec = {"u": u, "cp": cp, "t": t, "exp": exp, "symbol": f"model-{cp}-{K[i]:g}",
                   "K": float(K[i]), "S0": S0, "ask": ask, "mark": mark, "first_bid": np.nan,
                   "iv": sig, "delta": float(delta[i])}
            out.append(_lottery_settle(rec, ST, hours.to_numpy(), bids, px, c, lp))
    return pd.DataFrame(out)


def _nice_step(x):
    mag = 10 ** math.floor(math.log10(x))
    for m in (1, 2, 2.5, 5, 10):
        if x <= m * mag * 1.25:
            return m * mag
    return 10 * mag


def _lottery_settle(r: dict, ST, times, bids, px, c: Costs, lp: LotteryParams) -> dict:
    """按 1 单位期权计算,再换算成每 1 USDT 成本的 R。"""
    cost = r["ask"] + c.trade_fee(r["S0"], r["ask"])
    q, proceeds, half_at = 1.0, 0.0, None
    for tt, b in zip(times, bids):
        if b >= lp.take_mult * r["ask"]:
            S = px.get(pd.Timestamp(tt), r["S0"])
            sq = q * lp.take_frac
            proceeds += sq * (b - c.trade_fee(S, b))
            q -= sq
            half_at = pd.Timestamp(tt)
            break
    intrinsic = max(ST - r["K"], 0) if r["cp"] == "C" else max(r["K"] - ST, 0)
    proceeds += q * (intrinsic - c.exercise_fee(ST, intrinsic))
    return {**r, "ST": ST, "intrinsic": intrinsic, "itm": intrinsic > 0, "cost": cost,
            "proceeds": proceeds, "multiple": proceeds / cost, "R": proceeds / cost - 1,
            "half_sold": half_at is not None, "half_at": half_at,
            "fee_share": c.trade_fee(r["S0"], r["ask"]) / cost,
            "exit_time": r["exp"]}


# ----------------------------------------------------------------------------
# B. 暴跌后追买看跌
# ----------------------------------------------------------------------------

def put_signals(data: OptData, u: str, pp: PutParams, start, end) -> pd.DataFrame:
    b = data.bars4h(u, pp.ma, pp.lookback_bars)
    cond = (b.ret24 <= -pp.drop_thr) & (b.close < b.sma)
    trig = cond & ~cond.shift(1, fill_value=False)       # 条件“刚出现”的那根才算新信号
    s = b[trig].loc[start:end]
    return s.assign(u=u)


def next_friday(t: pd.Timestamp, min_hours: int) -> pd.Timestamp:
    d = (t + pd.Timedelta(hours=min_hours)).normalize() + pd.Timedelta(hours=8)
    if d < t + pd.Timedelta(hours=min_hours):
        d += pd.Timedelta(days=1)
    while d.dayofweek != 4:
        d += pd.Timedelta(days=1)
    return d


def _put_manage(entry: dict, times, bids, S_path, bars, pp: PutParams, c: Costs,
                ST, exp) -> dict:
    """持仓管理(每 1 单位期权):翻倍卖一半 → MA20 收复 / 到期前 1 天平仓 → 否则到期结算。"""
    ask = entry["ask"]
    cost = ask + c.trade_fee(entry["S0"], ask)
    q, proceeds = 1.0, 0.0
    half_at, exit_t, reason = None, None, None
    stop_t = exp - pp.exit_hours_before * H
    reclaim = bars[(bars.index > entry["t"]) & (bars.close > bars.sma)].index
    reclaim_t = reclaim[0] if len(reclaim) else None
    for tt, b, S in zip(times, bids, S_path):
        tt = pd.Timestamp(tt)
        if half_at is None and b >= pp.take_mult * ask:
            sq = q * pp.take_frac
            proceeds += sq * (b - c.trade_fee(S, b))
            q -= sq
            half_at = tt
        hit_reclaim = reclaim_t is not None and tt >= reclaim_t
        if (hit_reclaim or tt >= stop_t) and b > 0:
            proceeds += q * (b - c.trade_fee(S, b))
            q = 0.0
            exit_t, reason = tt, ("ma_reclaim" if hit_reclaim else "pre_expiry")
            break
    if q > 0:                                             # 一直没有买一价:持有到期结算
        intrinsic = max(entry["K"] - ST, 0)
        proceeds += q * (intrinsic - c.exercise_fee(ST, intrinsic))
        exit_t, reason = exp, "expiry"
    return {**entry, "exp": exp, "cost": cost, "proceeds": proceeds,
            "R": proceeds / cost - 1, "multiple": proceeds / cost, "half_sold": half_at is not None,
            "half_at": half_at, "exit_time": exit_t, "exit_reason": reason,
            "hold_hours": (exit_t - entry["t"]) / H}


def _bvol_context(bv: pd.Series | None, t) -> dict:
    if bv is None or t not in bv.index:
        return {"bvol": np.nan, "bvol_med30": np.nan}
    hist = bv.loc[t - pd.Timedelta(days=30): t - H]
    return {"bvol": float(bv[t]), "bvol_med30": float(hist.median()) if len(hist) else np.nan}


def put_real(data: OptData, u: str, pp: PutParams) -> pd.DataFrame:
    ch = data.chain(u)
    px = data.px(u)
    bars = data.bars4h(u, pp.ma, pp.lookback_bars)
    bv = data.bvol(u)
    by_sym = {s: g.sort_values("t") for s, g in ch.groupby("symbol")}
    sig = put_signals(data, u, pp, ch.t.min(), ch.t.max())
    out, busy_until = [], pd.Timestamp.min
    tenor_d = (ch.exp - ch.t) / pd.Timedelta(days=1)
    for t, row in sig.iterrows():
        if t <= busy_until:
            continue
        exp = next_friday(t, pp.min_hours_to_expiry)
        g = ch[(ch.t == t) & (ch.exp == exp) & (ch.cp == "P") & (ch.ask > 0)]
        if g.empty or exp not in px.index:
            continue
        pick = g.iloc[(g.delta - pp.target_delta).abs().argmin()]
        # “平时”的同类期权 IV:过去 30 天、Delta −0.25~−0.35、剩余 2–9 天的看跌中位数
        ref = ch[(ch.cp == "P") & ch.delta.between(-0.35, -0.25) & tenor_d.between(2, 9)
                 & (ch.t < t) & (ch.t >= t - pd.Timedelta(days=30))]
        path = by_sym[pick.symbol]
        path = path[(path.t > t) & (path.t <= exp)]
        entry = {"u": u, "t": t, "symbol": pick.symbol, "K": pick.K, "S0": px.get(t, np.nan),
                 "ask": pick.ask, "mark": pick.mark, "iv": pick.iv, "delta": pick.delta,
                 "iv_ref": float(ref.iv.median()) if len(ref) else np.nan,
                 "drop24": row.ret24, **_bvol_context(bv, t)}
        res = _put_manage(entry, path.t.to_numpy(), path.bid.to_numpy(),
                          px.reindex(path.t).ffill().to_numpy(), bars, pp, pp.costs,
                          px[exp], exp)
        out.append(res)
        busy_until = res["exit_time"]
    return pd.DataFrame(out)


def put_model(data: OptData, u: str, pp: PutParams, cal: dict, start=None, end=None) -> pd.DataFrame:
    bv, px = data.bvol(u), data.px(u)
    bars = data.bars4h(u, pp.ma, pp.lookback_bars)
    start = start or bv.index[0] + pd.Timedelta(days=30)
    end = end or bv.index[-1]
    sig = put_signals(data, u, pp, start, end)
    tick, step_pct = cal["tick"], cal["step_pct"]
    ratio = cal["put_iv_ratio"]
    out, busy_until = [], pd.Timestamp.min
    for t, row in sig.iterrows():
        if t <= busy_until or t not in bv.index or t not in px.index:
            continue
        exp = next_friday(t, pp.min_hours_to_expiry)
        if exp not in px.index:
            continue
        S0 = px[t]
        sig0 = bv[t] / 100 * ratio
        T0 = (exp - t) / pd.Timedelta(days=365)
        step = _nice_step(S0 * step_pct)
        K = np.arange(np.floor(S0 * 0.5 / step), np.ceil(S0 / step) + 1) * step
        price, delta = bs(S0, K, T0, sig0, "P")
        i = int(np.abs(delta - pp.target_delta).argmin())
        mark = float(price[i])
        ask = max(tick, math.ceil(mark * cal["put_ask_ratio"] / tick - 1e-9) * tick)
        hours = pd.date_range(t + H, exp, freq="h")
        hours = hours[hours.isin(px.index) & hours.isin(bv.index)]
        S_path = px.reindex(hours).to_numpy()
        Tl = ((exp - hours) / pd.Timedelta(days=365)).to_numpy()
        pb, _ = bs(S_path, K[i], Tl, bv.reindex(hours).to_numpy() / 100 * ratio, "P")
        bids = np.floor(pb * cal["put_bid_ratio"] / tick) * tick
        entry = {"u": u, "t": t, "symbol": f"model-P-{K[i]:g}", "K": float(K[i]), "S0": S0,
                 "ask": ask, "mark": mark, "iv": sig0, "delta": float(delta[i]),
                 "iv_ref": np.nan, "drop24": row.ret24, **_bvol_context(bv, t)}
        res = _put_manage(entry, hours.to_numpy(), bids, S_path, bars, pp, pp.costs, px[exp], exp)
        out.append(res)
        busy_until = res["exit_time"]
    return pd.DataFrame(out)


# ----------------------------------------------------------------------------
# 组合模拟 / 马丁
# ----------------------------------------------------------------------------

def simulate(trades: pd.DataFrame, frac: float, initial: float = 10_000.0) -> tuple[pd.Series, pd.DataFrame]:
    """按时间顺序:开仓时权利金 = frac × 当时已实现权益;平仓时权益 += 权利金 × R。"""
    if trades.empty:
        return pd.Series(dtype=float), trades
    ev = []
    for i, r in trades.iterrows():
        ev.append((r.t, 1, i))
        ev.append((r.exit_time, 0, i))
    ev.sort(key=lambda x: (x[0], x[1]))
    eq, prem, curve = initial, {}, []
    for tt, kind, i in ev:
        if kind == 1:
            prem[i] = frac * eq
        else:
            eq += prem[i] * trades.at[i, "R"]
            curve.append((tt, eq))
    t2 = trades.copy()
    t2["premium"] = pd.Series(prem)
    t2["pnl"] = t2.premium * t2.R
    s = pd.Series([v for _, v in curve], index=[k for k, _ in curve]).groupby(level=0).last()
    return s, t2


def martingale(R: np.ndarray, frac: float = 0.01, initial: float = 10_000.0,
               ruin_level: float = 0.01, max_trades: int = 100_000) -> dict:
    """每次亏损后下一笔权利金加倍,盈利后回到 frac × 当前权益;权益 < 初始 × ruin_level 视为归零。"""
    eq, k, base = initial, 0, frac * initial
    peak_bet = 0.0
    for n, r in enumerate(R[:max_trades], 1):
        bet = min(base * 2 ** k, eq)
        peak_bet = max(peak_bet, bet / eq)
        eq += bet * r
        if r < 0:
            k += 1
        else:
            k, base = 0, frac * eq
        if eq < initial * ruin_level:
            return {"ruined": True, "trades": n, "equity": eq, "peak_bet_frac": peak_bet}
    return {"ruined": False, "trades": len(R[:max_trades]), "equity": eq, "peak_bet_frac": peak_bet}


def martingale_mc(R: np.ndarray, n_paths: int = 10_000, horizon: int = 5_000, seed: int = 7,
                  frac: float = 0.01) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    for _ in range(n_paths):
        seq = rng.choice(R, size=horizon, replace=True)
        m = martingale(seq, frac=frac)
        f = _fixed(seq, frac)
        rows.append({"ruined": m["ruined"], "trades_to_ruin": m["trades"] if m["ruined"] else np.nan,
                     "fixed_ruined": f})
    return pd.DataFrame(rows)


def _fixed(seq, frac, initial=10_000.0, ruin_level=0.01):
    eq = initial
    for r in seq:
        eq += frac * eq * r
        if eq < initial * ruin_level:
            return True
    return False
