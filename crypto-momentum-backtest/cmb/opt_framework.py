"""期权“三版本对比”统一框架(纯模拟,不下任何真实订单)。

三个版本:
- trend(顺势):方向和行情一致(牛市 / 向上偏离 / —— 买看涨;熊市 / 向下偏离 / 暴跌后 —— 买看跌)
- contra(逆势):方向相反
- straddle(多空都买):任何状态都买平值跨式
三种入场触发(每种一个模块):
- squeeze:4h 布林带宽度处于过去 90 天最低 20% 时(压缩刚出现的那根),按市场状态决定方向;震荡市只有跨式开仓
- deviation:每周一 08:00 UTC,若日线收盘偏离 200 日均线 ≥ X%(均值回归实验)
- crash:4h 线 24 小时内跌 ≥ X% 且收盘 < 4h MA20(暴跌后追买看跌实验)
市场状态(三版本共用):牛市 = 收盘 > MA200 且 MA200 高于 10 天前;熊市 = 收盘 < MA200 且 MA200 低于 10 天前;其余震荡。

定价:
- real:币安期权真实快照(2023-05-18 ~ 2023-10-23),按卖一买、买一卖;
- model:隐含波动率 = BVOL(2023-06-20 起,真实指数)或“由实际波动率估算的 BVOL 代理”(之前)
  × 按期限 / Delta 分组、用真实快照校准的系数;Black–Scholes 中间价 ± 3% 作为卖一 / 买一。
每笔 = 一次开仓(单腿或跨式两腿合计),权利金(含手续费)= 1R。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np
import pandas as pd

from cmb.options import Costs, OptData, bs

H = pd.Timedelta(hours=1)
H4 = pd.Timedelta(hours=4)
DAY = pd.Timedelta(days=1)
TENORS = {"weekly": (2, 9), "monthly": (14, 40), "quarterly": (60, 130)}


@dataclass(frozen=True)
class FwParams:
    trigger: str = "squeeze"          # squeeze / deviation / crash
    version: str = "trend"            # trend / contra / straddle
    expiry: str = "weekly"            # weekly / monthly / quarterly
    start: str = "2020-01-01"
    end: str | None = None
    target_delta: float = 0.30
    budget: float = 0.005             # 每笔权利金 = 账户 0.5% = 1R
    max_open: int = 3
    take_mult: float = 2.0            # 价值翻倍卖一半
    take_frac: float = 0.5
    exit_hours_before: int = 24       # 到期前 1 天平仓;0 = 持有到期结算
    exit_on_ma200: bool = False       # 均值回归模块:价格回到 200 日均线平仓
    exit_on_ma20_4h: bool = False     # 暴跌模块:4h 收盘站回 MA20 平仓
    # 市场状态
    sma_long: int = 200
    slope_lookback: int = 10
    # squeeze
    bb_n: int = 20
    bb_k: float = 2.0
    bb_window_days: int = 90
    bb_pct: float = 0.20
    # deviation
    dev_thr: float = 0.30
    # crash
    drop_thr: float = 0.08
    drop_bars: int = 6
    ma4h: int = 20
    # 成本
    spread: float = 0.03
    costs: Costs = Costs()

    def with_(self, **kw):
        return replace(self, **kw)


# ----------------------------------------------------------------------------
# 行情数据、市场状态、信号
# ----------------------------------------------------------------------------

class Market:
    def __init__(self, root: str | Path = "data/options", daily_root: str | Path = "data/raw"):
        self.root, self.daily_root = Path(root), Path(daily_root)
        self._b4, self._d = {}, {}

    def bars4h(self, u: str) -> pd.DataFrame:
        """时间戳 = 收盘时刻。"""
        if u not in self._b4:
            d = pd.read_csv(self.root / "spot4h" / f"{u}.csv.gz", parse_dates=["time"])
            d["t"] = d.time + H4
            self._b4[u] = d.set_index("t")[["open", "high", "low", "close"]].astype(float)
        return self._b4[u]

    def daily(self, u: str, prm: FwParams) -> pd.DataFrame:
        """日线 + MA200 + 市场状态;时间戳 = 收盘时刻(次日 00:00 UTC)。"""
        key = (u, prm.sma_long, prm.slope_lookback)
        if key not in self._d:
            d = pd.read_csv(self.daily_root / f"{u}.csv.gz", parse_dates=["date"])
            d = d.set_index("date")[["open", "high", "low", "close"]].astype(float)
            d.index = d.index + DAY
            d["sma"] = d.close.rolling(prm.sma_long).mean()
            up = d.sma > d.sma.shift(prm.slope_lookback)
            dn = d.sma < d.sma.shift(prm.slope_lookback)
            d["regime"] = np.where((d.close > d.sma) & up, "bull",
                                   np.where((d.close < d.sma) & dn, "bear", "range"))
            d.loc[d.sma.isna(), "regime"] = None
            d["dev"] = d.close / d.sma - 1
            self._d[key] = d
        return self._d[key]

    def regime_at(self, u: str, prm: FwParams, t) -> tuple[str | None, float]:
        d = self.daily(u, prm)
        i = d.index.searchsorted(t, side="right") - 1
        if i < 0:
            return None, np.nan
        return d.regime.iloc[i], d.dev.iloc[i]

    def signals(self, u: str, prm: FwParams) -> pd.DatetimeIndex:
        b = self.bars4h(u)
        if prm.trigger == "squeeze":
            mid = b.close.rolling(prm.bb_n).mean()
            sd = b.close.rolling(prm.bb_n).std()
            bw = 2 * prm.bb_k * sd / mid
            win = prm.bb_window_days * 6
            thr = bw.rolling(win, min_periods=win).quantile(prm.bb_pct)
            cond = bw <= thr
        elif prm.trigger == "crash":
            ret = b.close / b.close.shift(prm.drop_bars) - 1
            cond = (ret <= -prm.drop_thr) & (b.close < b.close.rolling(prm.ma4h).mean())
        elif prm.trigger == "deviation":
            idx = b.index[(b.index.dayofweek == 0) & (b.index.hour == 8)]
            return idx[(idx >= pd.Timestamp(prm.start))
                       & (idx <= (pd.Timestamp(prm.end) if prm.end else idx.max()))]
        else:
            raise ValueError(prm.trigger)
        trig = cond & ~cond.shift(1, fill_value=False)
        idx = b.index[trig.to_numpy()]
        return idx[(idx >= pd.Timestamp(prm.start))
                   & (idx <= (pd.Timestamp(prm.end) if prm.end else idx.max()))]

    def legs_for(self, u: str, prm: FwParams, t) -> tuple[list[str], str | None, float]:
        """返回 (要买的腿, 市场状态, 偏离度)。腿:"C" / "P" / 跨式 ["C","P"](平值)。"""
        reg, dev = self.regime_at(u, prm, t)
        if reg is None:
            return [], reg, dev
        if prm.version == "straddle":
            if prm.trigger == "deviation" and not abs(dev) >= prm.dev_thr:
                return [], reg, dev
            return ["C", "P"], reg, dev
        if prm.trigger == "squeeze":
            direction = {"bull": "up", "bear": "down"}.get(reg)
        elif prm.trigger == "deviation":
            direction = "up" if dev >= prm.dev_thr else ("down" if dev <= -prm.dev_thr else None)
        else:  # crash
            direction = "down"
        if direction is None:
            return [], reg, dev
        follow = "C" if direction == "up" else "P"
        leg = follow if prm.version == "trend" else ("P" if follow == "C" else "C")
        return [leg], reg, dev


# ----------------------------------------------------------------------------
# 到期日规则(三种分组都在开仓前定死)
# ----------------------------------------------------------------------------

def _last_friday(year, month):
    d = pd.Timestamp(year=year, month=month, day=1) + pd.offsets.MonthEnd(0)
    while d.dayofweek != 4:
        d -= DAY
    return d + pd.Timedelta(hours=8)


def pick_expiry(t: pd.Timestamp, group: str) -> pd.Timestamp:
    if group == "weekly":                         # 至少 48 小时后的最近一个周五 08:00
        d = (t + pd.Timedelta(hours=48)).normalize() + pd.Timedelta(hours=8)
        if d < t + pd.Timedelta(hours=48):
            d += DAY
        while d.dayofweek != 4:
            d += DAY
        return d
    months = range(1, 13) if group == "monthly" else (3, 6, 9, 12)
    min_days = 14 if group == "monthly" else 30
    y, m = t.year, t.month
    for _ in range(30):
        if m in months:
            e = _last_friday(y, m)
            if e - t >= pd.Timedelta(days=min_days):
                return e
        m += 1
        if m > 12:
            y, m = y + 1, 1
    raise RuntimeError("no expiry")


# ----------------------------------------------------------------------------
# 定价
# ----------------------------------------------------------------------------

class ModelPricer:
    """IV = ATM 水平(BVOL 或代理)× 分组系数;中间价 ± spread。"""

    def __init__(self, u: str, mkt: Market, data: OptData, ratios: dict, proxy: dict,
                 step_pct: float, proxy_scale: float = 1.0, iv_scale: float = 1.0,
                 bvol_scale: float = 1.0):
        self.u, self.mkt, self.ratios, self.step_pct = u, mkt, ratios, step_pct
        b = mkt.bars4h(u)
        bv = data.bvol(u)
        bv4 = (bv.reindex(b.index) / 100 * bvol_scale if bv is not None
               else pd.Series(np.nan, index=b.index))
        self.proxy = proxy_iv(b, proxy) * proxy_scale
        self.atm = bv4.where(bv4.notna(), self.proxy) * iv_scale
        self.source = pd.Series(np.where(bv4.notna(), "BVOL", "proxy"), index=b.index)
        self.px = b.close

    def iv(self, t, cp, kind, group):
        return float(self.atm.get(t, np.nan)) * self.ratios[(group, kind, cp)]

    def open(self, t, exp, legs, prm: FwParams, group):
        S = float(self.px[t])
        T = (exp - t) / pd.Timedelta(days=365)
        step = S * self.step_pct
        out = []
        for cp in legs:
            kind = "atm" if len(legs) == 2 else "d30"
            sig = self.iv(t, cp, kind, group)
            if np.isnan(sig):
                return None
            if kind == "atm":
                K = round(S / step) * step
            else:
                n = np.arange(1, 200)
                Ks = (np.ceil(S / step) + n - 1) * step if cp == "C" else (np.floor(S / step) - n + 1) * step
                Ks = Ks[Ks > 0]
                _, dl = bs(S, Ks, T, sig, cp)
                K = float(Ks[np.abs(np.abs(dl) - prm.target_delta).argmin()])
            mid, dl = bs(S, K, T, sig, cp)
            out.append({"cp": cp, "K": K, "kind": kind, "ask": float(mid) * (1 + prm.spread),
                        "mid": float(mid), "delta": float(dl), "iv": sig})
        return out

    def bids(self, legs, exp, times, prm: FwParams, group):
        """每个时刻各腿的买一价(中间价 × (1 − spread))。"""
        S = self.px.reindex(times).to_numpy()
        T = np.maximum(((exp - times) / pd.Timedelta(days=365)).to_numpy(), 0)
        res = []
        for lg in legs:
            sig = self.atm.reindex(times).to_numpy() * self.ratios[(group, lg["kind"], lg["cp"])]
            mid, _ = bs(S, lg["K"], T, sig, lg["cp"])
            res.append(mid * (1 - prm.spread))
        return np.array(res)


class RealPricer:
    """币安期权真实快照(2023-05-18 ~ 2023-10-23)。"""

    def __init__(self, u: str, mkt: Market, data: OptData):
        self.u, self.mkt = u, mkt
        ch = data.chain(u)
        self.ch = ch
        self.by_t = {t: g for t, g in ch.groupby("t")}
        self.by_sym = {s: g.set_index("t") for s, g in ch.groupby("symbol")}
        self.px = data.px(u)
        self.t0, self.t1 = ch.t.min(), ch.t.max()

    def open(self, t, exp, legs, prm: FwParams, group):
        g = self.by_t.get(t)
        if g is None or t not in self.px.index:
            return None
        g = g[(g.exp == exp) & (g.ask > 0)]
        S = float(self.px[t])
        out = []
        if len(legs) == 2:
            ks = set(g[g.cp == "C"].K) & set(g[g.cp == "P"].K)
            if not ks:
                return None
            K = min(ks, key=lambda k: abs(k - S))
            for cp in legs:
                r = g[(g.cp == cp) & (g.K == K)].iloc[0]
                out.append({"cp": cp, "K": K, "kind": "atm", "ask": r.ask, "mid": r.mark,
                            "delta": r.delta, "iv": r.iv, "symbol": r.symbol})
            return out
        cp = legs[0]
        h = g[g.cp == cp]
        if h.empty:
            return None
        r = h.iloc[(h.delta.abs() - prm.target_delta).abs().argmin()]
        return [{"cp": cp, "K": r.K, "kind": "d30", "ask": r.ask, "mid": r.mark,
                 "delta": r.delta, "iv": r.iv, "symbol": r.symbol}]

    def bids(self, legs, exp, times, prm: FwParams, group):
        res = []
        for lg in legs:
            s = self.by_sym[lg["symbol"]].bid
            res.append(s.reindex(times).fillna(0.0).to_numpy())
        return np.array(res)


# ----------------------------------------------------------------------------
# IV 代理 & 校准
# ----------------------------------------------------------------------------

def _rv(b: pd.DataFrame):
    lr = np.log(b.close).diff()
    ann = math.sqrt(6 * 365)
    return lr.rolling(180).std() * ann, lr.rolling(42).std() * ann


def fit_proxy(mkt: Market, data: OptData, u: str) -> dict:
    """log(BVOL) = a + b·log(RV30) + c·log(RV7),只用 BVOL 存在的时段拟合。"""
    b = mkt.bars4h(u)
    rv30, rv7 = _rv(b)
    bv = data.bvol(u).reindex(b.index) / 100
    df = pd.DataFrame({"y": np.log(bv), "x1": np.log(rv30), "x2": np.log(rv7)}).dropna()
    X = np.column_stack([np.ones(len(df)), df.x1, df.x2])
    coef, *_ = np.linalg.lstsq(X, df.y.to_numpy(), rcond=None)
    pred = X @ coef
    resid = df.y.to_numpy() - pred
    r2 = 1 - resid.var() / df.y.var()
    return {"a": coef[0], "b": coef[1], "c": coef[2], "r2": float(r2),
            "resid_sd": float(resid.std()), "n": len(df),
            "bvol_over_rv30": float(np.exp(df.y - df.x1).mean())}


def proxy_iv(b: pd.DataFrame, p: dict) -> pd.Series:
    rv30, rv7 = _rv(b)
    return np.exp(p["a"] + p["b"] * np.log(rv30) + p["c"] * np.log(rv7))


FAIR_TEST = dict(trigger="deviation", version="straddle", dev_thr=0.0, expiry="weekly",
                 exit_hours_before=0, take_mult=1e9, spread=0.0,
                 costs=Costs(fee_rate=0.0, exercise_fee_rate=0.0))


def fair_ratio(mkt: "Market", pricers: dict, start=None, end=None) -> float:
    """公允性检验:每周一买平值跨式、持有到期、不计价差和手续费;返回 到期价值 ÷ 中间价权利金。
    = 1 表示平均而言按中间价公平;> 1 表示期权偏便宜(对买方有利)。"""
    prm = FwParams(**FAIR_TEST, **({"start": start} if start else {}), end=end)
    t = run_trades(mkt, pricers, prm)
    return float(t.proceeds.sum() / t.cost.sum()) if len(t) else np.nan


def calibrate_ratios(data: OptData, u: str) -> tuple[dict, pd.DataFrame]:
    """真实快照的 IV ÷ 同时刻 BVOL,按(期限组, Delta 组, 看涨/看跌)取中位数。"""
    ch = data.chain(u)
    bv = data.bvol(u)
    ch = ch[ch.t >= bv.index[0]]
    atm = bv.reindex(ch.t).to_numpy() / 100
    tenor = ((ch.exp - ch.t) / DAY).to_numpy()
    ad = ch.delta.abs().to_numpy()
    rows, ratios = [], {}
    for group, (lo, hi) in TENORS.items():
        for kind, (dlo, dhi) in (("d30", (0.25, 0.35)), ("atm", (0.45, 0.55))):
            for cp in ("C", "P"):
                m = ((ch.cp == cp).to_numpy() & (tenor >= lo) & (tenor <= hi)
                     & (ad >= dlo) & (ad <= dhi) & ~np.isnan(atm) & (ch.iv > 0).to_numpy())
                r = ch.iv.to_numpy()[m] / atm[m]
                val = float(np.median(r)) if m.sum() >= 30 else np.nan
                ratios[(group, kind, cp)] = val
                rows.append({"期限组": group, "Delta 组": "0.3" if kind == "d30" else "平值",
                             "类型": "看涨" if cp == "C" else "看跌", "IV ÷ BVOL": val,
                             "样本": int(m.sum())})
    for k, v in list(ratios.items()):           # 缺样本的组用相邻期限补
        if np.isnan(v):
            for alt in ("monthly", "weekly", "quarterly"):
                if not np.isnan(ratios.get((alt, k[1], k[2]), np.nan)):
                    ratios[k] = ratios[(alt, k[1], k[2])]
                    break
    return ratios, pd.DataFrame(rows)


# ----------------------------------------------------------------------------
# 回测
# ----------------------------------------------------------------------------

def run_trades(mkt: Market, pricers: dict, prm: FwParams) -> pd.DataFrame:
    """逐个信号开仓并计算每笔 R(不含资金约束);同时持仓上限在 simulate_fw 里处理。"""
    rows = []
    c = prm.costs
    for u, pr in pricers.items():
        b = mkt.bars4h(u)
        d = mkt.daily(u, prm)
        sigs = mkt.signals(u, prm)
        if isinstance(pr, RealPricer):
            sigs = sigs[(sigs >= pr.t0) & (sigs <= pr.t1)]
        ma20 = b.close.rolling(prm.ma4h).mean()
        for t in sigs:
            legs, reg, dev = mkt.legs_for(u, prm, t)
            if not legs or t not in b.index:
                continue
            exp = pick_expiry(t, prm.expiry)
            if isinstance(pr, RealPricer) and exp > pr.t1:
                continue
            if exp > b.index[-1]:
                continue
            lg = pr.open(t, exp, legs, prm, prm.expiry)
            if not lg:
                continue
            S0 = float(b.close[t])
            cost = sum(x["ask"] + c.trade_fee(S0, x["ask"]) for x in lg)
            if not cost > 0:
                continue
            times = b.index[(b.index > t) & (b.index <= exp)]
            bid = pr.bids(lg, exp, times, prm, prm.expiry)
            val = bid.sum(axis=0)
            stop_t = exp - prm.exit_hours_before * H if prm.exit_hours_before > 0 else None
            # 模块专属离场时点
            ext = None
            if prm.exit_on_ma200:
                dd = d.loc[(d.index > t) & (d.index <= exp)]
                cross = dd.index[(dd.close <= dd.sma) if dev > 0 else (dd.close >= dd.sma)]
                ext = cross[0] if len(cross) else None
            if prm.exit_on_ma20_4h:
                rc = b.index[(b.index > t) & (b.index <= exp) & (b.close > ma20).to_numpy()]
                ext = rc[0] if len(rc) else None
            q, proceeds, half_t, exit_t, reason = 1.0, 0.0, None, None, None
            for k, tt in enumerate(times):
                S = float(b.close[tt])
                v = val[k]
                if half_t is None and v >= prm.take_mult * cost:
                    sq = q * prm.take_frac
                    proceeds += sq * sum(bid[i, k] - c.trade_fee(S, bid[i, k]) for i in range(len(lg)))
                    q -= sq
                    half_t = tt
                hit_ext = ext is not None and tt >= ext
                hit_stop = stop_t is not None and tt >= stop_t
                if (hit_ext or hit_stop) and v > 0:
                    proceeds += q * sum(bid[i, k] - c.trade_fee(S, bid[i, k]) for i in range(len(lg)))
                    q, exit_t = 0.0, tt
                    reason = "ma_exit" if hit_ext else "pre_expiry"
                    break
            if q > 0:
                ST = float(b.close.get(exp, np.nan))
                for x in lg:
                    intr = max(ST - x["K"], 0) if x["cp"] == "C" else max(x["K"] - ST, 0)
                    proceeds += q * (intr - c.exercise_fee(ST, intr))
                exit_t, reason = exp, "expiry"
            rows.append({
                "u": u, "t": t, "exp": exp, "legs": "+".join(x["cp"] for x in lg),
                "K": "/".join(f"{x['K']:g}" for x in lg), "S0": S0, "regime": reg, "dev": dev,
                "iv": float(np.mean([x["iv"] for x in lg])),
                "iv_source": getattr(pr, "source", pd.Series(dtype=str)).get(t, "real"),
                "cost": cost, "proceeds": proceeds, "R": proceeds / cost - 1,
                "multiple": proceeds / cost, "half_sold": half_t is not None,
                "exit_time": exit_t, "exit_reason": reason,
                "hold_days": (exit_t - t) / DAY,
            })
    return pd.DataFrame(rows).sort_values("t").reset_index(drop=True) if rows else pd.DataFrame()


def simulate_fw(trades: pd.DataFrame, prm: FwParams, initial: float = 10_000.0):
    """权利金 = budget × 当时已实现权益;同时持仓 ≤ max_open(超出的信号跳过)。"""
    if trades.empty:
        return pd.Series(dtype=float), trades
    ev = []
    for i, r in trades.iterrows():
        ev.append((r.t, 1, i))
        ev.append((r.exit_time, 0, i))
    ev.sort(key=lambda x: (x[0], x[1]))
    eq, open_, prem, curve, taken = initial, set(), {}, [], []
    for tt, kind, i in ev:
        if kind == 1:
            if len(open_) >= prm.max_open:
                continue
            open_.add(i)
            prem[i] = prm.budget * eq
            taken.append(i)
        elif i in open_:
            open_.discard(i)
            eq += prem[i] * trades.at[i, "R"]
            curve.append((tt, eq))
    t2 = trades.loc[taken].copy()
    t2["premium"] = pd.Series(prem)
    s = pd.Series([v for _, v in curve], index=[k for k, _ in curve]).groupby(level=0).last()
    return s, t2


# ----------------------------------------------------------------------------
# “暴拉缓跌 / 暴跌缓拉”
# ----------------------------------------------------------------------------

def tail_asymmetry(mkt: Market, u: str, prm: FwParams, start="2020-01-01") -> pd.DataFrame:
    """按市场状态(用上一根日线收盘时的状态),取绝对涨跌幅最大的 5% 行情,统计涨 / 跌各占多少。"""
    d = mkt.daily(u, prm)
    rows = []
    dr = d.close.pct_change()
    reg_prev = d.regime.shift(1)
    b = mkt.bars4h(u)
    r4 = b.close.pct_change()
    di = d.index
    reg4 = pd.Series(d.regime.to_numpy(), index=di).reindex(b.index, method="ffill").shift(1)
    for name, ret, reg in (("日线", dr, reg_prev), ("4 小时", r4, reg4)):
        ret, reg = ret.loc[start:], reg.loc[start:]
        for state in ("bull", "bear", "range"):
            x = ret[(reg == state)].dropna()
            if len(x) < 40:
                continue
            cut = x.abs().quantile(0.95)
            top = x[x.abs() >= cut]
            rows.append({"标的": u, "周期": name, "状态": state, "样本": len(x),
                         "前 5% 大波动笔数": len(top), "其中上涨": int((top > 0).sum()),
                         "上涨占比": float((top > 0).mean()),
                         "上涨平均幅度": float(top[top > 0].mean()) if (top > 0).any() else np.nan,
                         "下跌平均幅度": float(top[top < 0].mean()) if (top < 0).any() else np.nan})
    return pd.DataFrame(rows)


def real_skew(data: OptData, mkt: Market, u: str, prm: FwParams) -> pd.DataFrame:
    """真实快照:每天 08:00,|Delta|≈0.3 的看涨 IV − 看跌 IV(按期限组),并标上当天市场状态。"""
    ch = data.chain(u)
    ch = ch[ch.t.dt.hour == 8]
    tenor = (ch.exp - ch.t) / DAY
    rows = []
    for group in ("weekly", "monthly"):
        lo, hi = TENORS[group]
        g = ch[tenor.between(lo, hi) & ch.delta.abs().between(0.25, 0.35) & (ch.iv > 0)]
        for t, x in g.groupby("t"):
            c, p = x[x.cp == "C"].iv.median(), x[x.cp == "P"].iv.median()
            if np.isnan(c) or np.isnan(p):
                continue
            reg, _ = mkt.regime_at(u, prm, t)
            rows.append({"u": u, "t": t, "group": group, "skew": c - p, "regime": reg})
    return pd.DataFrame(rows)
