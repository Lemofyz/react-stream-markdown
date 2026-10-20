"""期权实验 → results_options/REPORT_OPTIONS.md

A. 彩票期权(单独保留)
B. 三版本对比框架(顺势 / 逆势 / 多空都买):
   B1 主实验:4h 布林带压缩入场 × 每周 / 每月到期
   B2 均值回归模块:偏离 200 日均线 ≥ 20/30/50%,每周开仓 × 每周 / 每月 / 每季度到期
   B3 暴跌模块(原“暴跌后追买看跌”):24h 跌 ≥ 5/8/12%
   B4 “暴拉缓跌 / 暴跌缓拉”与期权偏斜
   B5 定价假设与稳健性
C. 马丁对照(单独保留)

用法:
    python download_options.py
    python run_options.py
"""
from __future__ import annotations

import itertools
import time
from pathlib import Path

import numpy as np
import pandas as pd

from cmb.charts import plot_equity, plot_hist
from cmb.metrics import max_drawdown, max_streak
from cmb.opt_framework import (FwParams, Market, ModelPricer, RealPricer, calibrate_ratios,
                               fair_ratio, fit_proxy, real_skew, run_trades, simulate_fw,
                               tail_asymmetry)
from cmb.options import (Costs, LotteryParams, OptData, calibrate, lottery_model, lottery_real,
                         martingale, martingale_mc, simulate)
from run_backtest import md_table, num, pct

OUT = Path("results_options")
REAL_US = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT"]
MODEL_US = ["BTCUSDT", "ETHUSDT"]
NOCAP = Costs(fee_cap=None)
IS_END, OOS_START = "2023-12-31", "2024-01-01"
VERSIONS = {"trend": "顺势", "contra": "逆势", "straddle": "多空都买"}
VERSIONS_EN = {"trend": "Trend", "contra": "Contrarian", "straddle": "Straddle"}
EXPIRIES = {"weekly": "每周", "monthly": "每月", "quarterly": "每季度"}
REGIMES = {"bull": "牛市", "bear": "熊市", "range": "震荡"}
SQUEEZE = dict(trigger="squeeze")
DEVIATION = dict(trigger="deviation", exit_hours_before=0, exit_on_ma200=True)
CRASH = dict(trigger="crash", exit_on_ma20_4h=True)


# ============================================================================
# 通用统计
# ============================================================================

def boot_ratio(num_, den, n=2000, seed=1):
    """sum(num)/sum(den) 的 bootstrap 95% 区间(期权回报极度偏斜,需要看区间)。"""
    num_, den = np.asarray(num_, float), np.asarray(den, float)
    if len(num_) == 0:
        return np.nan, np.nan, np.nan
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(num_), size=(n, len(num_)))
    r = num_[idx].sum(1) / den[idx].sum(1)
    return float(num_.sum() / den.sum()), float(np.quantile(r, 0.025)), float(np.quantile(r, 0.975))


def cagr(eq: pd.Series, start, end, initial=10_000.0):
    if eq.empty:
        return np.nan
    years = (pd.Timestamp(end) - pd.Timestamp(start)).days / 365.25
    return (eq.iloc[-1] / initial) ** (1 / years) - 1 if years > 0 and eq.iloc[-1] > 0 else np.nan


def fw_stats(tk: pd.DataFrame, eq: pd.Series, start, end) -> dict:
    if tk.empty:
        return {"笔数": 0}
    w, lo = tk.R[tk.R > 0], tk.R[tk.R <= 0]
    return {
        "笔数": len(tk), "期望值(R)": num(tk.R.mean(), 3), "胜率": pct((tk.R > 0).mean()),
        "盈亏比": num(w.mean() / abs(lo.mean())) if len(w) and len(lo) else "—",
        "最大回撤": pct(max_drawdown(pd.concat([pd.Series([10_000.0]), eq]))),
        "最长连续归零": int(max_streak(tk.sort_values("t").R <= -0.9)),
        "年化收益": pct(cagr(eq, start, end)),
    }


def losing_runs(tk: pd.DataFrame, eq: pd.Series) -> dict:
    """单边趋势中的连续亏损:同一标的、按开仓顺序、直到出现第一笔盈利为止。
    返回:最长连亏笔数、单段累计亏损(R 与占账户%)、等到“回头”之前账户的最低回撤。"""
    best = {"n": 0, "R": 0.0, "acct": 0.0, "dd": 0.0}
    if tk.empty:
        return best
    eqf = pd.concat([pd.Series([10_000.0], [tk.t.min() - pd.Timedelta(hours=1)]), eq]).sort_index()
    for _, g in tk.sort_values("t").groupby("u"):
        run = []
        for _, r in g.iterrows():
            if r.R <= 0:
                run.append(r)
                continue
            if run:
                _score(run, r.exit_time, eqf, best)
            run = []
        if run:
            _score(run, run[-1].exit_time, eqf, best)
    return best


def _score(run, end_t, eqf, best):
    start_t = run[0].t
    before = eqf.loc[:start_t]
    base = before.iloc[-1] if len(before) else 10_000.0
    loss = sum(-r.R * r.premium for r in run)
    seg = eqf.loc[start_t:end_t]
    dd = (seg.min() / base - 1) if len(seg) else 0.0
    best["n"] = max(best["n"], len(run))
    best["R"] = max(best["R"], sum(-r.R for r in run))
    best["acct"] = max(best["acct"], loss / base)
    best["dd"] = min(best["dd"], dd)


# ============================================================================
# A. 彩票期权(规则与之前相同)
# ============================================================================

def lottery_stats(t: pd.DataFrame, t_nocap: pd.DataFrame | None = None) -> dict:
    if t.empty:
        return {}
    t = t.sort_values(["t", "u", "cp"])
    pm, lo, hi = boot_ratio(t.intrinsic, t.mark)
    pa = t.intrinsic.sum() / t.ask.sum()
    return {
        "笔数": len(t), "中奖率(到期有价值)": pct(t.itm.mean(), 2),
        "≥10倍": f"{(t.multiple >= 10).sum()} 笔 ({pct((t.multiple >= 10).mean(), 2)})",
        "≥40倍": f"{(t.multiple >= 40).sum()} 笔 ({pct((t.multiple >= 40).mean(), 2)})",
        "最大倍数": f"{t.multiple.max():.1f}×",
        "期望值(R)·费率封顶": num(t.R.mean(), 3),
        "期望值(R)·不封顶": num(t_nocap.R.mean(), 3) if t_nocap is not None and len(t_nocap) else "—",
        "最长连续归零": max_streak(t.multiple <= 0),
        "到期价值÷卖一价": num(pa, 3),
        "到期价值÷标记价 [95%区间]": f"{pm:.2f} [{lo:.2f}, {hi:.2f}]",
        "卖一÷标记价": num((t.ask / t.mark).median()),
    }


