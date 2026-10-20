"""绩效指标。"""
from __future__ import annotations

import numpy as np
import pandas as pd


def max_drawdown(eq: pd.Series) -> float:
    if eq.empty:
        return np.nan
    return float((eq / eq.cummax() - 1).min())


def sharpe(eq: pd.Series, periods: int = 365) -> float:
    r = eq.pct_change().dropna()
    if len(r) < 2 or r.std() == 0:
        return np.nan
    return float(r.mean() / r.std() * np.sqrt(periods))


def cagr(eq: pd.Series) -> float:
    if len(eq) < 2 or eq.iloc[0] <= 0:
        return np.nan
    years = (eq.index[-1] - eq.index[0]).days / 365.25
    if years <= 0:
        return np.nan
    return float((eq.iloc[-1] / eq.iloc[0]) ** (1 / years) - 1)


def trade_stats(trades: pd.DataFrame) -> dict:
    if trades is None or trades.empty:
        return {"trades": 0, "win_rate": np.nan, "avg_win_R": np.nan,
                "avg_loss_R": np.nan, "payoff": np.nan, "expectancy_R": np.nan,
                "total_R": 0.0, "profit_factor": np.nan, "stop_rate": np.nan,
                "max_consec_stops": 0, "max_consec_losses": 0}
    R = trades["R"]
    wins, losses = R[R > 0], R[R <= 0]
    avg_w = wins.mean() if len(wins) else np.nan
    avg_l = losses.mean() if len(losses) else np.nan
    gross_l = -trades.loc[trades.pnl <= 0, "pnl"].sum()
    return {
        "trades": int(len(R)),
        "win_rate": float((R > 0).mean()),
        "avg_win_R": float(avg_w),
        "avg_loss_R": float(avg_l),
        "payoff": float(avg_w / abs(avg_l)) if avg_l and not np.isnan(avg_l) else np.nan,
        "expectancy_R": float(R.mean()),
        "total_R": float(R.sum()),
        "profit_factor": float(trades.loc[trades.pnl > 0, "pnl"].sum() / gross_l)
        if gross_l > 0 else np.nan,
        # 被初始止损打掉的比例(不含部分止盈后的保本止损)
        "stop_rate": float((trades.exit_reason == "stop").mean()),
        "max_consec_stops": max_streak(trades.sort_values("exit_date").exit_reason == "stop"),
        # 最长连续亏损笔数(按平仓顺序,净盈亏 ≤ 0 记为亏损)
        "max_consec_losses": max_streak(trades.sort_values("exit_date").R <= 0),
    }


def max_streak(flags: pd.Series) -> int:
    best = cur = 0
    for f in flags:
        cur = cur + 1 if f else 0
        best = max(best, cur)
    return best


def drawdown_info(eq: pd.Series) -> dict:
    """最大回撤及其时间:峰值日、谷底日、恢复日(重新创新高),以及最长的水下期。"""
    if eq.empty:
        return {}
    peak = eq.cummax()
    dd = eq / peak - 1
    trough = dd.idxmin()
    peak_date = eq.loc[:trough].idxmax()
    after = eq.loc[trough:]
    rec = after[after >= eq.loc[peak_date]]
    rec_date = rec.index[0] if len(rec) else None
    # 最长水下期:任意一次从前高到收复前高(或到数据结束)的天数
    under = dd < 0
    longest, start = 0, None
    for d, u in under.items():
        if u and start is None:
            start = d
        elif not u and start is not None:
            longest = max(longest, (d - start).days)
            start = None
    if start is not None:
        longest = max(longest, (eq.index[-1] - start).days)
    return {
        "max_drawdown": float(dd.min()),
        "dd_peak": peak_date, "dd_trough": trough, "dd_recovery": rec_date,
        "dd_fall_days": (trough - peak_date).days,
        "dd_recover_days": (rec_date - trough).days if rec_date is not None else None,
        "dd_total_days": ((rec_date or eq.index[-1]) - peak_date).days,
        "longest_underwater_days": longest,
    }


