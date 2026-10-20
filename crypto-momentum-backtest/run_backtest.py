"""运行全部实验并生成报告:results/REPORT.md + 图表 + CSV。

用法:
    python run_backtest.py                 # 全部实验(约 10 分钟)
    python run_backtest.py --quick         # 只跑严格版 / 放宽版 两组主结果
    python run_backtest.py --top-pct 0.05 --ma-exit 10 --risk 0.01 --stop atr --stop-atr-mult 2
                                           # 自定义单次回测(只输出资金曲线和交易明细)
"""
from __future__ import annotations

import argparse
import itertools
import time
from pathlib import Path

import numpy as np
import pandas as pd

from cmb.charts import plot_equity, plot_mc_hist, plot_trade
from cmb.engine import CONDITIONS, Params, SignalBuilder, buy_and_hold, run_backtest
from cmb.metrics import drawdown_info, monte_carlo_dd, summary, yearly
from cmb.universe import load_panel

OUT = Path("results")
IS_END, OOS_START = "2023-12-31", "2024-01-01"

# 严格版 = 需求里给的默认参数,原样不动
STRICT = Params()
# 放宽版:严格版在 6.8 年里只出 4 笔交易,无法做统计。按“最少改动、让交易数 ≥ 100”放宽两处瓶颈
# (只看交易笔数选,不看收益):强势排名 2% → 10%,横盘区间 20% → 40%;止损改为 1×ATR
# (“突破日最低点 + 距离 > 1ATR 就放弃”这一条会再过滤掉约 70% 的信号)。其余条件完全相同。
RELAXED = Params(top_pct=0.10, cons_max_range=0.40, stop_mode="atr", stop_atr_mult=1.0)
VARIANTS = {"严格版": STRICT, "放宽版": RELAXED}

STOPS = {"突破日最低点(>1ATR 放弃)": dict(stop_mode="low"),
         "1×ATR": dict(stop_mode="atr", stop_atr_mult=1.0),
         "2×ATR": dict(stop_mode="atr", stop_atr_mult=2.0)}


# ---------------------------------------------------------------------------- 格式化

def isnan(x):
    return x is None or (isinstance(x, (float, np.floating)) and np.isnan(x))


def pct(x, d=1):
    return "—" if isnan(x) else f"{x * 100:.{d}f}%"


def num(x, d=2):
    return "—" if isnan(x) else f"{x:.{d}f}"


def md_table(df: pd.DataFrame, fmt: dict | None = None, index=True, rename=None) -> str:
    fmt = fmt or {}
    d = df.copy()
    for c, f in fmt.items():
        if c in d:
            d[c] = d[c].map(f)
    if index:
        d = d.reset_index()
    cols = [str((rename or {}).get(c, c)) for c in d.columns]
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for _, r in d.iterrows():
        lines.append("| " + " | ".join(str(v) for v in r.values) + " |")
    return "\n".join(lines)


def metrics_row(s: dict, trades=True) -> dict:
    row = {"总收益": pct(s["total_return"]), "年化": pct(s["cagr"]),
           "最大回撤": pct(s["max_drawdown"]), "夏普": num(s["sharpe"])}
    if trades:
        row.update({"交易数": s["trades"], "胜率": pct(s["win_rate"]),
                    "盈亏比": num(s["payoff"]), "期望值(R)": num(s["expectancy_R"], 3)})
    else:
        row.update({"交易数": "—", "胜率": "—", "盈亏比": "—", "期望值(R)": "—"})
    return row


TRADE_COLS = ["symbol", "signal_date", "entry_date", "exit_date", "entry_price",
              "stop_pct", "hold_days", "pnl", "R", "exit_reason"]


def trades_table(t: pd.DataFrame) -> str:
    if t.empty:
        return "(无交易)"
    d = t[TRADE_COLS].copy()
    for c in ("signal_date", "entry_date", "exit_date"):
        d[c] = d[c].dt.date
    return md_table(d, {"entry_price": lambda x: f"{x:.6g}", "stop_pct": pct,
                        "pnl": lambda x: f"{x:,.1f}", "R": lambda x: f"{x:+.2f}"},
                    index=False)


def run(sb, prm):
    r = run_backtest(sb, prm)
    return r, summary(r.equity, r.trades, prm.initial_capital)


def stop_label(kw) -> str:
    return "突破日低点" if kw["stop_mode"] == "low" else f"{kw['stop_atr_mult']:g}×ATR"