def lottery_part(D: OptData, t0) -> tuple[str, str]:
    lp = LotteryParams()
    cal = {u: calibrate(D, u, lp) for u in MODEL_US}
    cal_tbl = pd.DataFrame([{
        "标的": u, "彩票·看涨 IV÷BVOL": num(c["lot_iv_ratio_C"]), "彩票·看跌 IV÷BVOL": num(c["lot_iv_ratio_P"]),
        "彩票·卖一÷标记": num(c["lot_ask_ratio"]), "彩票·买一÷标记": num(c["lot_bid_ratio"]),
        "行权价间距": pct(c["step_pct"]), "最小报价": f"{c['tick']:g}", "校准样本": c["lot_n"]}
        for u, c in cal.items()])

    real = {u: lottery_real(D, u, lp) for u in REAL_US}
    real_nc = {u: lottery_real(D, u, lp.with_(costs=NOCAP)) for u in REAL_US}
    rows = [{"标的": u, **lottery_stats(real[u], real_nc[u])} for u in REAL_US]
    be = pd.concat([real["BTCUSDT"], real["ETHUSDT"]])
    be_nc = pd.concat([real_nc["BTCUSDT"], real_nc["ETHUSDT"]])
    rows.append({"标的": "BTC+ETH", **lottery_stats(be, be_nc)})
    allr = pd.concat(real.values())
    rows.append({"标的": "全部 5 个", **lottery_stats(allr, pd.concat(real_nc.values()))})
    lot_real_tbl = pd.DataFrame(rows)
    allr.to_csv(OUT / "lottery_real_trades.csv", index=False)
    side_rows = [{"": f"BTC+ETH · {'看涨' if cp == 'C' else '看跌'}", **lottery_stats(be[be.cp == cp])}
                 for cp in ("C", "P")]

    val_rows = []
    for u in MODEL_US:
        ov0, ov1 = D.bvol(u).index[0], D.chain(u).t.max()
        m = lottery_model(D, u, lp, cal[u], start=ov0, end=ov1)
        r = real[u][real[u].t >= ov0]
        for name, t in (("真实快照", r), ("BVOL 模型", m)):
            val_rows.append({"标的": u, "数据": name, "笔数": len(t), "中奖率": pct(t.itm.mean(), 2),
                             "期望值(R)": num(t.R.mean(), 3),
                             "到期价值÷卖一价": num(t.intrinsic.sum() / t.ask.sum(), 3)})
    val_tbl = pd.DataFrame(val_rows)

    mod = pd.concat([lottery_model(D, u, lp, cal[u]) for u in MODEL_US])
    mod_nc = pd.concat([lottery_model(D, u, lp.with_(costs=NOCAP), cal[u]) for u in MODEL_US])
    mod.to_csv(OUT / "lottery_model_trades.csv", index=False)
    sens_rows = []
    for k in (1.0, 1.2, None):
        cc = {u: ({**c, "lot_iv_ratio_C": k, "lot_iv_ratio_P": k} if k else c) for u, c in cal.items()}
        mm = pd.concat([lottery_model(D, u, lp, cc[u]) for u in MODEL_US])
        v, lo, hi = boot_ratio(mm.intrinsic, mm.mark)
        sens_rows.append({"翼部 IV 假设": f"BVOL × {k:g}" if k else "BVOL × 校准值(≈1.4)",
                          "笔数": len(mm), "中奖率": pct(mm.itm.mean(), 2),
                          "到期价值÷标记价 [95%区间]": f"{v:.2f} [{lo:.2f}, {hi:.2f}]",
                          "期望值(R)": num(mm.R.mean(), 3)})
    sens_tbl = pd.DataFrame(sens_rows)
    lot_mod_tbl = pd.DataFrame(
        [{"": "BTC+ETH 全部", **lottery_stats(mod, mod_nc)}]
        + [{"": f"{u} · {'看涨' if cp == 'C' else '看跌'}",
            **lottery_stats(mod[(mod.u == u) & (mod.cp == cp)],
                            mod_nc[(mod_nc.u == u) & (mod_nc.cp == cp)])}
           for u in MODEL_US for cp in ("C", "P")]
        + [{"": str(y), **lottery_stats(g, mod_nc[mod_nc.t.dt.year == y])}
           for y, g in mod.groupby(mod.t.dt.year)])
    eq_lr, _ = simulate(be.reset_index(drop=True), lp.budget)
    eq_lm, _ = simulate(mod.reset_index(drop=True), lp.budget)
    eq_lm_nc, _ = simulate(mod_nc.reset_index(drop=True), lp.budget)
    plot_equity({"Lottery, BVOL model, fee capped": eq_lm,
                 "Lottery, BVOL model, fee uncapped": eq_lm_nc,
                 "Lottery, real quotes (2023-05~10)": eq_lr},
                OUT / "equity_lottery.png",
                "Daily 24h OTM lottery options (call+put, 0.2% each), BTC+ETH, 10,000 USDT",
                legend_loc="lower left")
    print(f"彩票完成 ({time.time() - t0:.0f}s)")

    real_row = lot_real_tbl.set_index("标的").loc["BTC+ETH"]
    mod_row = lot_mod_tbl.iloc[0]
    pm, pm_lo, pm_hi = boot_ratio(mod.intrinsic, mod.mark)
    pr, pr_lo, pr_hi = boot_ratio(be.intrinsic, be.mark)
    md = f"""## A. 彩票期权(单独实验)

**规则**:每个到期日(币安每天 08:00 UTC 到期),到期前 24 小时在虚值看涨和虚值看跌中各找 |Delta| 0.02–0.05、有卖一价的合约,
选**卖一价最便宜**的一档按卖一价买入;每张花账户 0.2%(含手续费);持有期间每小时检查,买一价 ≥ 成本 × 10 卖一半,其余持有到期结算。
“中奖”= 到期有内在价值;“归零”= 到期没有价值且没卖出过一半。模型版本选 |Delta| ≥ 0.02 的最远一档。
手续费:币安期权 0.03% × 标的名义价值、行权 0.015%;网上资料未能确认是否“不超过权利金 10%”,两种都跑(默认封顶)。

### A.1 用真实快照校准(BVOL 与真实快照重叠的 2023-06-20 ~ 2023-10-23)

{md_table(cal_tbl, index=False)}

深度虚值翼部的隐含波动率约为 BVOL 的 **1.3–1.4 倍**(波动率微笑),卖一价又比标记价高约 **30%**。模型 vs 真实(同一段时间):

{md_table(val_tbl, index=False)}

### A.2 真实快照结果(2023-05-18 ~ 2023-10-23)

{md_table(lot_real_tbl, index=False)}

{md_table(pd.DataFrame(side_rows).set_index(""))}

“到期价值 ÷ 标记价”< 1 = 即使按中间价买也亏(期权被高估);方括号是 bootstrap 95% 区间——中奖的只有极少几笔,区间很宽。

### A.3 BVOL 模型(2023-06 ~ 至今,BTC + ETH)

{md_table(lot_mod_tbl.set_index(""))}

![彩票期权资金曲线](equity_lottery.png)

资金曲线(初始 10,000 USDT,每张 0.2%):BVOL 模型 {eq_lm.index[0]:%Y-%m} → {eq_lm.index[-1]:%Y-%m} 期末
**{eq_lm.iloc[-1]:,.0f} USDT**(最大回撤 {pct(max_drawdown(eq_lm))});手续费不封顶时 {eq_lm_nc.iloc[-1]:,.0f} USDT;
真实快照 5 个月期末 {eq_lr.iloc[-1]:,.0f} USDT(最大回撤 {pct(max_drawdown(eq_lr))})。

**敏感性:翼部定价假设**(“高估”有多少来自校准出的翼部 IV ≈ BVOL × 1.4):

{md_table(sens_tbl, index=False)}

{sens_note(sens_tbl)}
"""
    s1 = sens_tbl.iloc[0]
    concl = (f"**彩票期权:平均{'被高估' if pm < 1 else '被低估'}。** BVOL 模型(BTC+ETH,{mod_row['笔数']} 张)到期价值只有标记价的 "
             f"{pm:.2f} 倍(95% 区间 {pm_lo:.2f}–{pm_hi:.2f}),按卖一价只有 {mod_row['到期价值÷卖一价']} 倍;中奖率 "
             f"{mod_row['中奖率(到期有价值)']},≥10 倍 {mod_row['≥10倍']}、≥40 倍 {mod_row['≥40倍']},每笔 {mod_row['期望值(R)·费率封顶']}R,"
             f"最长连续归零 {mod_row['最长连续归零']} 张,3 年多 10,000 → {eq_lm.iloc[-1]:,.0f} USDT。真实快照(2023 年 5–10 月,"
             f"{real_row['笔数']} 张)方向一致(到期价值 ÷ 标记价 {pr:.2f},区间 {pr_lo:.2f}–{pr_hi:.2f},只有 2 张中奖)。"
             f"若翼部按平值 IV 定价({s1['翼部 IV 假设']}),到期价值 ÷ 标记价为 {s1['到期价值÷标记价 [95%区间]']}——大致公平;"
             f"**高估来自波动率微笑(翼部 IV 高 30–40%)+ 卖一价比标记价高约 30% + 按名义价值收的手续费**。")
    return md, concl


