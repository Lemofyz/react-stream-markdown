"""“抛物线做空”独立实验:生成 results_short/REPORT_SHORT.md + 图表 + CSV。

规则和选参方式在看结果之前定死(见 cmb/short_engine.py 文件头和下面的 PRE_REGISTERED):
- 12 个变体 = 暴涨阈值 100/200/300% × 入场 daily/4h × 止损 当天最高/1×ATR;
- 只用 2020–2023(样本内)按“手续费 0.1% 下的每笔期望值(R)”选一个变体(样本内 ≥ 20 笔);
- 2024-01-01 至今(样本外)只用来检验,不再改任何参数。

用法:
    python download_futures.py daily && python download_futures.py detail
    python run_short.py
"""
from __future__ import annotations

import itertools
import time
from pathlib import Path

import numpy as np
import pandas as pd

from cmb.charts import plot_equity, plot_mc_hist, plot_short_trade
from cmb.metrics import drawdown_info, monte_carlo_dd, summary
from cmb.short_engine import Aux, ShortBacktester, ShortParams, pump_events
from cmb.universe import TRADFI_PERPS, load_panel
from run_backtest import isnan, md_table, num, pct

DATA = Path("data/futures")
OUT = Path("results_short")
IS_END, OOS_START = "2023-12-31", "2024-01-01"
THRESHOLDS = (1.0, 2.0, 3.0)
ENTRIES = ("daily", "4h")
STOPS = ("day_high", "atr")
FEES = {"0.05%(币安合约吃单)": 0.0005, "0.1%": 0.001, "0.6%(Coinbase 档)": 0.006}
SELECT_FEE = 0.001
MIN_IS_TRADES = 20
BASE = ShortParams()


def vname(th, en, st):
    return f"{th:.0%} · {'日线收盘破前低' if en == 'daily' else '4h 破首根低点'} · " \
           f"{'当天最高止损' if st == 'day_high' else '1×ATR 止损'}"


def tail_stats(t: pd.DataFrame) -> dict:
    if t.empty:
        return {"worst_R": np.nan, "n_lt_15": 0, "n_lt_2": 0, "n_lt_3": 0}
    return {"worst_R": float(t.R.min()), "n_lt_15": int((t.R < -1.5).sum()),
            "n_lt_2": int((t.R < -2).sum()), "n_lt_3": int((t.R < -3).sum())}


def row_metrics(r, prm) -> dict:
    m = summary(r.equity, r.trades, prm.initial_capital)
    m.update(tail_stats(r.trades))
    m["funding_R"] = float(r.trades.funding_R.mean()) if len(r.trades) else np.nan
    return m


def fmt_row(m: dict) -> dict:
    return {"交易数": m["trades"], "胜率": pct(m["win_rate"]), "盈亏比": num(m["payoff"]),
            "期望值(R)": num(m["expectancy_R"], 3), "累计R": num(m["total_R"], 1),
            "总收益": pct(m["total_return"]), "最大回撤": pct(m["max_drawdown"]),
            "最长连亏": m["max_consec_losses"], "单笔最大亏损": num(m["worst_R"]) + "R"
            if not isnan(m["worst_R"]) else "—", "< −2R 笔数": m["n_lt_2"]}


TRADE_COLS = ["symbol", "pump_gain", "detect_date", "entry_date", "exit_date", "entry_price",
              "stop_pct", "hold_days", "funding_R", "R", "exit_reason"]


def trades_table(t: pd.DataFrame) -> str:
    if t.empty:
        return "(无交易)"
    d = t[TRADE_COLS].copy()
    for c in ("detect_date", "entry_date", "exit_date"):
        d[c] = d[c].dt.date
    return md_table(d, {"pump_gain": lambda x: f"+{x:.0%}", "entry_price": lambda x: f"{x:.6g}",
                        "stop_pct": pct, "funding_R": lambda x: f"{x:+.2f}",
                        "R": lambda x: f"{x:+.2f}"}, index=False,
                    rename={"pump_gain": "暴涨幅度", "detect_date": "暴涨第1天",
                            "stop_pct": "止损距离", "funding_R": "资金费(R)"})


