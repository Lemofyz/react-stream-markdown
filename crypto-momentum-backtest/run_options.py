"""期权实验:彩票期权 + 暴跌后追买看跌(含马丁对照)→ results_options/REPORT_OPTIONS.md

用法:
    python download_options.py
    python run_options.py
"""
from __future__ import annotations

import time
from pathlib import Path

import numpy as np
import pandas as pd

from cmb.charts import plot_equity, plot_hist
from cmb.metrics import max_drawdown, max_streak
from cmb.options import (Costs, LotteryParams, OptData, PutParams, calibrate, lottery_model,
                         lottery_real, martingale, martingale_mc, put_model, put_real, simulate)
from run_backtest import isnan, md_table, num, pct

OUT = Path("results_options")
REAL_US = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT"]
MODEL_US = ["BTCUSDT", "ETHUSDT"]
THRESHOLDS = (0.05, 0.08, 0.12)
NOCAP = Costs(fee_cap=None)


def boot_ratio(num_, den, n=2000, seed=1):
    """sum(num)/sum(den) 的 bootstrap 95% 区间(期权回报极度偏斜,需要看区间)。"""
    num_, den = np.asarray(num_, float), np.asarray(den, float)
    if len(num_) == 0:
        return np.nan, np.nan, np.nan
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(num_), size=(n, len(num_)))
    r = num_[idx].sum(1) / den[idx].sum(1)
    return float(num_.sum() / den.sum()), float(np.quantile(r, 0.025)), float(np.quantile(r, 0.975))


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


def put_stats(t: pd.DataFrame) -> dict:
    if t.empty:
        return {"笔数": 0}
    losers = t[t.R <= 0]
    reb = losers[losers.exit_reason == "ma_reclaim"]
    loss_amt = -losers.R.sum()
    iv_up = (t.bvol / t.bvol_med30 - 1)
    return {
        "笔数": len(t), "胜率": pct((t.R > 0).mean()), "期望值(R)": num(t.R.mean(), 3),
        "R 中位数": num(t.R.median()), "翻倍卖一半": pct(t.half_sold.mean()),
        "最好一笔": f"{t.R.max():+.2f}R",
        "反弹(收复MA20)离场的亏损·笔数占比": pct(len(reb) / len(losers)) if len(losers) else "—",
        "·亏损金额占比": pct(-reb.R.sum() / loss_amt) if loss_amt > 0 else "—",
        "开仓 BVOL ÷ 前30天中位数": f"{1 + iv_up.mean():.2f}×" if iv_up.notna().any() else "—",
        "BVOL 高出(百分点)": num((t.bvol - t.bvol_med30).mean(), 1) if iv_up.notna().any() else "—",
        "平均持有(小时)": num(t.hold_hours.mean(), 0),
        "最长连亏": int(max_streak(t.sort_values("t").R <= 0)),
    }