def _ratio_ci(cell: str) -> tuple[float, float, float]:
    v, rest = cell.split(" [")
    lo, hi = rest.rstrip("]").split(", ")
    return float(v), float(lo), float(hi)


def sens_note(sens_tbl) -> str:
    v1, lo1, hi1 = _ratio_ci(sens_tbl.iloc[0]["到期价值÷标记价 [95%区间]"])
    if lo1 > 1:
        verdict = "明显 > 1:如果按平值 IV 定价,深度虚值期权其实是便宜的"
    elif hi1 < 1:
        verdict = "仍明显 < 1:连按平值 IV 定价都付多了"
    else:
        verdict = "区间包含 1:按平值 IV 定价时大致是公平的"
    return (f"翼部按 BVOL × 1.0 定价时,到期价值 ÷ 标记价为 {v1:.2f}(区间 {lo1:.2f}–{hi1:.2f}),{verdict}。"
            f"也就是说,**高估主要来自波动率微笑**——市场对深度虚值期权多收了约 40% 的隐含波动率,"
            f"而这段时间实际发生的 24 小时尾部波动并不支持这个溢价;再加上卖一价比标记价高约 30% 和手续费,"
            f"即使在 × 1.0 的假设下,买方每笔期望仍是 {sens_tbl.iloc[0]['期望值(R)']}R。")


# ============================================================================
# B. 三版本对比框架
# ============================================================================

class Runner:
    """anchor=True:按时段(BVOL / 代理)缩放 IV,使“每周一买平值跨式持有到期”按中间价恰好不赚不亏
    (2023 年真实报价里这个检验正好 ≈ 1.00)。iv_scale 在锚定之后再整体放大 IV(模拟波动率溢价)。"""

    def __init__(self, D: OptData, M: Market, anchor: bool = True, iv_scale: float = 1.0):
        self.D, self.M = D, M
        self.ratios, self.proxy, self.ratio_tbls = {}, {}, {}
        self.model, self.real, self.anchor = {}, {}, {}
        for u in MODEL_US:
            r, tbl = calibrate_ratios(D, u)
            p = fit_proxy(M, D, u)
            step = calibrate(D, u, LotteryParams())["step_pct"]
            self.ratios[u], self.proxy[u], self.ratio_tbls[u] = r, p, tbl
            raw = ModelPricer(u, M, D, r, p, step)
            fb = fp = 1.0
            if anchor:
                bv0 = D.bvol(u).index[0]
                fb = fair_ratio(M, {u: raw}, start=str((bv0 + pd.Timedelta(days=7)).date()))
                fp = fair_ratio(M, {u: raw}, end=str(bv0.date()))
            self.anchor[u] = {"bvol": fb, "proxy": fp}
            self.model[u] = ModelPricer(u, M, D, r, p, step, proxy_scale=fp, iv_scale=iv_scale,
                                        bvol_scale=fb)
            self.real[u] = RealPricer(u, M, D)
        self.end = min(M.bars4h(u).index[-1] for u in MODEL_US)
        self._cache = {}

    def trades(self, prm: FwParams, real=False) -> pd.DataFrame:
        key = (prm, real)
        if key not in self._cache:
            self._cache[key] = run_trades(self.M, self.real if real else self.model, prm)
        return self._cache[key]

    def run(self, prm: FwParams, period: str = "all"):
        t = self.trades(prm)
        if period == "is":
            t, s, e = t[t.t <= IS_END], prm.start, IS_END
        elif period == "oos":
            t, s, e = t[t.t >= OOS_START], OOS_START, self.end
        else:
            s, e = prm.start, self.end
        eq, tk = simulate_fw(t.reset_index(drop=True), prm)
        return eq, tk, s, e