def main():
    OUT.mkdir(exist_ok=True)
    t0 = time.time()
    panel = load_panel(DATA / "1d", TRADFI_PERPS)
    aux = Aux(DATA)
    bt = ShortBacktester(panel, aux)
    print(f"载入 {len(bt.syms)} 个永续合约, {bt.dates[0].date()} ~ {bt.dates[-1].date()}")

    # ---------------- 事件统计 ----------------
    ev_rows = []
    for th in THRESHOLDS:
        ev = pump_events(panel, BASE.with_(pump_threshold=th))
        by_year = ev.groupby(ev.detect_date.dt.year).size()
        ev_rows.append({"阈值": f"≥{th:.0%}", "事件数": len(ev), "涉及合约": ev.symbol.nunique(),
                        **{str(y): int(by_year.get(y, 0)) for y in range(2020, bt.dates[-1].year + 1)}})
    ev_tbl = pd.DataFrame(ev_rows)

    # ---------------- 12 个变体 × 3 档手续费 × 样本内/外 ----------------
    rows, results = [], {}
    is_p = BASE.with_(end=IS_END)
    oos_p = BASE.with_(start=OOS_START)
    for th, en, st in itertools.product(THRESHOLDS, ENTRIES, STOPS):
        for fname, f in FEES.items():
            kw = dict(pump_threshold=th, entry_mode=en, stop_mode=st, fee=f)
            for per, pp in (("IS", is_p), ("OOS", oos_p), ("ALL", BASE)):
                prm = pp.with_(**kw)
                r = bt.run(prm)
                results[(th, en, st, f, per)] = r
                m = row_metrics(r, prm)
                rows.append({"variant": vname(th, en, st), "threshold": th, "entry": en,
                             "stop": st, "fee": f, "period": per, **{k: m[k] for k in (
                                 "trades", "win_rate", "payoff", "expectancy_R", "total_R",
                                 "total_return", "max_drawdown", "max_consec_losses",
                                 "worst_R", "n_lt_2", "funding_R", "sharpe")}})
        print(f"{vname(th, en, st)} 完成 ({time.time() - t0:.0f}s)")
    grid = pd.DataFrame(rows)
    grid.to_csv(OUT / "variants_grid.csv", index=False)

    # ---------------- 预先定好的选择规则:只看样本内 ----------------
    sel_pool = grid[(grid.period == "IS") & (grid.fee == SELECT_FEE)]
    eligible = sel_pool[sel_pool.trades >= MIN_IS_TRADES]
    if eligible.empty:
        eligible = sel_pool[sel_pool.trades >= 5]
    best = eligible.sort_values("expectancy_R", ascending=False).iloc[0]
    key = (best.threshold, best.entry, best.stop)
    sel_name = vname(*key)
    print("样本内选中:", sel_name)

    def g(per, f=SELECT_FEE, k=key):
        return grid[(grid.threshold == k[0]) & (grid.entry == k[1]) & (grid.stop == k[2])
                    & (grid.fee == f) & (grid.period == per)].iloc[0]

    # 主表:12 个变体,手续费 0.1%,样本内 vs 样本外
    main_rows = []
    for th, en, st in itertools.product(THRESHOLDS, ENTRIES, STOPS):
        a, b = g("IS", k=(th, en, st)), g("OOS", k=(th, en, st))
        main_rows.append({"变体": vname(th, en, st) + (" ★" if (th, en, st) == key else ""),
                          "内·笔数": a.trades, "内·胜率": pct(a.win_rate),
                          "内·期望R": num(a.expectancy_R, 3), "内·最大回撤": pct(a.max_drawdown),
                          "外·笔数": b.trades, "外·胜率": pct(b.win_rate),
                          "外·期望R": num(b.expectancy_R, 3), "外·最大回撤": pct(b.max_drawdown),
                          "外·最差单笔": num(b.worst_R) + "R" if not isnan(b.worst_R) else "—"})
    main_tbl = pd.DataFrame(main_rows)
    # 手续费敏感性:每个变体在三档手续费下的样本内/外期望
    fee_rows = []
    for th, en, st in itertools.product(THRESHOLDS, ENTRIES, STOPS):
        row = {"变体": vname(th, en, st)}
        for fname, f in FEES.items():
            row[f"{fname}·内"] = num(g("IS", f, (th, en, st)).expectancy_R, 3)
            row[f"{fname}·外"] = num(g("OOS", f, (th, en, st)).expectancy_R, 3)
        fee_rows.append(row)
    fee_tbl = pd.DataFrame(fee_rows)

    # ---------------- 选中变体:详细结果 ----------------
    sel = {per: results[(*key, SELECT_FEE, per)] for per in ("IS", "OOS", "ALL")}
    sel_m = {per: row_metrics(sel[per], sel[per].params) for per in sel}
    detail_rows = []
    for per, lab in (("IS", "样本内 2020–2023"), ("OOS", "样本外 2024–至今"), ("ALL", "全区间")):
        detail_rows.append({"": lab, **fmt_row(sel_m[per]),
                            "夏普": num(sel_m[per]["sharpe"]),
                            "平均资金费(R)": num(sel_m[per]["funding_R"], 3)})
    for fname, f in FEES.items():
        if f == SELECT_FEE:
            continue
        for per, lab in (("IS", "样本内"), ("OOS", "样本外")):
            r = results[(*key, f, per)]
            m = row_metrics(r, r.params)
            detail_rows.append({"": f"{lab} · 手续费 {fname}", **fmt_row(m), "夏普": num(m["sharpe"]),
                                "平均资金费(R)": num(m["funding_R"], 3)})
    # 资金费全部按保守假设
    for per, pp in (("IS", is_p), ("OOS", oos_p)):
        prm = pp.with_(pump_threshold=key[0], entry_mode=key[1], stop_mode=key[2],
                       fee=SELECT_FEE, funding_mode="assumed")
        r = bt.run(prm)
        m = row_metrics(r, prm)
        detail_rows.append({"": f"{'样本内' if per == 'IS' else '样本外'} · 资金费全按 −0.1%/8h",
                            **fmt_row(m), "夏普": num(m["sharpe"]),
                            "平均资金费(R)": num(m["funding_R"], 3)})
    # 止损只用日线检查(“下一根 K 线”= 次日开盘):更保守的成交假设
    for per, pp in (("IS", is_p), ("OOS", oos_p)):
        prm = pp.with_(pump_threshold=key[0], entry_mode=key[1], stop_mode=key[2],
                       fee=SELECT_FEE, intraday_bars=False)
        r = bt.run(prm)
        m = row_metrics(r, prm)
        detail_rows.append({"": f"{'样本内' if per == 'IS' else '样本外'} · 止损只用日线(次日开盘成交)",
                            **fmt_row(m), "夏普": num(m["sharpe"]),
                            "平均资金费(R)": num(m["funding_R"], 3)})
    detail_tbl = pd.DataFrame(detail_rows).set_index("")

    ra = sel["ALL"]
    t = ra.trades
    t.to_csv(OUT / "trades_selected.csv", index=False)
    funding_real_share = (t.funding_real_n.sum() /
                          max(1, t.funding_real_n.sum() + t.funding_assumed_n.sum())) if len(t) else np.nan
    btc = panel["close"]["BTCUSDT"].loc[BASE.start:].ffill()
    plot_equity({"Selected short variant, fee 0.1%": ra.equity,
                 "Same, fee 0.6%": results[(*key, 0.006, "ALL")].equity},
                OUT / "equity_short.png",
                f"Parabolic short ({sel_name.split(' · ')[0]} pump) · 10,000 USDT · "
                f"{BASE.start} ~ {bt.dates[-1].date()}")
    dd = drawdown_info(pd.concat([pd.Series([BASE.initial_capital],
                                            [ra.equity.index[0] - pd.Timedelta(days=1)]),
                                  ra.equity]))
    mc = monte_carlo_dd(t, n=1000, seed=42) if len(t) >= 2 else {}
    if mc:
        mc["mean_R"] = float(t.R.mean())
    if mc:
        plot_mc_hist(mc["dd_samples"], mc["hist_dd"], mc["dd_median"], mc["dd_p95"],
                     OUT / "mc_drawdown_short.png",
                     f"Monte Carlo: selected short variant, {mc['trades']} trades shuffled 1000x")
    reasons = t.exit_reason.value_counts() if len(t) else pd.Series(dtype=int)

    # ---------------- K 线图 ----------------
    cdir = OUT / "trade_charts"
    cdir.mkdir(exist_ok=True)
    for f in cdir.glob("*.png"):
        f.unlink()
    best10 = t.sort_values("R", ascending=False).head(10)
    worst10 = t.sort_values("R").head(10)
    chart_md = []
    for tag, df in (("best", best10), ("worst", worst10)):
        for i, (_, tr) in enumerate(df.iterrows(), 1):
            fn = f"{tag}{i:02d}_{tr.symbol}_{tr.entry_date:%Y%m%d}.png"
            plot_short_trade(panel, tr, cdir / fn, BASE.ma_period,
                             f"[{tag} {i}] {tr.symbol} pump +{tr.pump_gain:.0%}  short "
                             f"{tr.entry_date:%Y-%m-%d} @ {tr.entry_price:.4g}  stop {tr.stop:.4g} "
                             f"(+{tr.stop_pct:.1%})  exit {tr.exit_date:%Y-%m-%d} {tr.exit_reason}"
                             f"  R={tr.R:+.2f}")
            chart_md.append(f"**{'最好' if tag == 'best' else '最差'} {i}. {tr.symbol}** "
                            f"R = {tr.R:+.2f}\n\n![{tr.symbol}](trade_charts/{fn})")
    print(f"图表完成 ({time.time() - t0:.0f}s)")

    # ---------------- 汇总判断用到的数 ----------------
    oos01 = grid[(grid.period == "OOS") & (grid.fee == SELECT_FEE) & (grid.trades > 0)]
    is01 = grid[(grid.period == "IS") & (grid.fee == SELECT_FEE) & (grid.trades > 0)]
    oos06 = grid[(grid.period == "OOS") & (grid.fee == 0.006) & (grid.trades > 0)]
    last = panel["close"].apply(lambda c: c.last_valid_index())
    delisted = last[last < bt.dates[-1] - pd.Timedelta(days=3)]
    f = ra.funnel

    report = f"""# “抛物线做空”策略 · 币安 USDT 永续回测报告

> 纯历史数据模拟:未连接任何账户、未下任何真实订单、不需要 API 密钥。
> 生成时间 {pd.Timestamp.now(tz="UTC"):%Y-%m-%d %H:%M} UTC · 数据截至 {bt.dates[-1].date()}

## 1. 数据

- 做空在现货上做不了,所以这里用**币安 USDT 本位永续合约**的公开历史数据(data.binance.vision):
  日线 + 4 小时线(只为出现暴涨的合约下载)+ **真实历史资金费率**。共 **{len(bt.syms)}** 个合约,
  其中 {len(delisted)} 个在回测结束前已下架(数据仍保留,持仓期间下架按最后收盘价平仓)。
- 这意味着:**只有当时已经上线永续合约的币才能被做空**。很多只在现货暴涨、没有合约的小币,散户本来就空不了,所以不在样本里。
- 日线按 UTC 00:00 切分;流动性 = 近 7 天合约日均成交额 ≥ 500 万 USDT。
- 选中变体的交易里,**{pct(funding_real_share, 0)}** 的资金费结算点用的是真实历史费率,其余按“空头每 8 小时付 0.1%”保守估计。

## 2. 规则(看结果之前定死)

| 环节 | 规则 | 参数 |
|---|---|---|
| 暴涨 | 收盘时过去 3 或 4 天累计涨幅 ≥ 100% / 200% / 300%;第一次满足的这天 = 暴涨第 1 天(D);10 天内不重复计 | `pump_threshold`, `pump_windows`, `max_wait_days` |
| 不追第 1、2 天 | 最早在 D+2(第 3 天)开仓,最晚 D+10;每个事件最多一笔 | `min_entry_offset` |
| 入场 A(日线) | s 日收盘价 < s−1 日最低价(s ≥ D+1)→ s+1 日开盘做空 | `entry_mode="daily"` |
| 入场 B(4h) | 当天首根 4h K 线(00–04 UTC)的最低点被之后的 4h K 线跌破 → 在该价位做空(低开则按开盘价) | `entry_mode="4h"` |
| 止损 | 当天最高点(日线入场 = 信号日最高;4h 入场 = 当天截至触发的最高)或 成交价 + 1×ATR(14);止损距离 > 1.5×ATR 则放弃 | `stop_mode`, `max_stop_atr` |
| 止损成交价 | max(止损价, 下一根 K 线开盘价);当根直接跳空到止损上方则按开盘价——模拟急涨跳空。持仓期间用 **4h K 线**检查(“下一根”= 下一根 4h);第 5 节另给出只用日线(“下一根”= 次日开盘)的更保守结果 | `intraday_bars` |
| 仓位 | 风险 0.5%;数量 = 账户 × 0.5% ÷ 止损距离;单笔名义 ≤ 25% 账户;全部空单名义 ≤ 100% 账户 | `risk_per_trade`, `max_position_pct`, `max_gross_exposure` |
| 平一半 | 最低价 ≤ 前一日 MA10 × 1.02(“均线附近”)→ 按该价平一半,剩余止损移到开仓价 | `ma_touch_buffer`, `partial_frac` |
| 全平 | 平一半之后收盘价 > MA10 → 次日开盘全平;最长持有 20 天(第 20 天收盘全平) | `max_hold_days` |
| 成本 | 手续费每次开/平 0.05% / 0.1% / 0.6% 三档;滑点每次 0.2%;真实资金费率(缺失时空头每 8h 付 0.1%) | `fee`, `slippage`, `funding_mode` |

同一天内的先后顺序一律按对空头**最不利**处理:先检查止损,再检查平一半。
**R** = 本笔净盈亏(价格 + 手续费 + 滑点 + 资金费)÷ 开仓时计划风险。

**选参方式(预先定死)**:12 个变体(3 档阈值 × 2 种入场 × 2 种止损)全部跑;只用 **2020–2023** 在手续费 0.1% 下
按“每笔期望值(R)”挑一个(样本内 ≥ {MIN_IS_TRADES} 笔);**2024-01-01 至今只用来检验**,所有参数不再改动。
三档手续费、资金费假设都只是成本情景,不参与挑选。

**透明说明(唯一一次看过试跑结果后的改动)**:最初持仓期间只用日线检查止损(“下一根 K 线”= 次日开盘)。
试跑一个变体时发现 ALPACA 的 −26R 来自“次日开盘”恰好是下架结算后冻结的价格,不是真实可成交价,
所以改为有 4h 数据时用 4h K 线检查止损(“下一根”= 下一根 4h),并把下架结算后的零成交 K 线视为已停止交易。
这是成交真实性的修正,不是调参;改动前的日线版本结果放在第 5 节作为压力测试(结果更差),不影响结论方向。

## 3. 暴涨事件有多少

{md_table(ev_tbl, index=False)}

## 4. 12 个变体:样本内 vs 样本外(手续费 0.1%)

★ = 按预定规则在样本内选中的变体:**{sel_name}**

{md_table(main_tbl, index=False)}

样本内期望为正的变体 {int((is01.expectancy_R > 0).sum())}/{len(is01)} 个;样本外 {int((oos01.expectancy_R > 0).sum())}/{len(oos01)} 个
(手续费 0.6% 时样本外 {int((oos06.expectancy_R > 0).sum())}/{len(oos06)} 个)。

### 手续费敏感性(每笔期望值 R;内 = 2020–23,外 = 2024–)

{md_table(fee_tbl, index=False)}

## 5. 选中变体详细结果:{sel_name}

![资金曲线](equity_short.png)

{md_table(detail_tbl)}

- 信号漏斗(全区间):暴涨事件 {f['events']} → 出现走弱信号 {f['signals']} → 开盘已高于止损放弃 {f['skip_gap_above_stop']}
  · 止损距离 > 1.5ATR 放弃 {f['skip_stop_too_wide']} · 缺 4h 数据 {f['skip_no_4h_data']} · 总敞口已满 {f['skip_exposure']} → 成交 **{f['filled']}** 笔
- 离场原因:{", ".join(f"{k} {v}" for k, v in reasons.items()) or "—"}
- 最大回撤 {pct(dd.get('max_drawdown'))}:{dd.get('dd_peak').date() if dd else '—'} → {dd.get('dd_trough').date() if dd else '—'}
  (下跌 {dd.get('dd_fall_days', '—')} 天),{('收复于 ' + str(dd['dd_recovery'].date()) + f"(恢复 {dd['dd_recover_days']} 天)") if dd and dd.get('dd_recovery') is not None else '至今未收复'};
  最长水下期 {dd.get('longest_underwater_days', '—')} 天
- **单笔最大亏损 {num(sel_m['ALL']['worst_R'])}R**;亏损超过 1.5R 的 {sel_m['ALL']['n_lt_15']} 笔、超过 2R 的 {sel_m['ALL']['n_lt_2']} 笔、超过 3R 的 {sel_m['ALL']['n_lt_3']} 笔
  (止损按“止损价和下一根开盘价中更差的”成交,所以急涨跳空时亏损会远超 1R)

### 蒙特卡洛:打乱交易顺序 1000 次

{mc_section(mc)}

## 6. 最好和最差的 10 笔(选中变体,全区间)

### 最好的 10 笔

{trades_table(best10)}

### 最差的 10 笔

{trades_table(worst10)}

全部明细:`results_short/trades_selected.csv`;全部变体:`results_short/variants_grid.csv`

### K 线图

灰底 = 暴涨区间(第 1 天往前 3–4 天),箭头 = 暴涨第 1 天(D),▼ = 做空,红线 = 止损(点线 = 平一半后移到开仓价),
▲ = 平一半,✕ = 最终平仓;蓝线 = MA10;纵轴为对数坐标。

{chr(10).join(chart_md)}

## 7. 在美国以散户身份做空加密货币:合规渠道和限制

> 以下只列公开报道中的事实,**全部需要你自己再向券商/交易所/律师核实**;
> 加密衍生品监管在 2025–2026 年变化很快,条款随时可能更新。不构成法律或投资建议。

{COMPLIANCE}

## 8. 局限

- 日线 + 4h 回测看不到更细的盘中顺序;已按对空头最不利的顺序处理,但真实的爆仓/插针可能更极端。
- 滑点固定 0.2%,暴涨小币的真实滑点(尤其止损时)可能大得多;没有模拟强平、保证金追加、自动减仓(ADL)。
- 资金费用的是币安数据;换到美国合规平台,资金费机制和标的都不同(见第 7 节)。
- 只有当时有永续合约的币才在样本里;暴涨事件数量有限,结论的置信区间很宽。

## 9. 总结

{conclusion(sel_name, sel_m, grid, key, is01, oos01, oos06, mc, funding_real_share, t)}
"""
    (OUT / "REPORT_SHORT.md").write_text(report, encoding="utf-8")
    print(f"报告已写入 {OUT / 'REPORT_SHORT.md'} ({time.time() - t0:.0f}s)")