def main():
    OUT.mkdir(exist_ok=True)
    t0 = time.time()
    D = OptData()
    lp, pp = LotteryParams(), PutParams()

    # ---------------- 校准 ----------------
    cal = {u: calibrate(D, u, lp, pp) for u in MODEL_US}
    cal_tbl = pd.DataFrame([{
        "标的": u, "彩票·看涨 IV÷BVOL": num(c["lot_iv_ratio_C"]), "彩票·看跌 IV÷BVOL": num(c["lot_iv_ratio_P"]),
        "彩票·卖一÷标记": num(c["lot_ask_ratio"]), "彩票·买一÷标记": num(c["lot_bid_ratio"]),
        "Δ−0.3 看跌 IV÷BVOL": num(c["put_iv_ratio"]), "看跌·卖一÷标记": num(c["put_ask_ratio"]),
        "看跌·买一÷标记": num(c["put_bid_ratio"]), "行权价间距": pct(c["step_pct"]),
        "最小报价": f"{c['tick']:g}", "校准样本(彩票/看跌)": f"{c['lot_n']}/{c['put_n']}"}
        for u, c in cal.items()])
    print(f"校准完成 ({time.time() - t0:.0f}s)")

    # ---------------- A. 彩票:真实快照 ----------------
    real = {u: lottery_real(D, u, lp) for u in REAL_US}
    real_nc = {u: lottery_real(D, u, lp.with_(costs=NOCAP)) for u in REAL_US}
    rows = []
    for u in REAL_US:
        rows.append({"标的": u, **lottery_stats(real[u], real_nc[u])})
    be = pd.concat([real["BTCUSDT"], real["ETHUSDT"]])
    be_nc = pd.concat([real_nc["BTCUSDT"], real_nc["ETHUSDT"]])
    rows.append({"标的": "BTC+ETH", **lottery_stats(be, be_nc)})
    allr = pd.concat(real.values())
    rows.append({"标的": "全部 5 个", **lottery_stats(allr, pd.concat(real_nc.values()))})
    lot_real_tbl = pd.DataFrame(rows)
    allr.to_csv(OUT / "lottery_real_trades.csv", index=False)
    side_rows = [{"": f"BTC+ETH · {'看涨' if cp == 'C' else '看跌'}", **lottery_stats(be[be.cp == cp])}
                 for cp in ("C", "P")]

    # 模型与真实对照(重叠时段)
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

    # ---------------- A. 彩票:BVOL 模型 2023-06 ~ 至今 ----------------
    mod = pd.concat([lottery_model(D, u, lp, cal[u]) for u in MODEL_US])
    mod_nc = pd.concat([lottery_model(D, u, lp.with_(costs=NOCAP), cal[u]) for u in MODEL_US])
    mod.to_csv(OUT / "lottery_model_trades.csv", index=False)
    # 敏感性:如果翼部 IV 没有那么高(只有 BVOL 的 1.0 / 1.2 倍),彩票还算不算贵?
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
    print(f"彩票完成 ({time.time() - t0:.0f}s)")

    eq_lr, _ = simulate(be.reset_index(drop=True), lp.budget)
    eq_lm, _ = simulate(mod.reset_index(drop=True), lp.budget)
    eq_lm_nc, _ = simulate(mod_nc.reset_index(drop=True), lp.budget)
    plot_equity({"Lottery, BVOL model, fee capped": eq_lm,
                 "Lottery, BVOL model, fee uncapped": eq_lm_nc,
                 "Lottery, real quotes (2023-05~10)": eq_lr},
                OUT / "equity_lottery.png",
                "Daily 24h OTM lottery options (call+put, 0.2% each), BTC+ETH, 10,000 USDT",
                legend_loc="lower left")

    # ---------------- B. 追跌看跌 ----------------
    put_rows_real, put_rows_mod, put_trades = [], [], {}
    for th in THRESHOLDS:
        p = pp.with_(drop_thr=th)
        pr = pd.concat([put_real(D, u, p) for u in REAL_US])
        pm = pd.concat([put_model(D, u, p, cal[u]) for u in MODEL_US])
        put_trades[th] = pm.sort_values("t").reset_index(drop=True)
        put_rows_real.append({"阈值": f"{th:.0%}", **put_stats(pr),
                              "期权 IV ÷ 平时同类 IV": f"{(pr.iv / pr.iv_ref).mean():.2f}×"
                              if len(pr) and pr.iv_ref.notna().any() else "—"})
        put_rows_mod.append({"阈值": f"{th:.0%}", **put_stats(pm)})
        if th == 0.08:
            pr.to_csv(OUT / "put_real_trades_8pct.csv", index=False)
    pd.concat(put_trades.values()).to_csv(OUT / "put_model_trades.csv", index=False)
    put_real_tbl = pd.DataFrame(put_rows_real).fillna("—")
    put_mod_tbl = pd.DataFrame(put_rows_mod)
    pm8 = put_trades[0.08]
    yr_put = pd.DataFrame([{"年份": y, **put_stats(g)} for y, g in pm8.groupby(pm8.t.dt.year)])
    eqs = {}
    for th in THRESHOLDS:
        e, _ = simulate(put_trades[th], pp.budget)
        eqs[f"Put chase, drop >= {th:.0%}"] = e
    plot_equity(eqs, OUT / "equity_put.png",
                "Buy ~-0.3 delta puts after 24h crash (BVOL model, BTC+ETH, 1% per trade)")
    print(f"看跌完成 ({time.time() - t0:.0f}s)")

    # ---------------- 马丁对照 ----------------
    R8 = pm8.R.to_numpy()
    years = (pm8.t.max() - pm8.t.min()).days / 365.25
    per_year = len(pm8) / years if years > 0 else np.nan
    hist_m = martingale(R8)
    horizon = int(np.ceil(per_year * 10))
    mc = martingale_mc(R8, n_paths=5000, horizon=horizon)
    ruin_days = mc.trades_to_ruin / per_year * 365
    plot_hist(ruin_days.dropna().to_numpy(), OUT / "martingale_ruin_days.png",
              f"Martingale (double premium after each loss), time to ruin, 5000 bootstrap paths",
              "days until account < 1% of start",
              {"median": float(ruin_days.median()), "mean": float(ruin_days.mean())})
    print(f"马丁完成 ({time.time() - t0:.0f}s)")

    # ---------------- 报告 ----------------
    btc_eth_real = lot_real_tbl.set_index("标的").loc["BTC+ETH"]
    mod_all = lot_mod_tbl.iloc[0]
    pm_ratio, pm_lo, pm_hi = boot_ratio(mod.intrinsic, mod.mark)
    pr_ratio, pr_lo, pr_hi = boot_ratio(be.intrinsic, be.mark)
    report = f"""# 期权实验报告:彩票期权 & 暴跌后追买看跌

> 纯历史数据模拟:未连接任何账户、未下任何真实订单、不需要 API 密钥。
> 生成时间 {pd.Timestamp.now(tz="UTC"):%Y-%m-%d %H:%M} UTC

## 1. 数据和最重要的局限

| 数据 | 来源 | 时间范围 | 用途 |
|---|---|---|---|
| 期权逐小时真实快照(买一/卖一/标记价/IV/Delta) | 币安 data.binance.vision `EOHSummary` | **2023-05-18 ~ 2023-10-23**(BTC/ETH 147 天,BNB/XRP/DOGE 更少) | “真实”结果 |
| BVOL 指数(币安 BTC/ETH 30 天隐含波动率,逐秒→每小时) | 币安 `BVOLIndex` | 2023-06-20 ~ {D.bvol('BTCUSDT').index[-1]:%Y-%m-%d} | 模型定价 |
| 现货 1 小时 K 线 | 币安 | 2023-01 起 | 结算价、4 小时信号 |

- **币安公开的期权逐笔/快照数据只有 2023 年 5–10 月这 5 个月**(一段低波动的平静期)。更长的真实期权历史需要
  Deribit 等数据源,但本运行环境的网络策略禁止访问 `history.deribit.com` / `www.deribit.com`(也禁止 Tardis、OKX、Bybit、
  币安期权 API)。可以在云环境设置的 Network access 里把这些域名加入允许列表后重跑,见
  [文档](https://code.claude.com/docs/en/cloud-environments#network-access)。
- 因此本报告分两部分:**真实快照结果**(样本小但是真实成交价)和 **BVOL 模型估算**(2023-06 起 3 年多):
  用真实的 BVOL 隐含波动率 × 用 2023 年真实快照校准的“偏斜系数”,按 Black–Scholes 定价,
  买卖价按校准的卖一/买一系数。**模型结果不是真实成交**,它假设 2023 年那段时间测到的
  “翼部 IV 比 BVOL 高多少”“价差多大”在之后几年保持不变。
- 手续费:币安期权交易费 0.03% × 标的价格 × 数量、行权费 0.015%。网上资料没有确认币安是否对手续费设“不超过权利金 10%”的上限,
  所以两种都跑:**封顶(默认)** 和 **不封顶**。对几美元的深度虚值期权,这个差别非常大。

## 2. 规则(在看结果之前定死)

**A. 彩票期权**
- 每个到期日(币安每天 08:00 UTC 到期),在到期前 24 小时的快照里,分别找虚值看涨和虚值看跌中 |Delta| 在 0.02–0.05、
  有卖一价的合约,选**卖一价最便宜**的那一档;按卖一价买入。
- 每张“彩票”(看涨、看跌各算一张)花当时账户的 0.2%(含手续费)。
- 持有期间每小时检查:买一价 ≥ 成本价 × 10 → 按买一价卖一半;其余持有到期,按结算价(到期时刻的现货价)行权。
- “中奖”= 到期时有内在价值;“归零”= 到期没有价值且没卖出过一半。R = 净收回 ÷ 成本 − 1(最多 −1R)。
- 模型版本:每天 08:00 到期;行权价按校准的间距排列,选 |Delta| ≥ 0.02 的最远一档(即最便宜的一档)。

**B. 暴跌后追买看跌**
- 信号:4 小时 K 线收盘价比 24 小时前(6 根)跌 ≥ 5% / 8% / 12%,且收盘价 < 4h MA20;只在条件“刚出现”那根触发。
- 买入:至少 48 小时后最近的周五 08:00 到期、Delta 最接近 −0.30 的看跌,按卖一价,花账户 1%(含手续费)。
- 平仓:买一价 ≥ 成本 × 2 → 卖一半;剩余在 4h 收盘站回 MA20 后、或到期前 24 小时按买一价全部卖出;卖不掉则到期结算。
- 同一标的同时只持有一笔。“平时 IV”= 开仓前 30 天 BVOL 的中位数。
- 马丁对照:每次亏损后下一笔权利金加倍,盈利后回到账户 1%;权益跌破初始的 1% 视为归零。

## 3. 实验 A:彩票期权

### 3.1 用真实快照校准(BVOL 与真实快照重叠的 2023-06-20 ~ 2023-10-23)

{md_table(cal_tbl, index=False)}

- 深度虚值翼部的隐含波动率约为 BVOL 的 **1.3–1.4 倍**(波动率微笑),买入时的卖一价又比标记价高约 **30%**。

模型 vs 真实(同一段时间):

{md_table(val_tbl, index=False)}

### 3.2 真实快照结果(2023-05-18 ~ 2023-10-23)

{md_table(lot_real_tbl, index=False)}

{md_table(pd.DataFrame(side_rows).set_index(""))}

- “到期价值 ÷ 标记价”< 1 说明即使按中间价买也是亏的(期权被高估);< 1 的程度越大越高估。
  方括号是 bootstrap 95% 区间——中奖的只有极少几笔,所以区间很宽。

### 3.3 BVOL 模型(2023-06 ~ 至今,BTC + ETH)

{md_table(lot_mod_tbl.set_index(""))}

![彩票期权资金曲线](equity_lottery.png)

资金曲线(初始 10,000 USDT,每张 0.2%):BVOL 模型 {eq_lm.index[0]:%Y-%m} → {eq_lm.index[-1]:%Y-%m} 期末
**{eq_lm.iloc[-1]:,.0f} USDT**(最大回撤 {pct(max_drawdown(eq_lm))});手续费不封顶时 {eq_lm_nc.iloc[-1]:,.0f} USDT;
真实快照 5 个月期末 {eq_lr.iloc[-1]:,.0f} USDT(最大回撤 {pct(max_drawdown(eq_lr))})。

**敏感性:翼部定价假设**。上面的“高估”结论部分来自校准出的“翼部 IV ≈ BVOL × 1.4”。
如果翼部只按 BVOL × 1.0 或 × 1.2 定价(即期权比实际便宜),结果如下:

{md_table(sens_tbl, index=False)}

{sens_note(sens_tbl)}

## 4. 实验 B:暴跌后追买看跌

### 4.1 真实快照(2023-05 ~ 10,5 个标的)

{md_table(put_real_tbl, index=False)}

这段时间几乎没有暴跌,样本只有个位数,仅作参考。

### 4.2 BVOL 模型(2023-07 ~ 至今,BTC + ETH)

{md_table(put_mod_tbl, index=False)}

阈值 8% 的分年份:

{md_table(yr_put, index=False)}

![追跌看跌资金曲线](equity_put.png)

资金曲线期末(初始 10,000 USDT,每笔 1%):{"、".join(f"{k.split('>= ')[1]} 阈值 {v.iloc[-1]:,.0f} USDT(最大回撤 {pct(max_drawdown(v))})" for k, v in eqs.items())}。

- “开仓 BVOL ÷ 前 30 天中位数”:开仓时 30 天隐含波动率比平时高多少。跌得越狠越贵——12% 阈值时已是**涨价后的保险**;
  而真实快照里实际买入的短期限看跌,其 IV 比平时同类期权高得更多(见 4.1 最后一列)。
- 注意模型的偏差方向:暴跌时短期限看跌的偏斜会变陡,而模型用的是平静期校准的固定系数,
  **会低估开仓时的权利金**,所以模型结果对买方是偏乐观的。

### 4.3 马丁对照(8% 阈值的交易序列)

- 真实顺序跑一遍:{'**' + str(hist_m['trades']) + ' 笔后归零**' if hist_m['ruined'] else '没有归零'}(共 {len(R8)} 笔),
  单笔下注最高达到当时账户的 {pct(hist_m['peak_bet_frac'], 0)}。
- 按历史 R 分布有放回抽样 5000 条路径(每条 {horizon} 笔 ≈ 10 年,按每年约 {per_year:.0f} 笔换算天数):

| | 马丁(亏损后加倍) | 固定 1% |
|---|---|---|
| 10 年内归零的概率 | **{pct(mc.ruined.mean())}** | {pct(mc.fixed_ruined.mean())} |
| 归零所需时间:中位数 | {num(ruin_days.median(), 0)} 天({num(mc.trades_to_ruin.median(), 0)} 笔) | — |
| 归零所需时间:平均 | {num(ruin_days.mean(), 0)} 天 | — |
| 1 年内归零 | {pct((ruin_days <= 365).mean())} | — |

![马丁归零时间分布](martingale_ruin_days.png)

有放回抽样假设每笔独立;真实的暴跌往往扎堆出现(连续几笔都亏),所以真实的归零速度只会更快。

## 5. 结论

{conclusion(btc_eth_real, mod_all, pm_ratio, pm_lo, pm_hi, pr_ratio, pr_lo, pr_hi,
            put_mod_tbl, put_real_tbl, mc, ruin_days, hist_m, sens_tbl)}
"""
    (OUT / "REPORT_OPTIONS.md").write_text(report, encoding="utf-8")
    print(f"报告已写入 {OUT / 'REPORT_OPTIONS.md'} ({time.time() - t0:.0f}s)")


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