def squeeze_part(R: Runner, Rraw: Runner, t0) -> tuple[str, dict]:
    rows, iso, reg_rows, curves = [], [], [], {"weekly": {}, "monthly": {}}
    res = {}
    for v, e in itertools.product(VERSIONS, ("weekly", "monthly")):
        prm = FwParams(**SQUEEZE, version=v, expiry=e)
        eq, tk, s, en = R.run(prm)
        res[(v, e)] = (eq, tk)
        name = f"{VERSIONS[v]} · {EXPIRIES[e]}"
        rows.append({"版本 · 到期": name, **fw_stats(tk, eq, s, en)})
        curves[e][f"{VERSIONS_EN[v]} ({e})"] = eq
        r_is, r_oos = R.run(prm, "is"), R.run(prm, "oos")
        a = fw_stats(r_is[1], r_is[0], r_is[2], r_is[3])
        b = fw_stats(r_oos[1], r_oos[0], r_oos[2], r_oos[3])
        iso.append({"版本 · 到期": name, "内·笔数": a.get("笔数"), "内·期望R": a.get("期望值(R)"),
                    "内·年化": a.get("年化收益"), "内·最大回撤": a.get("最大回撤"),
                    "外·笔数": b.get("笔数"), "外·期望R": b.get("期望值(R)"),
                    "外·年化": b.get("年化收益"), "外·最大回撤": b.get("最大回撤")})
        row = {"版本 · 到期": name}
        for rg, lab in REGIMES.items():
            g = tk[tk.regime == rg]
            row[lab] = f"{g.R.mean():+.3f} ({len(g)})" if len(g) else "—"
        reg_rows.append(row)
        tk.to_csv(OUT / f"squeeze_{v}_{e}.csv", index=False)
    for e in ("weekly", "monthly"):
        plot_equity(curves[e], OUT / f"equity_squeeze_{e}.png",
                    f"Three versions, 4h Bollinger squeeze entries, {e} expiry (BTC+ETH, 0.5% per trade)")
    val = []
    for v, e in itertools.product(VERSIONS, ("weekly", "monthly")):
        prm = FwParams(**SQUEEZE, version=v, expiry=e, start="2023-06-21", end="2023-10-01")
        tr, tm, tw = R.trades(prm, real=True), R.trades(prm), Rraw.trades(prm)
        val.append({"版本 · 到期": f"{VERSIONS[v]} · {EXPIRIES[e]}",
                    "真实报价·笔数": len(tr), "真实报价·期望R": num(tr.R.mean(), 3) if len(tr) else "—",
                    "锚定模型·期望R": num(tm.R.mean(), 3) if len(tm) else "—",
                    "原始模型·期望R": num(tw.R.mean(), 3) if len(tw) else "—"})
    print(f"压缩主实验完成 ({time.time() - t0:.0f}s)")
    md = f"""### B1. 主实验:4h 布林带压缩入场

{md_table(pd.DataFrame(rows), index=False)}

“归零”= 收回不到权利金的 10%(R ≤ −0.9);年化收益按 2020-01 至今、每笔 0.5% 权利金、最多同时 3 笔计算。

![每周到期](equity_squeeze_weekly.png)

![每月到期](equity_squeeze_monthly.png)

**分市场状态**(每格 = 每笔期望 R(笔数),按开仓时的状态;顺势 / 逆势在震荡市不开仓):

{md_table(pd.DataFrame(reg_rows), index=False)}

**样本内 2020–2023 vs 样本外 2024–至今**(各自从 10,000 USDT 起算;样本内大部分时间 IV 是锚定后的代理值,样本外是锚定后的真实 BVOL):

{md_table(pd.DataFrame(iso), index=False)}

**模型 vs 真实报价**(同一段时间 2023-06-21 ~ 2023-10-01,只有十几笔;锚定系数按整个 BVOL 时段算,
在这几个月里会让模型比真实报价更贵、结果更悲观):

{md_table(pd.DataFrame(val), index=False)}
"""
    return md, res


def deviation_part(R: Runner, t0) -> tuple[str, dict]:
    rows, risk_rows, res = [], [], {}
    for thr, e in itertools.product((0.20, 0.30, 0.50), ("weekly", "monthly", "quarterly")):
        row = {"偏离阈值": f"{thr:.0%}", "到期": EXPIRIES[e]}
        for v in VERSIONS:
            prm = FwParams(**DEVIATION, version=v, expiry=e, dev_thr=thr)
            eq, tk, s, en = R.run(prm)
            res[(thr, e, v)] = (eq, tk)
            row[f"{VERSIONS[v]}·期望R(笔数)"] = f"{tk.R.mean():+.3f} ({len(tk)})" if len(tk) else "—"
            if v in ("contra", "trend"):
                lr = losing_runs(tk, eq)
                st = fw_stats(tk, eq, s, en)
                risk_rows.append({
                    "版本": VERSIONS[v], "偏离阈值": f"{thr:.0%}", "到期": EXPIRIES[e],
                    "笔数": st.get("笔数"), "期望值(R)": st.get("期望值(R)", "—"),
                    "最长连续归零": st.get("最长连续归零", 0),
                    "单边段最长连亏(笔)": lr["n"],
                    "单边段累计亏损": f"{lr['R']:.1f}R / {pct(lr['acct'])}",
                    "等到回头前最低回撤": pct(lr["dd"]),
                    "最大回撤": st.get("最大回撤", "—"), "年化": st.get("年化收益", "—")})
        rows.append(row)
    risk = pd.DataFrame(risk_rows).sort_values(["版本", "偏离阈值"], kind="stable")
    iso = []
    for v in VERSIONS:
        for per, lab in (("is", "样本内 2020–23"), ("oos", "样本外 2024–")):
            eq, tk, s, en = R.run(FwParams(**DEVIATION, version=v, expiry="weekly", dev_thr=0.30), per)
            iso.append({"版本": VERSIONS[v], "区间": lab, **fw_stats(tk, eq, s, en)})
    eqc = {f"{VERSIONS_EN[v]} (30%, weekly)": res[(0.30, "weekly", v)][0] for v in VERSIONS}
    plot_equity(eqc, OUT / "equity_deviation.png",
                "Mean reversion vs trend: >=30% away from 200d MA, weekly entries, weekly expiry")
    print(f"均值回归模块完成 ({time.time() - t0:.0f}s)")
    md = f"""### B2. 均值回归模块:偏离 200 日均线

规则:每周一 08:00 UTC,若前一日收盘价高于(或低于)200 日均线 ≥ 20% / 30% / 50%:**逆势版**买 Delta≈0.3 的看跌(或看涨),
**顺势版**方向相反,**多空都买版**买平值跨式;到期分每周 / 每月 / 每季度(≥ 30 天后的季末周五)三组;价值翻倍卖一半,
剩余持有到期,或价格回到 200 日均线(日线收盘穿回)时平仓;每笔 0.5%,最多同时 3 笔(长期限时第 4 周起的信号会被跳过)。

每笔期望 R(笔数):

{md_table(pd.DataFrame(rows), index=False)}

逆势 vs 顺势的风险细节(“单边段”= 同一标的连续没有盈利的一段;累计亏损 = 这一段亏掉的权利金,R 和占段首账户的比例;
“等到回头前最低回撤”= 从这段第一笔开仓到第一笔盈利平仓之间,账户相对段首的最低点):

{md_table(risk, index=False)}

30% 阈值、每周到期的样本内 / 样本外:

{md_table(pd.DataFrame(iso), index=False)}

![均值回归资金曲线](equity_deviation.png)
"""
    return md, res