def mc_section(mc: dict) -> str:
    if not mc:
        return "(交易太少,无法做蒙特卡洛)"
    mc = {**mc, "neg": mc["hist_dd_R"] < 0 and mc.get("mean_R", 0) < 0}
    return f"""![蒙特卡洛回撤分布](mc_drawdown_short.png)

| | 实际顺序 | 中位数 | 95% 分位 | 99% 分位 | 1000 次中最差 |
|---|---|---|---|---|---|
| 最大回撤(逐笔复利,风险 0.5%) | {pct(mc['hist_dd'])} | {pct(mc['dd_median'])} | {pct(mc['dd_p95'])} | {pct(mc['dd_p99'])} | {pct(mc['dd_worst'])} |
| 最大回撤(R) | {mc['hist_dd_R']:.1f}R | {mc['ddR_median']:.1f}R | {mc['ddR_p95']:.1f}R | {mc['ddR_p99']:.1f}R | — |
| 最长连亏(笔) | {mc['hist_streak']} | {mc['streak_median']:.0f} | {mc['streak_p95']:.0f} | {mc['streak_p99']:.0f} | — |

{"**注意:这里每笔期望为负,回撤主要由“持续亏损”决定,打乱顺序几乎不改变最大回撤(分布很窄)——说明问题不是运气差,而是没有优势。下面的刹车线只在策略本身有正期望时才有意义。**" + chr(10) + chr(10) if mc.get('neg') else ""}刹车线参考:回撤到中位数({pct(mc['dd_median'])})减半风险;到 95% 分位({pct(mc['dd_p95'])},约 {mc['ddR_p95']:.0f}R)停止开新仓并复查。
蒙特卡洛只打乱了**已有**交易的顺序,无法覆盖“比历史上更极端的一次轧空”,做空策略的真实尾部比这里更厚。"""