# ---------------------------------------------------------------------------- 主流程

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data/raw")
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--selection", choices=["qulla", "top_gainers", "top_volume"])
    ap.add_argument("--top-pct", type=float)
    ap.add_argument("--cons-range", type=float)
    ap.add_argument("--ma-exit", type=int)
    ap.add_argument("--risk", type=float)
    ap.add_argument("--stop", choices=["low", "atr"])
    ap.add_argument("--stop-atr-mult", type=float)
    ap.add_argument("--min-volume", type=float)
    ap.add_argument("--start")
    ap.add_argument("--end")
    args = ap.parse_args()

    OUT.mkdir(exist_ok=True)
    t0 = time.time()
    panel = load_panel(args.data)
    sb = SignalBuilder(panel)
    print(f"载入 {len(sb.symbols)} 个交易对, {sb.dates[0].date()} ~ {sb.dates[-1].date()}")

    over = {k: v for k, v in {
        "selection": args.selection, "top_pct": args.top_pct, "cons_max_range": args.cons_range,
        "ma_exit": args.ma_exit, "risk_per_trade": args.risk, "stop_mode": args.stop,
        "stop_atr_mult": args.stop_atr_mult, "min_avg_quote_volume": args.min_volume,
        "start": args.start, "end": args.end}.items() if v is not None}
    if over:
        prm = STRICT.with_(**over)
        r, s = run(sb, prm)
        btc = buy_and_hold(panel, "BTCUSDT", prm)
        r.trades.to_csv(OUT / "trades_custom.csv", index=False)
        plot_equity({"Strategy": r.equity, "BTC buy & hold": btc}, OUT / "equity_custom.png",
                    "Custom run")
        print({k: s[k] for k in ("total_return", "cagr", "max_drawdown", "sharpe", "trades",
                                 "win_rate", "expectancy_R", "stop_rate", "max_consec_stops")})
        print(f"→ {OUT / 'trades_custom.csv'}, {OUT / 'equity_custom.png'}")
        return

    # ---------------- 主结果 ----------------
    main_res = {k: run(sb, p) for k, p in VARIANTS.items()}
    btc = buy_and_hold(panel, "BTCUSDT", STRICT)
    bs = summary(btc, pd.DataFrame(), STRICT.initial_capital)
    for k, (r, s) in main_res.items():
        print(k, {x: s[x] for x in ("total_return", "trades", "expectancy_R", "sharpe")})
        r.trades.to_csv(OUT / f"trades_{'strict' if k == '严格版' else 'relaxed'}.csv", index=False)
    pd.DataFrame({"strict": main_res["严格版"][0].equity, "relaxed": main_res["放宽版"][0].equity,
                  "btc": btc}).to_csv(OUT / "equity_daily.csv")
    plot_equity({"Strict (spec defaults)": main_res["严格版"][0].equity,
                 "Relaxed (top10%, range<40%, 1xATR stop)": main_res["放宽版"][0].equity,
                 "BTC buy & hold": btc}, OUT / "equity_curve.png",
                f"Qullamaggie breakout on Binance USDT spot · 10,000 USDT · "
                f"{STRICT.start} ~ {sb.dates[-1].date()}")
    if args.quick:
        return

    # ---------------- 横盘条件漏斗(严格版) ----------------
    order = ["strength", "near_high", "ma_trend", "tight", "atr_contract", "vol_dryup",
             "breakout_vol"]

    def nsig(prm):
        return int(sb.signals(prm)[0].loc[STRICT.start:].to_numpy().sum())

    funnel_rows, on = [], []
    funnel_rows.append({"累计加入的条件": "只要求:流动性合格 + 收盘突破前 15 天最高价",
                        "满足条件的 币×天": nsig(STRICT.with_(disabled=tuple(CONDITIONS)))})
    for c in order:
        on.append(c)
        n = nsig(STRICT.with_(disabled=tuple(x for x in CONDITIONS if x not in on)))
        funnel_rows.append({"累计加入的条件": "+ " + CONDITIONS[c], "满足条件的 币×天": n})
    funnel = pd.DataFrame(funnel_rows)
    strict_f = main_res["严格版"][0].funnel
    print(f"漏斗完成 ({time.time() - t0:.0f}s)")

    # ---------------- 选币方式 × 止损方式 ----------------
    rows = []
    sel_variants = {"原方案·严格版": STRICT, "原方案·放宽版": RELAXED.with_(stop_mode="low"),
                    "24h 涨幅榜前 10": STRICT.with_(selection="top_gainers"),
                    "24h 成交额榜前 10": STRICT.with_(selection="top_volume")}
    for (sname, sp), (stname, stkw) in itertools.product(sel_variants.items(), STOPS.items()):
        r, m = run(sb, sp.with_(**stkw))
        rows.append({"选币": sname, "止损": stname, "交易数": m["trades"],
                     "被止损比例": pct(m["stop_rate"]), "最多连续止损": m["max_consec_stops"],
                     "最长连亏": m["max_consec_losses"],
                     "胜率": pct(m["win_rate"]), "期望值(R)": num(m["expectancy_R"], 3),
                     "R 中位数": num(r.trades.R.median() if len(r.trades) else np.nan),
                     "总收益": pct(m["total_return"]), "最大回撤": pct(m["max_drawdown"]),
                     "夏普": num(m["sharpe"]),
                     "仓位被25%上限截断": pct(r.funnel["size_capped"] / max(1, r.funnel["filled"]), 0)})
    combo = pd.DataFrame(rows)
    combo.to_csv(OUT / "selection_x_stop.csv", index=False)
    print(f"选币×止损完成 ({time.time() - t0:.0f}s)")

    # ---------------- 消融:去掉每个条件 ----------------
    abl = {}
    for vname, vp in VARIANTS.items():
        rows = []
        base_r, base_m = main_res[vname]
        for c in [None, *CONDITIONS]:
            if c is None:
                r, m = base_r, base_m
            else:
                r, m = run(sb, vp.with_(disabled=(c,)))
            t = r.trades
            e_is = t[t.entry_date <= IS_END].R.mean() if len(t) else np.nan
            e_oos = t[t.entry_date >= OOS_START].R.mean() if len(t) else np.nan
            rows.append({"name": "基准(全部条件)" if c is None else "去掉 " + CONDITIONS[c],
                         "trades": m["trades"], "exp": m["expectancy_R"], "totR": m["total_R"],
                         "ret": m["total_return"], "sharpe": m["sharpe"],
                         "mdd": m["max_drawdown"], "e_is": e_is, "e_oos": e_oos})
        df = pd.DataFrame(rows)
        b = df.iloc[0]
        verdicts = []
        for i, r in df.iterrows():
            d = r.exp - b.exp
            if i == 0:
                verdicts.append("")
            elif isnan(d):
                verdicts.append("—")
            elif abs(d) < 0.05:
                verdicts.append(f"影响小({d:+.2f}R)")
            elif d < 0:
                verdicts.append(f"**有用**:去掉后期望 {d:+.2f}R")
            else:
                verdicts.append(f"没用/有害:去掉后期望 {d:+.2f}R")
        df["结论"] = verdicts
        abl[vname] = df
        df.to_csv(OUT / f"ablation_{'strict' if vname == '严格版' else 'relaxed'}.csv", index=False)
    print(f"消融完成 ({time.time() - t0:.0f}s)")

    # ---------------- 参数敏感性 ----------------
    rows = []
    for vname, vp in VARIANTS.items():
        for tp, ma, rk in itertools.product((0.01, 0.02, 0.05), (10, 20), (0.005, 0.01)):
            r, m = run(sb, vp.with_(top_pct=tp, ma_exit=ma, risk_per_trade=rk))
            rows.append({"版本": vname, "前N%": f"{tp:.0%}", "均线": ma, "风险": f"{rk:.1%}",
                         **metrics_row(m)})
    sens = pd.DataFrame(rows)
    sens.to_csv(OUT / "sensitivity.csv", index=False)
    rows = []
    for tp, cr in itertools.product((0.02, 0.05, 0.10, 0.20), (0.20, 0.30, 0.40)):
        row = {"强势前N%": f"{tp:.0%}", "横盘区间<": f"{cr:.0%}"}
        for stkw, tag in ((STOPS["突破日最低点(>1ATR 放弃)"], "低点止损"), (STOPS["1×ATR"], "1ATR止损")):
            r, m = run(sb, STRICT.with_(top_pct=tp, cons_max_range=cr, **stkw))
            row[f"{tag}·交易数"] = m["trades"]
            row[f"{tag}·期望(R)"] = num(m["expectancy_R"], 3)
            row[f"{tag}·总收益"] = pct(m["total_return"])
        rows.append(row)
    bottleneck = pd.DataFrame(rows)
    bottleneck.to_csv(OUT / "bottleneck_grid.csv", index=False)
    print(f"敏感性完成 ({time.time() - t0:.0f}s)")

    # ---------------- 样本外:2020–2023 调参,2024 至今检验 ----------------
    sig_grid = list(itertools.product((0.02, 0.05, 0.10, 0.20), (0.20, 0.30, 0.40),
                                      (0.7, 1.0), (1.0, 1.5)))
    exec_grid = list(itertools.product(STOPS.values(), (10, 20)))
    is_p = STRICT.with_(end=IS_END)
    oos_p = STRICT.with_(start=OOS_START, end=None)
    rows = []
    for tp, cr, vd, bv in sig_grid:          # 信号参数在外层,便于缓存
        for stkw, ma in exec_grid:
            kw = dict(top_pct=tp, cons_max_range=cr, vol_dryup_ratio=vd,
                      breakout_vol_mult=bv, ma_exit=ma, stop_atr_mult=1.0)
            kw.update(stkw)
            _, mi = run(sb, is_p.with_(**kw))
            _, mo = run(sb, oos_p.with_(**kw))
            rows.append({**kw, "stop": stop_label(kw),
                         **{f"is_{k}": mi[k] for k in ("sharpe", "total_return", "max_drawdown",
                                                       "expectancy_R", "trades")},
                         **{f"oos_{k}": mo[k] for k in ("sharpe", "total_return", "max_drawdown",
                                                        "expectancy_R", "trades")}})
    opt = pd.DataFrame(rows)
    opt.to_csv(OUT / "optimization_grid.csv", index=False)
    elig = opt[opt.is_trades >= 30]
    best = elig.sort_values("is_sharpe", ascending=False).iloc[0]
    best_kw = {k: best[k] for k in ("top_pct", "cons_max_range", "vol_dryup_ratio",
                                    "breakout_vol_mult", "stop_mode", "stop_atr_mult")}
    best_kw["ma_exit"] = int(best["ma_exit"])
    rank_corr = opt.is_sharpe.rank().corr(opt.oos_sharpe.rank())
    oos_rows, oos_curves = [], {}
    btc_is = buy_and_hold(panel, "BTCUSDT", is_p)
    btc_oos = buy_and_hold(panel, "BTCUSDT", oos_p)
    relaxed_kw = dict(top_pct=0.10, cons_max_range=0.40, stop_mode="atr", stop_atr_mult=1.0)
    for label, kw in (("样本内最优参数", best_kw), ("严格版", {}), ("放宽版", relaxed_kw)):
        for per, pp in (("样本内 2020–2023", is_p), ("样本外 2024–至今", oos_p)):
            r, m = run(sb, pp.with_(**kw))
            oos_rows.append({"": f"{per} · {label}", **metrics_row(m)})
            if per.startswith("样本外"):
                oos_curves[label] = r.equity
    for per, b in (("样本内 2020–2023", btc_is), ("样本外 2024–至今", btc_oos)):
        oos_rows.append({"": f"{per} · BTC 持有",
                         **metrics_row(summary(b, pd.DataFrame(), STRICT.initial_capital), False)})
    oos_tbl = pd.DataFrame(oos_rows).set_index("")
    plot_equity({"Tuned on 2020-2023": oos_curves["样本内最优参数"],
                 "Relaxed": oos_curves["放宽版"], "BTC buy & hold": btc_oos},
                OUT / "equity_oos.png", f"Out-of-sample {OOS_START} ~ {sb.dates[-1].date()}")
    oos_pos = (opt.oos_expectancy_R > 0).mean()
    is_pos = (opt.is_expectancy_R > 0).mean()
    print(f"样本外完成 ({time.time() - t0:.0f}s)")

    # ---------------- K 线图 ----------------
    chart_dir = OUT / "trade_charts"
    chart_dir.mkdir(exist_ok=True)
    for f in chart_dir.glob("*.png"):
        f.unlink()
    ts = main_res["严格版"][0].trades
    picks = [("严格版", r) for _, r in ts.sort_values("entry_date").iterrows()]
    tr = main_res["放宽版"][0].trades.sort_values("R")
    if len(tr):
        mid = tr.iloc[2:-2]
        sel = pd.concat([tr.tail(2).iloc[::-1], tr.head(2),
                         mid.sample(min(2, len(mid)), random_state=7)])
        picks += [("放宽版", r) for _, r in sel.iterrows()]
    charts = []
    for i, (vname, t) in enumerate(picks, 1):
        tag = "strict" if vname == "严格版" else "relaxed"
        fn = f"{i:02d}_{tag}_{t.symbol}_{t.entry_date:%Y%m%d}.png"
        title = (f"[{tag}] {t.symbol}  entry {t.entry_date:%Y-%m-%d} @ {t.entry_price:.4g}  "
                 f"stop {t.stop:.4g} (-{t.stop_pct:.1%})  exit {t.exit_date:%Y-%m-%d} "
                 f"{t.exit_reason}  R={t.R:+.2f}")
        plot_trade(panel, t, chart_dir / fn, STRICT.ma_fast, STRICT.ma_slow,
                   STRICT.runup_window, title)
        charts.append((vname, t, fn))
    print(f"K 线图完成 ({time.time() - t0:.0f}s)")

    # ---------------- 小资金(10 USDT,用放宽版) ----------------
    rx, rs = main_res["放宽版"]
    ss = main_res["严格版"][1]
    small = {mode: run(sb, RELAXED.with_(initial_capital=10.0, min_notional=5.0,
                                         min_notional_mode=mode)) for mode in ("skip", "bump")}
    t = rx.trades
    rt_cost = 2 * (RELAXED.fee + RELAXED.slippage)
    cost_R = rt_cost / t.stop_pct
    need_10u = np.minimum(10 * RELAXED.risk_per_trade / t.stop_pct, 10 * RELAXED.max_position_pct)
    bump_t = small["bump"][0].trades
    bump_risk = bump_t.risk_pct_equity if len(bump_t) else pd.Series([np.nan])
    stop_q90 = float(t.stop_pct.quantile(0.9))
    acct_need = max(5 / RELAXED.max_position_pct, 15 * stop_q90 / RELAXED.risk_per_trade)

    # ---------------- 幸存者偏差 ----------------
    last = panel["close"].apply(lambda c: c.last_valid_index())
    delisted = last[last < sb.dates[-1] - pd.Timedelta(days=3)]
    all_trades = pd.concat([main_res[k][0].trades for k in VARIANTS])
    traded_delisted = all_trades[all_trades.symbol.isin(delisted.index)]

    # ---------------- 回撤持续时间 + 蒙特卡洛 ----------------
    dd_rows = []
    for name, eq, tt in (("严格版", main_res["严格版"][0].equity, ts),
                         ("放宽版", rx.equity, rx.trades), ("BTC 买入持有", btc, None)):
        di = drawdown_info(pd.concat([pd.Series([STRICT.initial_capital],
                                                [eq.index[0] - pd.Timedelta(days=1)]), eq]))
        dd_rows.append({
            "": name, "最大回撤": pct(di["max_drawdown"]),
            "峰值日": di["dd_peak"].date(), "谷底日": di["dd_trough"].date(),
            "下跌天数": di["dd_fall_days"],
            "收复前高日": di["dd_recovery"].date() if di["dd_recovery"] is not None else "未收复",
            "恢复天数": di["dd_recover_days"] if di["dd_recover_days"] is not None
            else f"> {(eq.index[-1] - di['dd_trough']).days}",
            "回撤总时长(天)": di["dd_total_days"], "最长水下期(天)": di["longest_underwater_days"],
            "最长连亏(笔)": "—" if tt is None else summary(eq, tt)["max_consec_losses"],
            "最多连续止损(笔)": "—" if tt is None else summary(eq, tt)["max_consec_stops"],
        })
    dd_tbl = pd.DataFrame(dd_rows).set_index("")
    mc = {k: monte_carlo_dd(rx.trades, n=1000, seed=42, risk_scale=k) for k in (1.0, 2.0)}
    plot_mc_hist(mc[1.0]["dd_samples"], mc[1.0]["hist_dd"], mc[1.0]["dd_median"],
                 mc[1.0]["dd_p95"], OUT / "mc_drawdown.png",
                 f"Monte Carlo: relaxed version, {mc[1.0]['trades']} trades shuffled 1000x, 0.5% risk")
    mc_rows = []
    for k, lab in ((1.0, "每笔风险 0.5%"), (2.0, "每笔风险 1%(约 ×2)")):
        m_ = mc[k]
        mc_rows.append({"": lab, "实际顺序": pct(m_["hist_dd"]), "中位数": pct(m_["dd_median"]),
                        "95% 分位": pct(m_["dd_p95"]), "99% 分位": pct(m_["dd_p99"]),
                        "1000 次中最差": pct(m_["dd_worst"])})
    mc_tbl = pd.DataFrame(mc_rows).set_index("")
    m1 = mc[1.0]
    pd.DataFrame({"max_dd_0.5pct_risk": mc[1.0]["dd_samples"],
                  "max_dd_1pct_risk": mc[2.0]["dd_samples"]}).to_csv(
        OUT / "monte_carlo_drawdowns.csv", index=False)
    print(f"蒙特卡洛完成 ({time.time() - t0:.0f}s)")

    # ---------------- 写报告 ----------------
    yr = yearly(rx.equity, rx.trades, btc)
    yr_strict = ts.groupby(ts.entry_date.dt.year).size() if len(ts) else pd.Series(dtype=int)
    yr["严格版交易数"] = [int(yr_strict.get(y, 0)) for y in yr.index]
    head = pd.DataFrame([{"": "严格版(需求默认参数)", **metrics_row(ss)},
                         {"": "放宽版", **metrics_row(rs)},
                         {"": "BTC 买入持有", **metrics_row(bs, False)}]).set_index("")
    abl_fmt = {"exp": lambda x: num(x, 3), "totR": lambda x: num(x, 1), "ret": pct,
               "sharpe": num, "mdd": pct, "e_is": lambda x: num(x, 3),
               "e_oos": lambda x: num(x, 3)}
    abl_cols = {"name": "", "trades": "交易数", "exp": "期望(R)", "totR": "累计R",
                "ret": "总收益", "sharpe": "夏普", "mdd": "最大回撤",
                "e_is": "期望R·2020–23", "e_oos": "期望R·2024–"}

    chart_md = "\n\n".join(
        f"**{i}. {v} · {t.symbol}** — 信号日 {t.signal_date:%Y-%m-%d},"
        f"横盘 {'—' if isnan(t.base_len) else int(t.base_len)} 天,"
        f"买入 {t.entry_price:.4g},止损 {t.stop:.4g}(−{t.stop_pct:.1%}),"
        f"{t.exit_date:%Y-%m-%d} {t.exit_reason} 离场,R = {t.R:+.2f}"
        f"\n\n![{t.symbol}](trade_charts/{fn})"
        for i, (v, t, fn) in enumerate(charts, 1))

    best_desc = (f"强势前 {best_kw['top_pct']:.0%} / 横盘区间 < {best_kw['cons_max_range']:.0%} / "
                 f"横盘量 < {best_kw['vol_dryup_ratio']:.0%} × 上涨期量 / "
                 f"突破量 ≥ {best_kw['breakout_vol_mult']:g}× 横盘均量 / "
                 f"止损 {stop_label(best_kw)} / MA{best_kw['ma_exit']} 离场")
    top_is = elig.sort_values("is_sharpe", ascending=False).head(10)
    top_is_tbl = md_table(
        top_is[["top_pct", "cons_max_range", "vol_dryup_ratio", "breakout_vol_mult", "stop",
                "ma_exit", "is_trades", "is_sharpe", "is_expectancy_R", "oos_trades",
                "oos_sharpe", "oos_expectancy_R", "oos_total_return"]],
        {"top_pct": lambda x: f"{x:.0%}", "cons_max_range": lambda x: f"{x:.0%}",
         "vol_dryup_ratio": lambda x: f"{x:.0%}", "is_sharpe": num, "oos_sharpe": num,
         "is_expectancy_R": lambda x: num(x, 3), "oos_expectancy_R": lambda x: num(x, 3),
         "oos_total_return": pct}, index=False,
        rename={"top_pct": "强势前N%", "cons_max_range": "区间<", "vol_dryup_ratio": "缩量<",
                "breakout_vol_mult": "放量≥", "stop": "止损", "ma_exit": "均线",
                "is_trades": "内·笔数", "is_sharpe": "内·夏普", "is_expectancy_R": "内·期望R",
                "oos_trades": "外·笔数", "oos_sharpe": "外·夏普",
                "oos_expectancy_R": "外·期望R", "oos_total_return": "外·总收益"})

    p = STRICT
    n_elig = int(sb.eligible(p.min_avg_quote_volume, p.liquidity_window)
                 .loc[p.start:].sum(axis=1).median())
    ctx = dict(ss=ss, rs=rs, bs=bs, combo=combo, abl=abl, opt=opt, rank_corr=rank_corr,
               is_pos=is_pos, oos_pos=oos_pos, mc=m1,
               dd=drawdown_info(rx.equity),
               oos_tbl=oos_tbl, yr=yr, cost_R=cost_R, need_10u=need_10u,
               bump_risk=bump_risk, acct_need=acct_need, rx=rx, best_desc=best_desc)
    report = f"""# Qullamaggie 突破策略 · 币安现货回测报告

> 纯历史数据模拟:未连接任何账户、未下任何真实订单、不需要 API 密钥。
> 生成时间 {pd.Timestamp.now(tz="UTC"):%Y-%m-%d %H:%M} UTC · 数据截至 {sb.dates[-1].date()}

## 1. 数据与范围

- 数据源:币安公开数据仓库 data.binance.vision 的现货 1d K 线(UTC 00:00 切日)
- 交易对:全部 `*USDT` 现货,剔除稳定币 / 法币 / 锚定黄金 / 包装币 / 杠杆代币(UP/DOWN/BULL/BEAR)、
  价格长期贴 1 美元的币、2026 年上线的代币化美股(AAPLB 等)后共 **{len(sb.symbols)}** 个
- 流动性门槛:过去 {p.liquidity_window} 天日均成交额 ≥ **{p.min_avg_quote_volume / 1e6:g}M USDT**(参数 `min_avg_quote_volume`);
  2020 年以来每天合格的币数中位数 {n_elig} 个(所以“前 2%”每个周期只有 2–3 个币)
- 下载从 2019-06 开始(预热),交易区间 **{p.start} ~ {sb.dates[-1].date()}**,覆盖 2020–21 牛市、2022 熊市、2023–24 复苏和之后的行情
- **幸存者偏差**:data.binance.vision 保留了已下架交易对的历史文件,本回测**包含已下架币**。数据中有
  **{len(delisted)}** 个交易对在回测结束前停止交易(LUNA、FTT 等),其中 {traded_delisted.symbol.nunique()} 个被严格版/放宽版买过。
  持仓期间下架则按最后一根收盘价平仓。残留偏差:下架前几天真实流动性比 K 线显示的差;2019-06 前已下架的币不在数据中(不影响 2020 年后的交易)。

## 2. 交易规则

| 环节 | 规则(严格版默认值) | 参数 |
|---|---|---|
| 条件 1 之前强势 | 截至横盘最后一天,30 天或 90 天涨幅在合格币中排名前 {p.top_pct:.0%} | `top_pct`, `mom_lookbacks` |
| 条件 2a 波动收窄 | 最近 {p.cons_min}–{p.cons_max} 天(区间最高 − 区间最低)÷ 当前收盘 < {p.cons_max_range:.0%} | `cons_min/max`, `cons_max_range` |
| 条件 2b ATR 收缩 | 横盘期平均真实波幅%(TR ÷ 前收)< 上涨阶段(横盘前 {p.runup_window} 天)的 {p.atr_contract_ratio:g} 倍 | `runup_window`, `atr_contract_ratio` |
| 条件 3 成交量萎缩 | 横盘期平均成交量 < 上涨阶段平均成交量 × {p.vol_dryup_ratio:.0%} | `vol_dryup_ratio` |
| 条件 4 贴近高点 | 收盘价 ≥ 最近 {p.near_high_window} 天最高价 × (1 − {p.near_high_pct:.0%}) | `near_high_window`, `near_high_pct` |
| 条件 5 均线向上 | 收盘 > MA{p.ma_fast} 且 > MA{p.ma_slow},且 MA{p.ma_slow} 高于 {p.ma_rising_lookback} 天前 | `ma_fast/slow`, `ma_rising_lookback` |
| 买入 | 收盘价 > 横盘区间最高价,且当日成交量 ≥ 横盘均量 × {p.breakout_vol_mult:g} → 下一根开盘买入 | `breakout_vol_mult` |
| 止损 | 突破日最低价;(开盘 − 止损)> {p.atr_mult:g}×ATR({p.atr_period}) 则放弃。可选 `stop_mode="atr"`:成交价 − k×ATR | `stop_mode`, `atr_mult`, `stop_atr_mult` |
| 仓位 | 风险 = 账户 × {p.risk_per_trade:.1%};仓位 = 风险 ÷ 止损距离;上限 {p.max_position_pct:.0%} 账户,不超过现金 | `risk_per_trade`, `max_position_pct` |
| 止盈 | 第 {p.partial_days} 根日线收盘卖 1/3,止损移到成本价;之后收盘跌破 MA{p.ma_exit} → 次日开盘全卖 | `partial_days`, `ma_exit` |
| 成本 | 每次买/卖:手续费 {p.fee:.2%} + 滑点 {p.slippage:.2%};不加杠杆、不做空 | `fee`, `slippage` |

实现细节:条件 1–5 全部用**突破前一天(t−1)收盘**为止的数据判断,横盘区间不含突破当天;
在 5–15 天中取所有条件都满足的**最长**区间;成交量用币本位成交量;同一天多个信号按动量强弱排序,现金用完即止。
**R** = 本笔净盈亏(含全部手续费、滑点)÷ 开仓时计划承担的风险金额。

**为什么有两个版本**:严格版完全按需求的默认参数,6.8 年里只触发了 **{ss['trades']}** 笔交易,样本太小,任何指标都没有统计意义
(漏斗见下表:强势前 2% 的币本身波动极大,5 天以上振幅 < 20% 的情况很少;剩下的信号又有大半因为
“止损距离 > 1ATR”被放弃)。所以另做一个**放宽版**:强势前 2% → **10%**、横盘区间 20% → **40%**、止损改为 **1×ATR**,
其余条件完全一样。放宽幅度只按“交易数 ≥ 100”来定,没有按收益挑;它是否过拟合由第 8 节样本外检验回答。

严格版的条件漏斗(2020 年起,满足条件的“币 × 天”数;最后一行即收盘突破信号数):

{md_table(funnel, index=False)}

信号到成交:{strict_f['signals']} 个信号 → 次日开盘已低于止损 {strict_f['skip_gap_below_stop']} · 止损距离 > 1ATR 放弃 {strict_f['skip_atr']} → 成交 **{strict_f['filled']}** 笔。

## 3. 主结果(初始资金 10,000 USDT)

![资金曲线](equity_curve.png)

{md_table(head)}

- 放宽版:平均盈利 {num(rs['avg_win_R'])}R,平均亏损 {num(rs['avg_loss_R'])}R,累计 {num(rs['total_R'], 1)}R,
  被止损比例 {pct(rs['stop_rate'])},最多连续止损 {rs['max_consec_stops']} 次;平均持仓市值只占账户 {pct(rx.exposure.mean())}
  ——0.5% 风险 + 信号稀少,资金大部分时间闲置,所以收益率绝对值很低。
- 放宽版离场原因:{", ".join(f"{k} {v}" for k, v in rx.trades.exit_reason.value_counts().items())}

### 回撤、持续时间与连亏(按每日收盘权益)

{md_table(dd_tbl)}

“下跌天数”= 峰值到谷底;“恢复天数”= 谷底到重新创新高;“最长水下期”= 任意一次从前高跌下去到收复前高的最长天数
(到数据结束仍未收复则算到最后一天)。“最长连亏”= 按平仓顺序,净盈亏 ≤ 0 的连续笔数(包含保本止损扣完手续费后的小亏)。

### 蒙特卡洛:打乱交易顺序 1000 次的最大回撤分布(放宽版)

历史只是这 {m1['trades']} 笔交易的**一种**排列顺序。把顺序随机打乱 1000 次、逐笔复利,看最大回撤会有多深
(按逐笔平仓计算,不含持仓中的浮动盈亏和多笔重叠,所以“实际顺序”一列与上表的日线回撤略有差别)。

![蒙特卡洛回撤分布](mc_drawdown.png)

{md_table(mc_tbl)}

| 以 R 计(与风险比例无关) | 实际顺序 | 中位数 | 95% 分位 | 99% 分位 |
|---|---|---|---|---|
| 最大回撤(R) | {m1['hist_dd_R']:.1f}R | {m1['ddR_median']:.1f}R | {m1['ddR_p95']:.1f}R | {m1['ddR_p99']:.1f}R |
| 最长连亏(笔) | {m1['hist_streak']} | {m1['streak_median']:.0f} | {m1['streak_p95']:.0f} | {m1['streak_p99']:.0f} |

**实盘刹车线建议**(以 0.5% 风险为例;用 R 表示的版本对任何风险比例都适用):

- **预警线 ≈ {pct(m1['dd_median'])}(约 {m1['ddR_median']:.0f}R)**:一半的随机顺序都会走到这么深,属于正常波动。到这里把每笔风险减半,不改规则。
- **刹车线 ≈ {pct(m1['dd_p95'])}(约 {m1['ddR_p95']:.0f}R)**:只有 5% 的随机顺序会这么深。实盘回撤超过它,更可能是
  “市场变了 / 策略失效”而不是运气差 → 停止开新仓,复查最近的交易和行情环境,至少等样本外重新验证再恢复。
- **连亏复查线 ≈ {m1['streak_p99']:.0f} 笔**:超过 99% 分位的连续亏损笔数,同样触发复查。
- 注意:蒙特卡洛只打乱了**已有**交易的顺序,假设未来交易的分布和过去一样;
  如果优势本身在衰减(见第 8 节样本外),真实回撤可能比 99% 分位还深,所以刹车线宁紧勿松。

### 分年份(放宽版;交易按开仓年份)

{md_table(yr, {"return": pct, "max_drawdown": pct, "sharpe": num, "btc_return": pct,
               "win_rate": pct, "expectancy_R": lambda x: num(x, 3), "total_R": num},
          rename={"year": "年份", "return": "收益", "max_drawdown": "年内最大回撤", "sharpe": "夏普",
                  "btc_return": "BTC 收益", "trades": "交易数", "win_rate": "胜率",
                  "expectancy_R": "期望(R)", "total_R": "累计R"})}

### 严格版全部交易

{trades_table(ts.sort_values("entry_date"))}

### 放宽版最好的 10 笔

{trades_table(rx.trades.sort_values("R", ascending=False).head(10))}

### 放宽版最差的 10 笔

{trades_table(rx.trades.sort_values("R").head(10))}

全部明细:`results/trades_strict.csv`、`results/trades_relaxed.csv`

## 4. 选币方式 × 止损方式

其余规则完全相同(次日开盘买、0.5% 风险、25% 上限、第 3 天卖 1/3 + 保本止损、均线离场、同样的成本)。
“24h 涨幅榜 / 成交额榜前 10”= 每天收盘在流动性合格的币里取排名前 10,次日开盘买入(已持有的跳过)。
“被止损比例”= 被**初始止损**打掉的交易占比(不含部分止盈后的保本止损);“最多连续止损”按平仓时间顺序统计。
ATR 止损时仓位按止损距离反算,每笔风险仍是 0.5%(除非被 25% 仓位上限截断,见最后一列)。
“原方案·放宽版”这里用放宽版的选币条件,配合三种止损。

{md_table(combo, index=False)}

## 5. 条件消融:去掉某一个条件,结果变好还是变坏?

判断标准:去掉后**每笔期望值(R)下降**说明这个条件在帮忙;上升说明它没用甚至有害;变化 < 0.05R 视为影响小。
同时给出 2020–23 与 2024– 两段的期望值,看结论在两段是否一致。注意:去掉条件通常会多出很多交易,
即使期望值略降,累计 R 也可能更高——这两列要一起看。

**放宽版**(基准 {rs['trades']} 笔):

{md_table(abl["放宽版"], abl_fmt, index=False, rename=abl_cols)}

**严格版**(基准只有 {ss['trades']} 笔,结论仅供参考):

{md_table(abl["严格版"], abl_fmt, index=False, rename=abl_cols)}

## 6. 参数敏感性

需求要求的组合:强势前 1%/2%/5% × 10/20 日均线离场 × 0.5%/1% 风险,两个版本各 12 组(放宽版里“前 N%”替换掉它的 10%)。

{md_table(sens, index=False)}

瓶颈参数(强势前 N% × 横盘区间上限;其他为严格版默认):

{md_table(bottleneck, index=False)}

## 7. 交易 K 线图(人工核对用)

灰底 = 横盘区间,虚线 = 区间最高价 / 横盘均量,breakout 箭头 = 突破日,▲ = 次日开盘买入,红线 = 止损
(点线 = 部分止盈后移到成本价),▼ = 卖出 1/3,✕ = 最终离场;蓝/紫线 = MA10 / MA20。
前 {len(ts)} 张是严格版全部交易,其余是放宽版最好 2 笔、最差 2 笔、随机 2 笔。

{chart_md}

## 8. 样本外检验

做法:在 {len(opt)} 组参数(强势前 2/5/10/20% × 横盘区间 20/30/40% × 缩量 70%/不要求 × 放量 1.0/1.5 倍 ×
止损 3 种 × 均线 10/20)里,只用 **2020–2023** 按夏普挑最优(要求 ≥ 30 笔),再原封不动拿到 **2024-01-01 至今**,初始资金重置为 10,000。

- 样本内最优:{best_desc}
- 全部参数组样本内 vs 样本外夏普的**秩相关 {num(rank_corr)}**(接近 1 = 样本内好的样本外也好;≈ 0 或负 = 调参在拟合噪声)
- 期望值为正的参数组占比:样本内 {pct(is_pos, 0)},样本外 {pct(oos_pos, 0)}

![样本外资金曲线](equity_oos.png)

{md_table(oos_tbl)}

样本内夏普前 10 的参数组及其样本外表现:

{top_is_tbl}

完整网格:`results/optimization_grid.csv`

## 9. 10 USDT 小资金的现实问题(以放宽版为例)

币安现货多数交易对最小下单金额(NOTIONAL 过滤器)约 **5 USDT**,另有数量步长(LOT_SIZE)。

- 10U 账户每笔风险 = 10 × 0.5% = **0.05U**。放宽版止损距离中位数 {pct(t.stop_pct.median())},
  算出的仓位中位数 **{need_10u.median():.2f}U**(25% 上限 = 2.5U),**{pct((need_10u < 5).mean(), 0)} 的交易低于 5U,根本下不了单**。
- 模拟 A(低于 5U 就放弃):{small['skip'][1]['trades']} 笔交易。
- 模拟 B(强行按 5U 下单):{small['bump'][1]['trades']} 笔,总收益 {pct(small['bump'][1]['total_return'])},最大回撤 {pct(small['bump'][1]['max_drawdown'])};
  每笔实际风险中位数为账户的 **{pct(bump_risk.median())}**(规则 0.5%,放大约 {num(bump_risk.median() / 0.005, 0)} 倍),
  而且 5U 仓位拆不出 1/3(1.67U < 5U),部分止盈做不了,剩余部分还可能变成卖不掉的“粉尘”。
- 手续费:一次买卖成本 ≈ 2 × ({p.fee:.2%} + {p.slippage:.2%}) = {rt_cost:.2%} 的仓位,换成 R = 成本 ÷ 止损距离,
  中位数 **{num(cost_R.median(), 3)}R**(每笔期望 {num(rs['expectancy_R'], 3)}R)。比例上大小账户一样,
  但小账户还要面对提币/划转固定费用、BNB 抵扣需要额外持币等问题。
- 要按规则执行(仓位和拆出的 1/3 都 ≥ 5U,覆盖 90% 交易的止损距离 ≤ {pct(stop_q90)}),账户至少需要约 **{acct_need:,.0f}U**。

## 10. 局限

- 日线回测看不到盘中先后顺序(同一天先创新高还是先打止损);滑点用固定比例,极端行情下实际更差。
- 严格版样本只有 {ss['trades']} 笔;放宽版也只有 {rs['trades']} 笔,期望值的置信区间很宽。
- 放宽版的放宽方向是在看过严格版交易数后决定的;虽然只按交易数选,仍属于一次“看过数据”的决定。
- 历史表现不代表未来;本项目仅用于研究,不构成投资建议。

## 11. 总结

{conclusion(**ctx)}
"""
    (OUT / "REPORT.md").write_text(report, encoding="utf-8")
    print(f"报告已写入 {OUT / 'REPORT.md'} ({time.time() - t0:.0f}s)")