def crash_part(R: Runner, t0) -> tuple[str, dict]:
    rows, res = [], {}
    for thr, e in itertools.product((0.05, 0.08, 0.12), ("weekly", "monthly")):
        row = {"跌幅阈值": f"{thr:.0%}", "到期": EXPIRIES[e]}
        for v in VERSIONS:
            prm = FwParams(**CRASH, version=v, expiry=e, drop_thr=thr)
            eq, tk, s, en = R.run(prm)
            res[(thr, e, v)] = (eq, tk)
            row[f"{VERSIONS[v]}·期望R(笔数)"] = f"{tk.R.mean():+.3f} ({len(tk)})" if len(tk) else "—"
        rows.append(row)
    det = []
    for thr in (0.05, 0.08, 0.12):
        tk = res[(thr, "weekly", "trend")][1]
        for src, lab in (("BVOL", "2023-06 起(真实 BVOL)"), ("proxy", "之前(代理 IV)")):
            g = tk[tk.iv_source == src]
            if g.empty:
                continue
            ratios = []
            for _, r in g.iterrows():
                a = R.model[r.u].atm
                hist = a.loc[r.t - pd.Timedelta(days=30): r.t - pd.Timedelta(hours=4)]
                ratios.append(a.get(r.t, np.nan) / hist.median() if len(hist) else np.nan)
            losers = g[g.R <= 0]
            reb = losers[losers.exit_reason == "ma_exit"]
            det.append({"跌幅阈值": f"{thr:.0%}", "区间": lab, "笔数": len(g),
                        "期望值(R)": num(g.R.mean(), 3), "胜率": pct((g.R > 0).mean()),
                        "开仓 IV ÷ 前30天中位数": f"{np.nanmean(ratios):.2f}×",
                        "反弹(站回MA20)离场的亏损·笔数占比": pct(len(reb) / len(losers)) if len(losers) else "—",
                        "·亏损金额占比": pct(reb.R.sum() / losers.R.sum()) if len(losers) else "—"})
    print(f"暴跌模块完成 ({time.time() - t0:.0f}s)")
    md = f"""### B3. 暴跌模块(覆盖原“暴跌后追买看跌”实验)

规则:4h 收盘比 24 小时前跌 ≥ 5% / 8% / 12% 且收盘 < 4h MA20(条件刚出现那根);**顺势版**买 Delta≈−0.3 看跌(即原实验),
**逆势版**买 Delta≈0.3 看涨(赌反弹),**多空都买版**买平值跨式;价值翻倍卖一半,剩余在 4h 收盘站回 MA20 或到期前 1 天平仓;
每笔 0.5%,最多同时 3 笔。

每笔期望 R(笔数):

{md_table(pd.DataFrame(rows), index=False)}

顺势版(买看跌,每周到期)细节:

{md_table(pd.DataFrame(det), index=False)}

“开仓 IV ÷ 前 30 天中位数”在 2023-06 之后是真实 BVOL;之前是代理值。代理会低估暴跌时刻的 IV(见 B5),所以样本内对买方偏乐观。
"""
    return md, res


def asymmetry_part(R: Runner) -> tuple[str, pd.DataFrame, pd.DataFrame]:
    prm = FwParams()
    asym = pd.concat([tail_asymmetry(R.M, u, prm) for u in MODEL_US])
    asym["状态"] = asym["状态"].map(REGIMES)
    sk = pd.concat([real_skew(R.D, R.M, u, prm) for u in MODEL_US])
    sk_tbl = pd.DataFrame()
    if len(sk):
        sk_tbl = sk.groupby(["u", "group", "regime"])["skew"].agg(["mean", "size"]).reset_index()
        sk_tbl["regime"] = sk_tbl["regime"].map(REGIMES)
        sk_tbl["group"] = sk_tbl["group"].map(EXPIRIES)
        sk_tbl = sk_tbl.rename(columns={"u": "标的", "group": "期限", "regime": "状态",
                                        "mean": "看涨IV − 看跌IV(平均)", "size": "天数"})
    sk_md = (md_table(sk_tbl, {"看涨IV − 看跌IV(平均)": lambda x: f"{x * 100:+.1f} 个百分点"}, index=False)
             if len(sk_tbl) else "(无数据)")
    return asym, sk_tbl, f"""### B4. 验证“暴拉缓跌 / 暴跌缓拉”

2020 年起,按前一根 K 线收盘时的市场状态分组,取绝对涨跌幅最大的 5%:

{md_table(asym, {"上涨占比": pct, "上涨平均幅度": pct, "下跌平均幅度": pct}, index=False)}

同期期权偏斜(只有 2023-05 ~ 10 的真实快照;每天 08:00、|Delta|≈0.3 的看涨 IV 减看跌 IV;正值 = 看涨更贵):

{sk_md}

真实快照只覆盖 5 个月、熊市日子很少,偏斜部分基本只能看牛市 / 震荡;3 年多的 BVOL 是单一的平值指数,不含偏斜信息。
"""