def monte_carlo_dd(trades: pd.DataFrame, n: int = 1000, seed: int = 42,
                   risk_scale: float = 1.0) -> dict:
    """把交易顺序随机打乱 n 次,逐笔复利,统计最大回撤 / 最长连亏的分布。

    每笔对账户的影响 = R × 开仓时风险占账户比例(≈ 0.5%);risk_scale=2 近似 1% 风险。
    按“逐笔平仓”计算,不含持仓期间的浮动盈亏和多笔重叠,所以和日线权益曲线的回撤不完全相同。
    """
    if trades is None or len(trades) < 2:
        return {}
    t = trades.sort_values("exit_date")
    r = (t.R * t.risk_pct_equity * risk_scale).to_numpy()
    R = t.R.to_numpy()
    rng = np.random.default_rng(seed)

    def path_dd(x):
        eq = np.concatenate([[1.0], np.cumprod(1 + x)])
        return float((eq / np.maximum.accumulate(eq) - 1).min())

    def r_dd(x):
        c = np.concatenate([[0.0], np.cumsum(x)])
        return float((c - np.maximum.accumulate(c)).min())

    def streak(x):
        best = cur = 0
        for v in x:
            cur = cur + 1 if v <= 0 else 0
            best = max(best, cur)
        return best

    dds, rdds, streaks = np.empty(n), np.empty(n), np.empty(n)
    for i in range(n):
        p = rng.permutation(len(r))
        dds[i], rdds[i], streaks[i] = path_dd(r[p]), r_dd(R[p]), streak(R[p])
    q = lambda a, x: float(np.quantile(a, x))  # noqa: E731
    return {
        "n": n, "trades": len(r), "dd_samples": dds,
        "hist_dd": path_dd(r), "hist_dd_R": r_dd(R), "hist_streak": streak(R),
        "dd_median": q(dds, 0.5), "dd_p95": q(dds, 0.05), "dd_p99": q(dds, 0.01),
        "dd_worst": float(dds.min()),
        "ddR_median": q(rdds, 0.5), "ddR_p95": q(rdds, 0.05), "ddR_p99": q(rdds, 0.01),
        "streak_median": q(streaks, 0.5), "streak_p95": q(streaks, 0.95),
        "streak_p99": q(streaks, 0.99),
    }


def summary(eq: pd.Series, trades: pd.DataFrame, initial: float | None = None) -> dict:
    """initial: 初始资金;给出时把它作为第一天开盘前的权益点。"""
    if initial is not None and len(eq):
        eq = pd.concat([pd.Series([initial], [eq.index[0] - pd.Timedelta(days=1)]), eq])
    out = {
        "start": eq.index[0].date().isoformat(),
        "end": eq.index[-1].date().isoformat(),
        "final_equity": float(eq.iloc[-1]),
        "total_return": float(eq.iloc[-1] / eq.iloc[0] - 1) if len(eq) else np.nan,
        "cagr": cagr(eq),
        "max_drawdown": max_drawdown(eq),
        "sharpe": sharpe(eq),
    }
    out.update(trade_stats(trades))
    return out


def yearly(eq: pd.Series, trades: pd.DataFrame, bench: pd.Series) -> pd.DataFrame:
    rows = []
    for y, e in eq.groupby(eq.index.year):
        prev = eq[eq.index.year < y]
        base = prev.iloc[-1] if len(prev) else e.iloc[0]
        b = bench[bench.index.year == y]
        bprev = bench[bench.index.year < y]
        bbase = bprev.iloc[-1] if len(bprev) else b.iloc[0]
        t = trades[trades.entry_date.dt.year == y] if not trades.empty else trades
        ts = trade_stats(t)
        rows.append({
            "year": y,
            "return": e.iloc[-1] / base - 1,
            "max_drawdown": max_drawdown(pd.concat([pd.Series([base]), e])),
            "sharpe": sharpe(e),
            "btc_return": b.iloc[-1] / bbase - 1,
            "trades": ts["trades"],
            "win_rate": ts["win_rate"],
            "expectancy_R": ts["expectancy_R"],
            "total_R": ts["total_R"],
        })
    return pd.DataFrame(rows).set_index("year")