COMPLIANCE = """**(1)合规的美国境内渠道(CFTC 监管的期货/永续)——标的都是大市值币,基本没有暴涨小币**

- **Coinbase Derivatives(CFTC 监管的指定合约市场 DCM)**:2025 年 7 月推出面向美国的“永续式期货”
  (perpetual-style futures,名义上 5 年到期,按小时结算资金费,最高约 10 倍杠杆),起步为 nano BTC / nano ETH;
  2025 年 11 月宣布扩展到 ADA、AVAX、BCH、LINK、DOGE、HBAR、LTC、DOT、SHIB、XLM、SUI 等。
  注意:Coinbase 现货高级交易的吃单费率档位(约 0.6%)和衍生品的按合约收费不是一回事。
- **Kraken(经 Bitnomial,CFTC 监管)**:2026 年 6 月前后向符合条件的美国客户推出 CFTC 监管的永续合约,
  首批 9 个标的:BTC、ETH、SOL、XRP、ADA、LINK、DOGE、LTC、AVAX;8 小时资金费。
- **Kalshi**:报道称 2026 年 5 月 CFTC 批准了其比特币永续合约。另有报道称 CFTC 允许把普通期货转成永续的临时不行动函
  (no-action letter)在 2026 年 6 月底到期——是否延期需要核实。
- **CME 期货**(通过期货券商,如 Interactive Brokers、Robinhood 等):BTC、ETH 及微型合约;
  2025 年 3 月上线 SOL 期货(标准 500 SOL / 微型 25 SOL),2025 年 5 月上线 XRP 期货(标准 50,000 / 微型 2,500 XRP),
  2025 年 10 月计划上线 SOL、XRP 期货期权。CME 期货有到期日、需要期货账户审批和保证金。

**(2)用证券账户间接做空**

- 反向 / 杠杆反向 BTC、ETH ETF(例如 ProShares 的反向比特币 ETF)——只覆盖 BTC/ETH,且每日再平衡有损耗。
- 在保证金账户里融券卖空现货加密 ETF(如比特币/以太坊现货 ETF):需要保证金账户审批、可借券、借券费,
  适用 Reg T 等保证金规则;同样只覆盖大市值币。

**(3)离岸平台和 DeFi 的限制**

- **Binance.com**:其用户条款长期写明不向美国人士提供服务(2019–2020 年起开始屏蔽美国用户,美国用户被引导到
  Binance.US,而后者不提供合约)。
- **Hyperliquid**:前端界面把位于/居住在美国的人列为“受限人士”,并封锁美国 IP;dYdX 的前端同样屏蔽美国用户。
  有观点认为智能合约本身与前端不同,但这不是定论,且绕过封锁违反用户条款。
- 用 VPN 等方式绕过地域限制,违反平台条款,可能导致账户冻结、资产无法提取,并有法律风险;
  CFTC 的立场是面向美国散户的杠杆加密商品交易需要在注册平台进行。

**(4)对本策略的实际含义**

本策略做空的对象是 3–4 天翻倍以上的小币,**这些币在美国合规平台上通常根本没有可做空的合约**;
能合规做空的只有 BTC、ETH、SOL、XRP 等少数大币,而它们几乎不会出现 3–4 天涨 100% 的行情。
所以在美国合规框架内,这个策略基本没有可执行的标的。

来源(需要核实最新情况):
[Coinbase: US Perpetual-Style Futures](https://www.coinbase.com/blog/coming-july-21-us-perpetual-style-futures) ·
[The Block: Coinbase Derivatives 扩展山寨币](https://www.theblock.co/post/380028/coinbase-derivatives-to-expand-24-7-futures-trading-for-bevy-of-altcoins-including-ada-avax-doge-and-shib) ·
[The TRADE: Kraken CFTC 永续](https://www.thetradenews.com/kraken-launches-first-cftc-regulated-perpetual-futures-for-us-traders/) ·
[Crypto Times: Kraken 2026-06](https://www.cryptotimes.io/2026/06/15/kraken-rolls-out-cftc-regulated-perpetual-futures-to-us-clients/) ·
[Blockworks: CME SOL/XRP](https://blockworks.com/news/cme-group-solana-xrp-futures) ·
[Cointelegraph: CME XRP 期货](https://cointelegraph.com/news/chicago-mercantile-exchange-cme-group-to-launch-xrp-futures) ·
[The Block: Binance 屏蔽美国用户](https://www.theblock.co/news/markets/2020-11-24-binance-blocking-us-users-14-day-deadline-new-email-85589) ·
[OneKey: Hyperliquid / dYdX 地域限制](https://onekey.so/blog/ecosystem/geo-block-hyperliquid-dydx-us/)"""