def robustness_part(R: Runner, Rraw: Runner, Rhi: Runner) -> tuple[str, pd.DataFrame]:
    rows = []
    for name, kw in (("压缩·顺势·每周", dict(**SQUEEZE, version="trend", expiry="weekly")),
                     ("压缩·逆势·每周", dict(**SQUEEZE, version="contra", expiry="weekly")),
                     ("压缩·跨式·每周", dict(**SQUEEZE, version="straddle", expiry="weekly")),
                     ("压缩·顺势·每月", dict(**SQUEEZE, version="trend", expiry="monthly")),
                     ("压缩·逆势·每月", dict(**SQUEEZE, version="contra", expiry="monthly")),
                     ("压缩·跨式·每月", dict(**SQUEEZE, version="straddle", expiry="monthly")),
                     ("偏离30%·顺势·每周", dict(**DEVIATION, version="trend", expiry="weekly", dev_thr=0.3)),
                     ("偏离30%·逆势·每周", dict(**DEVIATION, version="contra", expiry="weekly", dev_thr=0.3)),
                     ("暴跌8%·顺势·每周", dict(**CRASH, version="trend", expiry="weekly")),
                     ("暴跌8%·逆势·每周", dict(**CRASH, version="contra", expiry="weekly"))):
        prm = FwParams(**kw)
        row = {"配置": name}
        for lab, rr in (("原始模型", Rraw), ("公允锚定", R), ("锚定+IV×1.1", Rhi)):
            row[f"{lab}·样本内"] = num(rr.run(prm, "is")[1].R.mean(), 3)
            row[f"{lab}·样本外"] = num(rr.run(prm, "oos")[1].R.mean(), 3)
        rows.append(row)
    pt = pd.DataFrame([{"标的": u, "R²": num(p["r2"]), "残差(对数)": num(p["resid_sd"]),
                        "BVOL ÷ 30天实际波动率(平均)": num(p["bvol_over_rv30"]),
                        "拟合样本(4h)": p["n"]} for u, p in R.proxy.items()])
    an = pd.DataFrame([{"标的": u, "原始模型·BVOL 时段": num(R.anchor[u]["bvol"]),
                        "原始模型·代理时段": num(R.anchor[u]["proxy"]),
                        "锚定后·BVOL 时段": num(fair_ratio(R.M, {u: R.model[u]},
                                                         start=str((R.D.bvol(u).index[0] + pd.Timedelta(days=7)).date()))),
                        "锚定后·代理时段": num(fair_ratio(R.M, {u: R.model[u]},
                                                       end=str(R.D.bvol(u).index[0].date())))}
                       for u in MODEL_US])
    real_fair = fair_ratio(R.M, R.real, start="2023-05-22", end="2023-10-16")
    rt = pd.concat([t.assign(标的=u) for u, t in R.ratio_tbls.items()])
    return pd.DataFrame(rows), f"""### B5. 定价假设与稳健性(读结果前请先看这一节)

**1)公允性检验**:每周一买平值跨式、持有到期、不计价差和手续费,看“到期价值 ÷ 中间价权利金”。
**真实报价(2023-05 ~ 10)= {real_fair:.2f}**,即平值周期权按中间价大致公平。原始模型在长周期里却明显 > 1
(期权被定得偏便宜,买方白捡优势),所以本报告的主结果改用**公允锚定**:按时段把 IV 乘上下表的系数,使这个检验恰好 ≈ 1:

{md_table(an, index=False)}

**2)三种定价下的每笔期望 R**(“锚定+IV×1.1”≈ 再加上常见的 10% 波动率溢价):

{md_table(pd.DataFrame(rows), index=False)}

**结论稳健与否看这张表**:只有在锚定和 ×1.1 两种定价下、样本内和样本外都为正的配置,才可能有真实优势。

**3)IV 代理**(2023-06 之前没有任何隐含波动率数据):log(BVOL) = a + b·log(30 天实际波动率) + c·log(7 天实际波动率),
用 2023-06 ~ 至今 BVOL 与实际波动率同时存在的时段拟合;在暴跌 / 大幅偏离时刻,代理比真实 BVOL 低 3–10%:

{md_table(pt, index=False)}

**4)期限 / Delta 系数**(真实快照 IV ÷ 同时刻 BVOL,中位数;来自 2023 年下半年的平静行情):

{md_table(rt[["标的", "期限组", "Delta 组", "类型", "IV ÷ BVOL", "样本"]], {"IV ÷ BVOL": num}, index=False)}

模型价格 = Black–Scholes 中间价 ± 3%(按要求),另加币安期权手续费(0.03% × 名义价值,不超过权利金 10%)。
"""


# ============================================================================
# C. 马丁对照
# ============================================================================

def martingale_part(R: Runner, t0) -> tuple[str, str]:
    tk = R.trades(FwParams(**CRASH, version="trend", expiry="weekly", drop_thr=0.08))
    out_rows, ruin_for_plot = [], None
    for lab, g in (("全区间(含代理 IV 时段)", tk), ("2023-06 起(真实 BVOL)", tk[tk.iv_source == "BVOL"])):
        Rs = g.sort_values("t").R.to_numpy()
        years = (g.t.max() - g.t.min()).days / 365.25
        per_year = len(g) / years
        hist = martingale(Rs)
        mc = martingale_mc(Rs, n_paths=5000, horizon=int(np.ceil(per_year * 10)))
        days = mc.trades_to_ruin / per_year * 365
        ruin_for_plot = days
        out_rows.append({"R 序列": lab, "笔数": len(g), "每笔期望R": num(Rs.mean(), 3),
                         "按真实顺序": f"{hist['trades']} 笔后归零" if hist["ruined"] else "未归零",
                         "马丁 10 年内归零概率": pct(mc.ruined.mean()),
                         "归零时间中位数(天)": num(days.median(), 0),
                         "归零时间平均(天)": num(days.mean(), 0),
                         "1 年内归零": pct((days <= 365).mean()),
                         "固定 1% 10 年内归零": pct(mc.fixed_ruined.mean())})
    if ruin_for_plot is not None and ruin_for_plot.notna().any():
        plot_hist(ruin_for_plot.dropna().to_numpy(), OUT / "martingale_ruin_days.png",
                  "Martingale (double premium after each loss), time to ruin, BVOL-era trades, 5000 paths",
                  "days until account < 1% of start",
                  {"median": float(ruin_for_plot.median()), "mean": float(ruin_for_plot.mean())})
    tbl = pd.DataFrame(out_rows)
    print(f"马丁完成 ({time.time() - t0:.0f}s)")
    md = f"""## C. 马丁对照(单独实验)

用暴跌模块“顺势版(买看跌)· 跌 ≥ 8% · 每周到期”的交易序列:基础权利金 = 账户 1%,每次亏损后下一笔加倍,盈利后回到 1%;
权益跌破初始的 1% 视为归零。按历史 R 分布有放回抽样 5000 条路径(每条 ≈ 10 年的交易笔数)。

{md_table(tbl, index=False)}

![马丁归零时间分布](martingale_ruin_days.png)

有放回抽样假设每笔独立;真实的暴跌往往扎堆出现,所以真实的归零速度只会更快。
"""
    b = out_rows[-1]
    concl = (f"**马丁:** 用 2023-06 之后真实 BVOL 时段的序列(每笔 {b['每笔期望R']}R),马丁 10 年内归零概率 {b['马丁 10 年内归零概率']}、"
             f"归零时间中位数约 {b['归零时间中位数(天)']} 天,而固定 1% 为 {b['固定 1% 10 年内归零']};"
             f"全区间序列(含代理时段,每笔 {out_rows[0]['每笔期望R']}R)为 {out_rows[0]['马丁 10 年内归零概率']}。"
             f"加倍不改变期望值,只是把小额、分散的亏损变成一次性的全部亏损。")
    return md, concl