def conclusion(ss, rs, bs, combo, abl, opt, rank_corr, is_pos, oos_pos, mc, dd, oos_tbl, yr,
               cost_R, need_10u, bump_risk, acct_need, rx, best_desc) -> str:
    """结论里的数字全部取自本次运行结果,重跑会自动更新。"""
    g = combo.set_index(["选币", "止损"])["期望值(R)"]
    oos = oos_tbl["期望值(R)"]
    top10_R = rx.trades.R.nlargest(10).sum()
    p1 = (f"**一、期望值:放宽版为{'正' if rs['expectancy_R'] > 0 else '负'};严格版只有 {ss['trades']} 笔,无法判断。** "
          f"放宽版 {rs['trades']} 笔交易,每笔 {rs['expectancy_R']:+.3f}R(胜率 {pct(rs['win_rate'])},盈亏比 {num(rs['payoff'])});"
          f"样本外(2024 至今)放宽版期望 {oos.get('样本外 2024–至今 · 放宽版', '—')}R,"
          f"按 2020–23 调出的最优参数在样本外 {oos.get('样本外 2024–至今 · 样本内最优参数', '—')}R,"
          f"参数组样本内外夏普秩相关 {num(rank_corr)};期望为正的参数组从样本内 {pct(is_pos, 0)} 掉到样本外 {pct(oos_pos, 0)}"
          f"——优势存在但在 2024 年后明显衰减,样本内调出来的“最优参数”在样本外几乎没有优势。"
          f"对照组“24h 涨幅榜前 10”期望 {g.get(('24h 涨幅榜前 10', '1×ATR'), '—')}R、"
          f"“24h 成交额榜前 10”{g.get(('24h 成交额榜前 10', '1×ATR'), '—')}R(均为 1×ATR 止损)。"
          f"但 0.5% 风险下资金大部分时间闲置,总收益只有 {pct(rs['total_return'])},远低于 BTC 持有的 {pct(bs['total_return'])}。")
    ab = abl["放宽版"]
    b0 = ab.iloc[0]
    both = [r["name"].replace("去掉 ", "") for _, r in ab.iloc[1:].iterrows()
            if r.e_is < b0.e_is and r.e_oos < b0.e_oos]
    hurt = [r["name"].replace("去掉 ", "") for _, r in ab.iloc[1:].iterrows()
            if r.e_is > b0.e_is and r.e_oos > b0.e_oos]
    p1 += (f" 条件消融(放宽版)里,两段时间去掉后期望值都下降、即**稳定有用**的条件:{'、'.join(both) or '无'};"
           f"两段都是去掉反而更好的:{'、'.join(hurt) or '无'};其余条件在两段里结论不一致,说明作用不稳定。")
    p2 = (f"**二、最大的风险:利润集中在少数几笔大行情,而且机会很少。** 放宽版胜率 {pct(rs['win_rate'])},"
          f"被初始止损打掉 {pct(rs['stop_rate'])},最多连续止损 {rs['max_consec_stops']} 次;"
          f"最好的 10 笔合计 {top10_R:+.1f}R,而全部交易累计只有 {rs['total_R']:+.1f}R——"
          f"错过其中几笔,期望值就会变成负的。蒙特卡洛打乱顺序 1000 次,最大回撤中位数 {pct(mc['dd_median'])}、"
          f"95% 分位 {pct(mc['dd_p95'])}(约 {mc['ddR_p95']:.0f}R),最长连亏 95% 分位 {mc['streak_p95']:.0f} 笔——"
          f"0.5% 风险下回撤幅度本身不大,真正的风险是长时间不赚钱:放宽版从 {dd['dd_peak'].date()} 的高点起"
          f"{'至今 ' + str(dd['dd_total_days']) + ' 天仍未创新高' if dd['dd_recovery'] is None else '用了 ' + str(dd['dd_total_days']) + ' 天才收复'},"
          f"按实际顺序的逐笔回撤 {pct(mc['hist_dd'])} "
          f"{'已经接近或达到' if mc['hist_dd'] <= mc['dd_p95'] * 0.95 else '尚未达到'}蒙特卡洛 95% 分位的刹车线——"
          f"这和样本外优势衰减是同一个信号。跳空低开 / 闪崩会让单笔亏损超过 1R,"
          f"止损很近时手续费 + 滑点也会把一次止损放大到 −2R 以上;下架小币有流动性风险。")
    p3 = (f"**三、10U 小资金基本无法照规则执行。** 每笔风险只有 0.05U,算出的仓位 {pct((need_10u < 5).mean(), 0)} 低于币安 5U 最小下单额;"
          f"强行每笔下 5U,实际风险变成账户的约 {pct(bump_risk.median())}(规则的 {num(bump_risk.median() / 0.005, 0)} 倍),"
          f"且 5U 拆不出 1/3 做部分止盈。手续费 + 滑点每笔约吃掉 {num(cost_R.median(), 2)}R(中位数),"
          f"相对 {rs['expectancy_R']:+.2f}R 的期望值不可忽视。要按规则执行,账户至少约 {acct_need:,.0f}U。")
    return "\n\n".join([p1, p2, p3])


if __name__ == "__main__":
    main()