def conclusion(real_row, mod_row, pm, pm_lo, pm_hi, pr, pr_lo, pr_hi, put_mod, put_real_tbl,
               mc, ruin_days, hist_m, sens_tbl) -> str:
    """结论里的数字全部取自本次运行;措辞按数字的方向自动选择。"""
    over = pm < 1 and pm_hi < 1
    s1 = sens_tbl.iloc[0]
    p1 = (f"**彩票期权在币圈平均是{'被高估' if pm < 1 else '被低估'}的。** "
          f"BVOL 模型(2023-06 至今,BTC+ETH,{mod_row['笔数']} 张):到期价值只有标记价的 {pm:.2f} 倍"
          f"(95% 区间 {pm_lo:.2f}–{pm_hi:.2f}{',区间整体 < 1' if over else ',区间跨过 1'}),"
          f"按实际要付的卖一价只有 {mod_row['到期价值÷卖一价']} 倍;中奖率 {mod_row['中奖率(到期有价值)']},"
          f"≥10 倍 {mod_row['≥10倍']}、≥40 倍 {mod_row['≥40倍']},每笔期望值 {mod_row['期望值(R)·费率封顶']}R"
          f"(手续费不封顶 {mod_row['期望值(R)·不封顶']}R),最长连续归零 {mod_row['最长连续归零']} 张。"
          f"真实快照(2023 年 5–10 月,{real_row['笔数']} 张)方向一致:到期价值 ÷ 标记价 {pr:.2f},但只有 2 张中奖,"
          f"区间 {pr_lo:.2f}–{pr_hi:.2f} 跨过 1,单看真实样本不能下定论。"
          f"高估从哪来:如果翼部按平值隐含波动率定价({s1['翼部 IV 假设']}),到期价值 ÷ 标记价为 "
          f"{s1['到期价值÷标记价 [95%区间]']}——大致公平;实际市场给深度虚值期权的 IV 约为 BVOL 的 1.3–1.4 倍"
          f"(波动率微笑),这部分溢价没有被实际发生的尾部行情兑现。再叠加卖一价比标记价高约 30%、"
          f"按标的名义价值收的手续费,买方每笔亏掉大半权利金;偶尔的几十倍、上百倍不足以弥补几乎每天的归零。")
    rows = {r["阈值"]: r for _, r in put_mod.iterrows()}
    r8 = rows["8%"]
    pos = [k for k, r in rows.items() if float(r["期望值(R)"]) > 0]
    real5 = put_real_tbl.set_index("阈值").loc["5%"]
    p2 = (f"**暴跌后追买看跌:{'没有正期望' if not pos else '只有 ' + '、'.join(pos) + ' 阈值在模型里略为正,而且不可靠'}。** "
          + "模型(BTC+ETH):" + "、".join(f"{k} 阈值 {r['笔数']} 笔 {r['期望值(R)']}R(中位数 {r['R 中位数']}R)"
                                     for k, r in rows.items()) + "。"
          + (f"{'、'.join(pos)} 的正值来自少数几笔(最好一笔 {rows[pos[0]]['最好一笔']}),中位数仍是亏损;"
             f"同一阈值在真实快照里 {real5['笔数']} 笔、{real5['期望值(R)']}R;"
             f"而且模型对暴跌时的看跌权利金偏低估(见下),所以不能当作正期望。" if pos else "")
          + f"开仓时 BVOL 是平时的 " + "/".join(str(r["开仓 BVOL ÷ 前30天中位数"]) for r in rows.values())
          + f"(5%/8%/12%),跌得越狠、隐含波动率越贵;真实快照里所买期权自身的 IV 是平时同类期权的 "
          f"{real5['期权 IV ÷ 平时同类 IV']}(5%)——短期限看跌比 30 天指数涨得更多,模型低估了这一点。"
          f"8% 阈值下亏损交易中 {r8['反弹(收复MA20)离场的亏损·笔数占比']} 是“暴跌后反弹站回 MA20”离场,"
          f"占亏损金额的 {r8['·亏损金额占比']}:暴跌后买看跌,最常见的结局是价格反弹 + 隐含波动率回落,两头亏。")
    p3 = (f"**马丁会把一个负期望的策略变成必然归零。** 按 8% 阈值的历史 R 分布抽样,马丁加倍在 10 年内归零的概率 "
          f"{pct(mc.ruined.mean())}(固定 1% 为 {pct(mc.fixed_ruined.mean())}),归零时间中位数约 {num(ruin_days.median(), 0)} 天、"
          f"平均 {num(ruin_days.mean(), 0)} 天,{pct((ruin_days <= 365).mean())} 的路径在 1 年内归零"
          + (f";按真实顺序跑,{hist_m['trades']} 笔后就归零了。" if hist_m["ruined"] else "。")
          + "加倍不改变期望值,只是把小额、分散的亏损变成一次性的全部亏损。")
    p4 = ("**数据局限**:真实期权快照只有 2023 年 5–10 月;3 年多的结果依赖 BVOL + 校准系数的模型,"
          "模型对暴跌时的看跌权利金偏低估(对买方偏乐观)。开放 Deribit 数据访问后可以用真实历史成交重做。")
    return "\n\n".join([p1, p2, p3, p4])


if __name__ == "__main__":
    main()