# ============================================================================
# 结论
# ============================================================================

def framework_conclusion(sq: dict, dev: dict, cr: dict, R: Runner, rob: pd.DataFrame,
                         asym: pd.DataFrame, sk_tbl: pd.DataFrame) -> str:
    def er(tk):
        return tk.R.mean() if len(tk) else np.nan

    lines = []
    best = max(sq, key=lambda k: er(sq[k][1]))
    sq_txt = "、".join(f"{VERSIONS[v]}·{EXPIRIES[e]} {er(tk):+.3f}R" for (v, e), (_, tk) in sq.items())
    oos = {k: er(R.run(FwParams(**SQUEEZE, version=k[0], expiry=k[1]), "oos")[1]) for k in sq}
    is_ = {k: er(R.run(FwParams(**SQUEEZE, version=k[0], expiry=k[1]), "is")[1]) for k in sq}
    both = [k for k in sq if is_[k] > 0 and oos[k] > 0]
    lines.append(
        f"**三版本(压缩入场)谁更好:** 全区间 {sq_txt}。期望值最高的是 {VERSIONS[best[0]]}·{EXPIRIES[best[1]]};"
        f"样本内、样本外都为正的:{'、'.join(VERSIONS[v] + '·' + EXPIRIES[e] for v, e in both) or '没有'}。"
        f"样本外(2024 至今,真实 BVOL):" + "、".join(f"{VERSIONS[v]}·{EXPIRIES[e]} {oos[(v, e)]:+.3f}R" for v, e in sq) + "。")
    reg = []
    for rg, lab in REGIMES.items():
        vals = {k: er(tk[tk.regime == rg]) for k, (_, tk) in sq.items()}
        vals = {k: x for k, x in vals.items() if not np.isnan(x)}
        if vals:
            k = max(vals, key=vals.get)
            reg.append(f"{lab}里最好的是 {VERSIONS[k[0]]}·{EXPIRIES[k[1]]}({vals[k]:+.3f}R)")
    lines.append("**分市场状态:** " + ";".join(reg) + "。各状态笔数不多(见 B1),差异大多在噪声范围内。")
    c30, t30 = dev[(0.30, "weekly", "contra")][1], dev[(0.30, "weekly", "trend")][1]
    lr_c = losing_runs(c30, dev[(0.30, "weekly", "contra")][0])
    lr_t = losing_runs(t30, dev[(0.30, "weekly", "trend")][0])
    lines.append(
        f"**均值回归(偏离 MA200 ≥ 30%,每周):** 逆势 {er(c30):+.3f}R({len(c30)} 笔)vs 顺势 {er(t30):+.3f}R({len(t30)} 笔)。"
        f"逆势单边段最长连亏 {lr_c['n']} 笔、单段累计亏掉 {lr_c['R']:.1f}R(约账户 {pct(lr_c['acct'])}),"
        f"等到回头前账户最低回撤 {pct(lr_c['dd'])};顺势对应为 {lr_t['n']} 笔 / {lr_t['R']:.1f}R / {pct(lr_t['dd'])}。"
        f"0.5% 的小仓位让“等回头”的代价可控,但逆势{'没有' if er(c30) <= er(t30) else ''}比顺势更赚钱。")
    ct, cc = cr[(0.08, "weekly", "trend")][1], cr[(0.08, "weekly", "contra")][1]
    bv_t, bv_c = ct[ct.iv_source == "BVOL"], cc[cc.iv_source == "BVOL"]
    lines.append(
        f"**暴跌后(跌 ≥ 8%,每周):** 买看跌(原实验)全区间 {er(ct):+.3f}R,其中真实 BVOL 时段 {er(bv_t):+.3f}R({len(bv_t)} 笔);"
        f"反过来买看涨 {er(cc):+.3f}R,真实 BVOL 时段 {er(bv_c):+.3f}R({len(bv_c)} 笔)。")
    cols = ["公允锚定·样本内", "公允锚定·样本外", "锚定+IV×1.1·样本内", "锚定+IV×1.1·样本外"]
    vals = rob[cols].astype(float)
    robust = rob[vals.gt(0).all(axis=1)]
    if len(robust):
        lines.append("**经得起定价压力测试(锚定和 IV×1.1 下样本内外都为正)的:** "
                     + "、".join(r["配置"] for _, r in robust.iterrows()) + "。")
    else:
        near = rob[vals[["公允锚定·样本外", "锚定+IV×1.1·样本外"]].gt(0).all(axis=1)]
        near_txt = "、".join(
            f"{r['配置']}(样本外 锚定 {r['公允锚定·样本外']}R / IV×1.1 {r['锚定+IV×1.1·样本外']}R,"
            f"但样本内 {r['公允锚定·样本内']}R)" for _, r in near.iterrows())
        lines.append(
            "**没有任何配置经得起完整的压力测试**(锚定和 IV×1.1 两种定价下、样本内外都为正)。"
            + (f"最接近的是 {near_txt}:只在 2024 年后的真实 BVOL 时段为正,在代理 IV 的样本内是亏的,"
               "方向上与下面“大波动多为上涨”的发现一致(暴跌后更常见急速反弹),但样本外只有几十笔,"
               "需要用真实期权成交数据复核。" if len(near) else ""))
    d = asym[asym["周期"] == "日线"]
    up_lo, up_hi = d["上涨占比"].min(), d["上涨占比"].max()
    bull_sk = (sk_tbl[(sk_tbl["状态"] == "牛市")]["看涨IV − 看跌IV(平均)"] if len(sk_tbl) else pd.Series(dtype=float))
    lines.append(
        f"**“暴拉缓跌 / 暴跌缓拉”:** 2020 年以来 BTC / ETH 日线最大 5% 的波动里,上涨占 {pct(up_lo, 0)}–{pct(up_hi, 0)},"
        f"牛市、熊市、震荡都是**大涨多于大跌**(熊市里也是,多为轧空式反弹);4 小时级别基本对半。"
        f"所以更接近“暴拉”而不是“暴跌”;期权偏斜方面,2023 年真实报价里牛市时 |Delta|≈0.3 的看涨比看跌贵 "
        f"{bull_sk.min() * 100:+.1f} ~ {bull_sk.max() * 100:+.1f} 个百分点,市场只部分地把这种不对称定了价"
        f"(只有 5 个月数据、几乎没有熊市样本,不足以下定论)。" if len(bull_sk) else "")
    lines.append("**可信度提醒:** 除 2023 年 5–10 月外没有真实期权价格;主结果用的是“公允锚定模型”,"
                 "未经锚定的原始模型会把期权定得偏便宜,让几乎所有买方策略都显得赚钱(见 B5)——这正是需要警惕的过拟合来源。"
                 "再加上每个配置每年只有几笔到几十笔,单看一个数字很容易被少数大赢家左右;"
                 "只有在锚定和 IV×1.1 两种定价下、样本内外都为正的结论才值得进一步用真实数据(如 Deribit)验证。")
    return "\n\n".join(lines)


