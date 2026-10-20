"""Qullamaggie 风格突破策略的日线回测引擎(纯模拟,不下任何真实订单)。

时间约定(全部按 UTC 日线):
- 横盘条件用截至 t-1 日收盘的数据判断(横盘必须在突破前就已成形);
- 第 t 天收盘确认突破,第 t+1 天开盘成交买入;
- 止损在盘中触发;均线离场在收盘确认、下一根开盘卖出。
不使用任何未来数据。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace

import numpy as np
import pandas as pd

# 可以单独关掉的条件(用于“去掉某个条件后结果变好还是变坏”的消融实验)
CONDITIONS = {
    "strength": "1. 之前强势(1/3 个月涨幅前 N%)",
    "tight": "2a. 区间收窄((高-低)/收盘 < X%)",
    "atr_contract": "2b. 横盘 ATR 低于上涨阶段 ATR",
    "vol_dryup": "3. 横盘成交量萎缩",
    "near_high": "4. 贴近 60 日高点",
    "ma_trend": "5. 站上 10/20 日均线且 20 日线向上",
    "breakout_vol": "买入:突破日放量",
}


@dataclass(frozen=True)
class Params:
    # 回测区间(交易区间;信号计算会用到更早的数据作为预热)
    start: str = "2020-01-01"
    end: str | None = None
    initial_capital: float = 10_000.0

    # 流动性过滤:过去 liquidity_window 天日均成交额(USDT)门槛
    min_avg_quote_volume: float = 5_000_000.0
    liquidity_window: int = 30

    # 选币方式:qulla = 强势 + 横盘 + 放量突破;top_gainers = 24h 涨幅榜前 top_n;
    #           top_volume = 24h 成交额榜前 top_n
    selection: str = "qulla"
    top_n: int = 10

    # 横盘条件 1:之前强势(1 个月 / 3 个月 = 30 / 90 个自然日,币圈 7×24 交易)
    mom_lookbacks: tuple[int, ...] = (30, 90)
    top_pct: float = 0.02
    rank_mode: str = "any"  # any: 任一周期进入前 top_pct;all: 每个周期都要进入

    # 横盘条件 2:波动收窄
    cons_min: int = 5
    cons_max: int = 15
    cons_max_range: float = 0.20      # (区间最高 - 区间最低) / 当前收盘价
    runup_window: int = 20            # “之前上涨阶段” = 横盘开始前的这么多天
    atr_contract_ratio: float = 1.0   # 横盘期 ATR% < ratio × 上涨阶段 ATR%

    # 横盘条件 3:成交量萎缩
    vol_dryup_ratio: float = 0.70     # 横盘均量 < ratio × 上涨阶段均量

    # 横盘条件 4:贴近高点
    near_high_window: int = 60
    near_high_pct: float = 0.10       # 收盘价距 60 日最高价不超过 10%

    # 横盘条件 5:均线向上
    ma_fast: int = 10
    ma_slow: int = 20
    ma_rising_lookback: int = 5       # 20 日线 今天 > N 天前 视为“上升”

    # 买入:突破日成交量 ≥ mult × 横盘均量
    breakout_vol_mult: float = 1.5

    # 消融:关掉哪些条件(取值见 CONDITIONS)
    disabled: tuple[str, ...] = ()

    # 止损:low = 信号日(突破日)最低价,且距离 > atr_mult × ATR 则放弃;
    #       atr = 成交价 - stop_atr_mult × ATR(不额外过滤)
    stop_mode: str = "low"
    atr_period: int = 14
    atr_mult: float = 1.0
    stop_atr_mult: float = 1.0

    # 仓位
    risk_per_trade: float = 0.005
    max_position_pct: float = 0.25

    # 止盈 / 离场
    partial_days: int = 3          # 持有 N 根日线后(第 N 天收盘)卖出一部分
    partial_frac: float = 1 / 3
    ma_exit: int = 20              # 之后收盘跌破 N 日均线 → 下一根开盘全部卖出

    # 成本
    fee: float = 0.001
    slippage: float = 0.0005

    # 小资金模拟:交易所最小下单金额(0 = 不限制)
    min_notional: float = 0.0
    min_notional_mode: str = "skip"  # skip: 低于最小金额放弃交易;bump: 抬高到最小金额

    def with_(self, **kw) -> "Params":
        return replace(self, **kw)

    def as_dict(self) -> dict:
        return asdict(self)

    def on(self, cond: str) -> bool:
        return cond not in self.disabled


@dataclass
class Result:
    params: Params
    equity: pd.Series
    trades: pd.DataFrame
    exposure: pd.Series = field(default=None)
    funnel: dict = field(default_factory=dict)  # 信号 → 成交 的过滤统计


# ----------------------------------------------------------------------------
# 信号(向量化,带缓存,便于参数扫描)
# ----------------------------------------------------------------------------

class SignalBuilder:
    def __init__(self, panel: dict[str, pd.DataFrame]):
        self.p = panel
        self.symbols = list(panel["close"].columns)
        self.dates = panel["close"].index
        self._cache: dict = {}

    def _memo(self, key, fn):
        if key not in self._cache:
            # 横盘信号很占内存,参数扫描时只保留最近几组
            heavy = [k for k in self._cache if isinstance(k, tuple) and k[0] in ("qulla", "rank")]
            if len(heavy) >= 6:
                del self._cache[heavy[0]]
            self._cache[key] = fn()
        return self._cache[key]

    def eligible(self, min_vol: float, window: int) -> pd.DataFrame:
        def f():
            qv = self.p["quote_volume"]
            avg = qv.rolling(window, min_periods=window).mean()
            return (avg >= min_vol) & self.p["close"].notna()
        return self._memo(("elig", min_vol, window), f)

    def momentum(self, prm: Params) -> tuple[pd.DataFrame, pd.DataFrame]:
        """返回 (是否进入涨幅前 top_pct, 动量得分[越小越强])。"""
        key = ("mom", prm.min_avg_quote_volume, prm.liquidity_window,
               prm.mom_lookbacks, prm.top_pct, prm.rank_mode)

        def f():
            elig = self.eligible(prm.min_avg_quote_volume, prm.liquidity_window)
            n = elig.sum(axis=1)
            k = np.maximum(1, np.ceil(n * prm.top_pct))
            hits, ranks = [], []
            for lb in prm.mom_lookbacks:
                ret = (self.p["close"] / self.p["close"].shift(lb) - 1).where(elig)
                r = ret.rank(axis=1, ascending=False, method="first")
                hits.append(r.le(k, axis=0))
                ranks.append((r / n.values[:, None]).fillna(np.inf))
            hit = hits[0]
            for h in hits[1:]:
                hit = (hit | h) if prm.rank_mode == "any" else (hit & h)
            score = ranks[0]
            for r in ranks[1:]:
                score = np.minimum(score, r)
            return hit & elig, score
        return self._memo(key, f)

    def tr_pct(self) -> pd.DataFrame:
        def f():
            h, l, c = self.p["high"], self.p["low"], self.p["close"]
            pc = c.shift(1)
            tr = np.maximum(h - l, np.maximum((h - pc).abs(), (l - pc).abs()))
            return tr / pc
        return self._memo("trp", f)

    def atr(self, period: int) -> pd.DataFrame:
        def f():
            h, l, c = self.p["high"], self.p["low"], self.p["close"]
            pc = c.shift(1)
            tr = np.maximum(h - l, np.maximum((h - pc).abs(), (l - pc).abs()))
            return tr.rolling(period, min_periods=period).mean()
        return self._memo(("atr", period), f)

    def sma(self, n: int) -> pd.DataFrame:
        return self._memo(("sma", n),
                          lambda: self.p["close"].rolling(n, min_periods=n).mean())

    def qulla_signals(self, prm: Params):
        """强势 + 横盘 + 放量突破。

        对 N = cons_min..cons_max,横盘区间 = [t-N, t-1],上涨阶段 = 横盘之前 runup_window 天。
        在全部(未关闭的)横盘条件都满足的 N 中取最长的一个;第 t 天收盘 > 区间最高价
        且(可选)成交量 ≥ breakout_vol_mult × 区间均量 → 信号。
        返回 (signal, score, base_len, base_high)。
        """
        key = ("qulla", prm.min_avg_quote_volume, prm.liquidity_window, prm.mom_lookbacks,
               prm.top_pct, prm.rank_mode, prm.cons_min, prm.cons_max, prm.cons_max_range,
               prm.runup_window, prm.atr_contract_ratio, prm.vol_dryup_ratio,
               prm.near_high_window, prm.near_high_pct, prm.ma_fast, prm.ma_slow,
               prm.ma_rising_lookback, prm.breakout_vol_mult, tuple(sorted(prm.disabled)))

        def f():
            p = self.p
            c, v = p["close"], p["volume"]
            elig = self.eligible(prm.min_avg_quote_volume, prm.liquidity_window)
            mom_hit, score = self.momentum(prm)
            c1 = c.shift(1)                                  # 横盘最后一天(t-1)的收盘
            # 不依赖 N 的前置条件(全部在 t-1 判断)
            pre = elig.shift(1, fill_value=False) & elig
            if prm.on("strength"):
                pre &= mom_hit.shift(1, fill_value=False)
            if prm.on("near_high"):
                hh60 = p["high"].shift(1).rolling(prm.near_high_window,
                                                  min_periods=prm.near_high_window).max()
                pre &= c1 >= (1 - prm.near_high_pct) * hh60
            if prm.on("ma_trend"):
                f1, s1 = self.sma(prm.ma_fast).shift(1), self.sma(prm.ma_slow).shift(1)
                pre &= (c1 > f1) & (c1 > s1) & (s1 > s1.shift(prm.ma_rising_lookback))

            h1, l1 = p["high"].shift(1), p["low"].shift(1)
            trp1, v1 = self.tr_pct().shift(1), v.shift(1)
            nan = pd.DataFrame(np.nan, index=self.dates, columns=self.symbols)
            base_high, base_vol, base_len = nan.copy(), nan.copy(), nan.copy()
            for n in range(prm.cons_min, prm.cons_max + 1):   # 由短到长,后写覆盖 → 最长区间
                hh = h1.rolling(n, min_periods=n).max()
                ll = l1.rolling(n, min_periods=n).min()
                bvol = v1.rolling(n, min_periods=n).mean()
                ok = pre & hh.notna()
                if prm.on("tight"):
                    ok &= (hh - ll) / c1 < prm.cons_max_range
                if prm.on("atr_contract"):
                    base_atr = trp1.rolling(n, min_periods=n).mean()
                    run_atr = trp1.shift(n).rolling(prm.runup_window,
                                                    min_periods=prm.runup_window).mean()
                    ok &= base_atr < prm.atr_contract_ratio * run_atr
                if prm.on("vol_dryup"):
                    run_vol = v1.shift(n).rolling(prm.runup_window,
                                                  min_periods=prm.runup_window).mean()
                    ok &= bvol < prm.vol_dryup_ratio * run_vol
                base_high = base_high.mask(ok, hh)
                base_vol = base_vol.mask(ok, bvol)
                base_len = base_len.mask(ok, float(n))
            sig = c > base_high
            if prm.on("breakout_vol"):
                sig &= v >= prm.breakout_vol_mult * base_vol
            return sig & elig, score.shift(1).fillna(np.inf), base_len, base_high
        return self._memo(key, f)

    def ranking_signals(self, prm: Params, field: str):
        """24h 涨幅榜 / 成交额榜前 top_n(只在流动性合格的币里排)。"""
        key = ("rank", field, prm.min_avg_quote_volume, prm.liquidity_window, prm.top_n)

        def f():
            elig = self.eligible(prm.min_avg_quote_volume, prm.liquidity_window)
            if field == "gain":
                x = self.p["close"] / self.p["close"].shift(1) - 1
            else:
                x = self.p["quote_volume"]
            r = x.where(elig).rank(axis=1, ascending=False, method="first")
            sig = r.le(prm.top_n) & elig
            nan = pd.DataFrame(np.nan, index=self.dates, columns=self.symbols)
            return sig, r.fillna(np.inf), nan, nan
        return self._memo(key, f)

    def signals(self, prm: Params):
        if prm.selection == "qulla":
            return self.qulla_signals(prm)
        if prm.selection == "top_gainers":
            return self.ranking_signals(prm, "gain")
        if prm.selection == "top_volume":
            return self.ranking_signals(prm, "volume")
        raise ValueError(prm.selection)


# ----------------------------------------------------------------------------
# 逐日撮合
# ----------------------------------------------------------------------------

def run_backtest(sb: SignalBuilder, prm: Params) -> Result:
    p = sb.p
    dates = sb.dates
    start = pd.Timestamp(prm.start)
    end = pd.Timestamp(prm.end) if prm.end else dates[-1]
    i0 = max(1, int(dates.searchsorted(start)))
    i1 = int(dates.searchsorted(end, side="right")) - 1

    O, H, L, C = (p[k].to_numpy() for k in ("open", "high", "low", "close"))
    sig, score, blen, bhigh = (x.to_numpy() for x in sb.signals(prm))
    ATR = sb.atr(prm.atr_period).to_numpy()
    MA = sb.sma(prm.ma_exit).to_numpy()
    syms = sb.symbols
    # 每个币最后一根有效 K 线(用于处理下架)
    has = ~np.isnan(C)
    last_valid = np.where(has.any(axis=0), len(C) - 1 - np.argmax(has[::-1], axis=0), -1)

    slip, fee = prm.slippage, prm.fee
    cash = prm.initial_capital
    pos: dict[int, dict] = {}       # 列号 → 持仓
    last_px = np.full(C.shape[1], np.nan)
    pending_entries: list[tuple[float, int]] = []
    pending_exits: set[int] = set()
    trades: list[dict] = []
    eq_vals, exp_vals = [], []
    equity_prev = cash
    funnel = {"signals": 0, "skip_gap_below_stop": 0, "skip_atr": 0,
              "skip_min_notional": 0, "skip_no_cash": 0, "filled": 0, "size_capped": 0}

    def sell(j: int, qty: float, raw_px: float, d: int, reason: str, final: bool):
        nonlocal cash
        ps = pos[j]
        px = raw_px * (1 - slip)
        proceeds = qty * px * (1 - fee)
        cash += proceeds
        ps["qty"] -= qty
        ps["proceeds"] += proceeds
        ps["legs"].append((dates[d], reason, qty, px))
        if final:
            pnl = ps["proceeds"] - ps["cost"]
            partial = next((lg for lg in ps["legs"] if lg[1] == "partial"), None)
            trades.append({
                "symbol": syms[j],
                "signal_date": ps["signal_date"],
                "entry_date": ps["entry_date"],
                "exit_date": dates[d],
                "entry_price": ps["entry_px"],
                "stop": ps["init_stop"],
                "stop_pct": (ps["entry_px"] - ps["init_stop"]) / ps["entry_px"],
                "qty": ps["init_qty"],
                "notional": ps["init_qty"] * ps["entry_px"],
                "risk_usdt": ps["risk"],
                "risk_pct_equity": ps["risk"] / ps["equity_at_entry"],
                "cost": ps["cost"],
                "proceeds": ps["proceeds"],
                "pnl": pnl,
                "R": pnl / ps["risk"],
                "return_pct": pnl / ps["cost"],
                "hold_days": (dates[d] - ps["entry_date"]).days,
                "partial_taken": ps["partial_done"],
                "partial_date": partial[0] if partial else pd.NaT,
                "partial_price": partial[3] if partial else np.nan,
                "exit_price": px,
                "exit_reason": reason,
                "base_len": ps["base_len"],
                "base_high": ps["base_high"],
            })
            del pos[j]

    for d in range(i0, i1 + 1):
        o, l, c = O[d], L[d], C[d]
        valid = ~np.isnan(c)
        last_px[valid] = c[valid]

        # (a) 前一天收盘确认的均线离场 → 今日开盘卖出
        for j in list(pending_exits):
            if j in pos:
                px = o[j] if not np.isnan(o[j]) else last_px[j]
                sell(j, pos[j]["qty"], px, d, "ma_exit", final=True)
        pending_exits.clear()

        # (b) 新开仓:按信号强弱排序,资金不足即停止
        for _, j in sorted(pending_entries):
            if j in pos or np.isnan(o[j]):
                continue
            funnel["signals"] += 1
            atr_prev = ATR[d - 1, j]
            if np.isnan(atr_prev):
                funnel["skip_atr"] += 1
                continue
            entry_px = o[j] * (1 + slip)
            if prm.stop_mode == "low":
                stop = L[d - 1, j]
                if o[j] - stop <= 0:
                    funnel["skip_gap_below_stop"] += 1
                    continue
                if o[j] - stop > prm.atr_mult * atr_prev:
                    funnel["skip_atr"] += 1
                    continue
            else:
                stop = o[j] - prm.stop_atr_mult * atr_prev
                if stop <= 0:
                    funnel["skip_atr"] += 1
                    continue
            risk_amt = equity_prev * prm.risk_per_trade
            qty = risk_amt / (entry_px - stop)
            want = qty * entry_px
            notional = min(want, equity_prev * prm.max_position_pct, cash / (1 + fee))
            if prm.min_notional > 0 and notional < prm.min_notional:
                if prm.min_notional_mode == "bump" and cash / (1 + fee) >= prm.min_notional:
                    notional = prm.min_notional
                else:
                    funnel["skip_min_notional"] += 1
                    continue
            if notional <= 0:
                funnel["skip_no_cash"] += 1
                continue
            funnel["filled"] += 1
            if notional < want * 0.999:
                funnel["size_capped"] += 1
            qty = notional / entry_px
            cost = notional * (1 + fee)
            cash -= cost
            pos[j] = {
                "qty": qty, "init_qty": qty, "entry_px": entry_px, "stop": stop,
                "init_stop": stop, "risk": qty * (entry_px - stop), "cost": cost,
                "proceeds": 0.0, "entry_date": dates[d], "entry_i": d,
                "signal_date": dates[d - 1], "partial_done": False, "legs": [],
                "equity_at_entry": equity_prev,
                "base_len": blen[d - 1, j], "base_high": bhigh[d - 1, j],
            }
        pending_entries = []

        # (c) 盘中止损;已下架 / 无数据的币按最后价格平仓
        for j in list(pos):
            ps = pos[j]
            if d > last_valid[j]:
                sell(j, ps["qty"], last_px[j], d, "delisted", final=True)
                continue
            if np.isnan(l[j]):
                continue
            if l[j] <= ps["stop"]:
                px = min(o[j], ps["stop"])  # 跳空低开则按开盘价成交
                reason = "breakeven_stop" if ps["partial_done"] else "stop"
                sell(j, ps["qty"], px, d, reason, final=True)

        # (d) 收盘管理:到期部分止盈 + 止损上移到成本;之后检查均线离场
        for j in list(pos):
            ps = pos[j]
            if np.isnan(c[j]):
                continue
            held = d - ps["entry_i"] + 1  # 已持有的日线根数(含买入当天)
            if not ps["partial_done"] and held >= prm.partial_days:
                q = ps["qty"] * prm.partial_frac
                ok = True
                if prm.min_notional > 0:
                    ok = (q * c[j] >= prm.min_notional
                          and (ps["qty"] - q) * c[j] >= prm.min_notional)
                if ok and q > 0:
                    sell(j, q, c[j], d, "partial", final=False)
                ps["partial_done"] = True
                ps["stop"] = max(ps["stop"], ps["entry_px"])
            elif ps["partial_done"] and not np.isnan(MA[d, j]) and c[j] < MA[d, j]:
                pending_exits.add(j)

        # (e) 盯市
        mtm = sum(ps["qty"] * last_px[j] for j, ps in pos.items())
        equity_prev = cash + mtm
        eq_vals.append(equity_prev)
        exp_vals.append(mtm / equity_prev if equity_prev > 0 else 0.0)

        # (f) 收盘后生成信号,次日开盘执行
        if d < i1:
            hits = np.flatnonzero(sig[d])
            pending_entries = [(score[d, j], j) for j in hits if j not in pos]

    # 回测结束仍持有的仓位按最后收盘价平仓(标记为 open_at_end)
    for j in list(pos):
        sell(j, pos[j]["qty"], last_px[j], i1, "open_at_end", final=True)
    if eq_vals:
        eq_vals[-1] = cash

    idx = dates[i0:i1 + 1]
    return Result(prm, pd.Series(eq_vals, idx, name="equity"), pd.DataFrame(trades),
                  pd.Series(exp_vals, idx, name="exposure"), funnel)


def buy_and_hold(panel: dict[str, pd.DataFrame], symbol: str, prm: Params) -> pd.Series:
    c = panel["close"][symbol]
    o = panel["open"][symbol]
    start = pd.Timestamp(prm.start)
    end = pd.Timestamp(prm.end) if prm.end else c.index[-1]
    c = c.loc[start:end].ffill()
    first_open = o.loc[start:end].dropna().iloc[0]
    qty = prm.initial_capital * (1 - prm.fee) / (first_open * (1 + prm.slippage))
    return (c * qty).rename(f"{symbol} buy&hold")