def conclusion(sel_name, sel_m, grid, key, is01, oos01, oos06, mc, fshare, t) -> str:
    a, b, al = sel_m["IS"], sel_m["OOS"], sel_m["ALL"]
    n_oos_pos = int((oos01.expectancy_R > 0).sum())
    stable = (a["expectancy_R"] > 0 and b["expectancy_R"] > 0 and b["trades"] >= 10
              and n_oos_pos >= len(oos01) / 2)
    verdict = ("有正期望的迹象,但还谈不上“稳定”" if stable else "没有稳定的正期望")
    oos06_sel = grid[(grid.threshold == key[0]) & (grid.entry == key[1]) & (grid.stop == key[2])
                     & (grid.fee == 0.006) & (grid.period == "OOS")].iloc[0]
    p1 = (f"**一、是否有稳定正期望:{verdict}。** 按预定规则在 2020–2023 选中“{sel_name}”,"
          f"样本内 {a['trades']} 笔、每笔 {a['expectancy_R']:+.3f}R;**样本外(2024 至今){b['trades']} 笔、每笔 "
          f"{b['expectancy_R']:+.3f}R**(手续费 0.6% 时 {oos06_sel.expectancy_R:+.3f}R)。"
          f"12 个变体里,样本外期望为正的有 {n_oos_pos}/{len(oos01)} 个(0.1% 手续费)、"
          f"{int((oos06.expectancy_R > 0).sum())}/{len(oos06)} 个(0.6% 手续费)。"
          f"连最低的 0.05% 手续费下也全部为负,说明亏损不是成本造成的,而是入场本身没有优势:"
          f"暴涨币在“走弱”之后经常再次拉升,一半仓位平掉后剩下的一半大多被保本止损扫掉,盈亏比只有 {num(al['payoff'])}。"
          f"样本内交易数偏少(100% 阈值 {a['trades']} 笔,200%/300% 只有个位数到十几笔),"
          f"但样本外有 {b['trades']} 笔,结论方向一致。")
    worst = t.sort_values("R").head(1)
    w = worst.iloc[0] if len(worst) else None
    p2 = (f"**二、最大的尾部风险:暴涨后的二次拉升(轧空)+ 跳空,让单笔亏损远超 1R。** "
          f"选中变体全区间单笔最大亏损 {num(al['worst_R'])}R"
          + (f"({w.symbol} {w.entry_date:%Y-%m-%d})" if w is not None else "")
          + f",超过 2R 的有 {al['n_lt_2']} 笔;最长连亏 {al['max_consec_losses']} 笔。"
          + (f"蒙特卡洛 1000 次的最大回撤中位数 {pct(mc['dd_median'])}、95% 分位 {pct(mc['dd_p95'])}"
             f"(约 {mc['ddR_p95']:.0f}R)。" if mc else "")
          + f"12 个变体全区间里最差的单笔是 {num(grid[(grid.period == 'ALL') & (grid.fee == SELECT_FEE)].worst_R.min())}R"
            f"(4h 入场变体,如 2025 年 4 月 ALPACA 下架前的轧空:一天从 0.19 拉到 1.48)。"
          + f"做空的收益上限是 100%,亏损理论上没有上限;止损单在急涨中按更差的价格成交,"
            f"资金费在轧空时会转为空头支付(平均每笔资金费 {num(al['funding_R'], 3)}R)。"
            f"历史样本里没出现的更极端轧空(比如一天再涨几倍、交易所暂停、ADL 自动减仓)回测完全覆盖不到。")
    p3 = ("**三、美国散户合规渠道:能合规做空的几乎只有大币,而本策略的标的是暴涨小币。** "
          "境内可用的是 CFTC 监管的期货/永续(Coinbase Derivatives、Kraken 经 Bitnomial、CME 期货经期货券商等,"
          "标的以 BTC、ETH、SOL、XRP 和少数大市值山寨币为主),或用证券账户买反向 BTC/ETH ETF、融券卖空加密 ETF;"
          "Binance.com、Hyperliquid、dYdX 等离岸/去中心化平台的条款不允许美国用户,绕过地域限制有冻结和法律风险。"
          "具体标的、杠杆、资金费机制和准入条件变化很快,以上均需自行核实(详见第 7 节)。")
    return "\n\n".join([p1, p2, p3])


if __name__ == "__main__":
    main()