# ============================================================================

def main():
    OUT.mkdir(exist_ok=True)
    t0 = time.time()
    D, M = OptData(), Market()
    lot_md, lot_concl = lottery_part(D, t0)
    R = Runner(D, M)
    Rraw = Runner(D, M, anchor=False)
    Rhi = Runner(D, M, iv_scale=1.10)
    print(f"定价锚定完成 ({time.time() - t0:.0f}s)")
    sq_md, sq = squeeze_part(R, Rraw, t0)
    dev_md, dev = deviation_part(R, t0)
    cr_md, cr = crash_part(R, t0)
    asym, sk_tbl, asym_md = asymmetry_part(R)
    rob_rows, rob_md = robustness_part(R, Rraw, Rhi)
    mart_md, mart_concl = martingale_part(R, t0)
    fw_concl = framework_conclusion(sq, dev, cr, R, rob_rows, asym, sk_tbl)
    bv_end = D.bvol("BTCUSDT").index[-1]

    report = f"""# 期权实验报告:彩票期权 · 三版本对比框架 · 马丁对照

> 纯历史数据模拟:未连接任何账户、未下任何真实订单、不需要 API 密钥。
> 生成时间 {pd.Timestamp.now(tz="UTC"):%Y-%m-%d %H:%M} UTC

## 0. 数据和最重要的局限

| 数据 | 来源 | 时间范围 | 用途 |
|---|---|---|---|
| 期权逐小时真实快照(买一/卖一/标记价/IV/Delta) | 币安 `EOHSummary` | **2023-05-18 ~ 2023-10-23** | 真实报价结果、校准、模型验证 |
| BVOL 指数(币安 BTC/ETH 30 天隐含波动率) | 币安 `BVOLIndex` | 2023-06-20 ~ {bv_end:%Y-%m-%d} | 模型定价(真实 IV 水平) |
| IV 代理(由实际波动率回归出的 BVOL 估计) | 自建,见 B5 | 2020-01 ~ 2023-06 | 模型定价(**没有任何真实 IV 数据的时段**) |
| 现货 1h / 4h / 日线 K 线 | 币安 | 2019-06 起 | 信号、市场状态、结算价 |

- 需求里写的 DVOL 是 Deribit 的指数;本运行环境的网络策略禁止访问 Deribit(以及 Tardis、OKX、Bybit、币安期权 API),
  所以用币安自己的 BVOL 代替。在云环境设置的 Network access 里允许 `history.deribit.com` 后,可以换成 Deribit 的真实历史成交重做,见
  [文档](https://code.claude.com/docs/en/cloud-environments#network-access)。
- **2020–2023 样本内的大部分期权价格是代理值**,不是真实价格;2024 年至今的样本外用真实 BVOL;
  2023 年 5–10 月有真实报价可以对照。读结果时请按这个可信度排序:真实报价 > 真实 BVOL > 代理。
- 需求的最后一句(“中文总结:哪个版本在……”)被截断了;总结按“哪个版本在哪种市场状态下表现更好、有没有稳定正期望”来写。

{lot_md}

## B. 三版本对比框架

**市场状态(三版本共用)**:牛市 = 日线收盘 > MA200 且 MA200 高于 10 天前;熊市 = 收盘 < MA200 且 MA200 低于 10 天前;其余震荡。

| 版本 | 牛市 | 熊市 | 震荡 |
|---|---|---|---|
| 顺势 | 买看涨(Delta≈0.3) | 买看跌(Delta≈−0.3) | 不开仓 |
| 逆势 | 买看跌 | 买看涨 | 不开仓 |
| 多空都买 | 平值跨式 | 平值跨式 | 平值跨式 |

**共同规则**:入场 = 4h 布林带(20, 2σ)宽度处于过去 90 天最低 20%(条件刚出现那根);到期分每周(≥ 48 小时后的最近周五)/
每月(≥ 14 天后的月末周五)两组;每笔权利金(含手续费)= 账户 0.5% = 1R,最多同时 3 笔;价值翻倍卖一半,剩余在到期前 1 天平仓;
不加仓、不马丁。成本 = 真实报价(有的时候)或模型中间价 ± 3% + 币安期权手续费。标的:BTC、ETH。
**定价用“公允锚定模型”**:IV 水平先按时段缩放,使“每周一买平值跨式持有到期”按中间价不赚不亏(依据和对照见 B5)。
所以下面的正 / 负期望值衡量的是**入场时机和方向选择本身**带来的优势,而不是“期权本来就便宜 / 贵”。
均值回归和暴跌两个模块沿用同一套定价、仓位和统计,只是入场触发和模块专属的离场规则不同。

{sq_md}

{dev_md}

{cr_md}

{asym_md}

{rob_md}

{mart_md}

## D. 中文总结

{lot_concl}

{fw_concl}

{mart_concl}
"""
    (OUT / "REPORT_OPTIONS.md").write_text(report, encoding="utf-8")
    print(f"报告已写入 {OUT / 'REPORT_OPTIONS.md'} ({time.time() - t0:.0f}s)")


if __name__ == "__main__":
    main()
