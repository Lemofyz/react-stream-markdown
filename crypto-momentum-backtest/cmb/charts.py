"""图表:资金曲线、单笔交易 K 线图。"""
from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.patches import Rectangle  # noqa: E402

# 浅色背景;分类色按固定顺序使用
SURFACE, INK, INK2, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e4e3df"
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#6250d6"]
UP, DOWN = "#1baf7a", "#e34948"
plt.rcParams["axes.unicode_minus"] = False


def style_ax(ax):
    ax.set_facecolor(SURFACE)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(GRID)
    ax.tick_params(colors=INK2, labelsize=8)
    ax.grid(True, color=GRID, linewidth=0.6)
    ax.set_axisbelow(True)


def plot_equity(curves: dict[str, pd.Series], path: Path, title: str):
    fig, (a1, a2) = plt.subplots(2, 1, figsize=(11, 6.5), sharex=True,
                                 gridspec_kw={"height_ratios": [3, 1]})
    fig.patch.set_facecolor(SURFACE)
    for ax in (a1, a2):
        style_ax(ax)
    for (name, s), col in zip(curves.items(), SERIES):
        a1.plot(s.index, s.values, color=col, lw=2, label=name)
        a1.annotate(f"{s.iloc[-1]:,.0f}", (s.index[-1], s.iloc[-1]), xytext=(4, 0),
                    textcoords="offset points", color=INK2, fontsize=8, va="center")
        dd = s / s.cummax() - 1
        a2.plot(dd.index, dd.values * 100, color=col, lw=1.4)
    a1.set_yscale("log")
    a1.set_ylabel("USDT (log)", color=INK2)
    a1.set_title(title, color=INK, fontsize=11, loc="left")
    a1.legend(frameon=False, labelcolor=INK2, loc="upper left", fontsize=9)
    a2.set_ylabel("Drawdown %", color=INK2)
    fig.tight_layout()
    fig.savefig(path, dpi=120, facecolor=SURFACE)
    plt.close(fig)


def plot_trade(panel: dict[str, pd.DataFrame], tr: pd.Series, path: Path,
               ma_fast: int, ma_slow: int, runup: int, title: str):
    """K 线图:横盘区间(灰底)、区间最高价、突破日、买入、止损(含移到成本价)、卖出点。"""
    sym = tr.symbol
    sig = pd.Timestamp(tr.signal_date)
    blen = int(tr.base_len) if not np.isnan(tr.base_len) else 0
    base_start = sig - pd.Timedelta(days=blen)
    lo = base_start - pd.Timedelta(days=runup + 10)
    hi = pd.Timestamp(tr.exit_date) + pd.Timedelta(days=8)
    o, h, l, c, v = (panel[k][sym] for k in ("open", "high", "low", "close", "volume"))
    full_c = c
    df = pd.DataFrame({"o": o, "h": h, "l": l, "c": c, "v": v}).loc[lo:hi].dropna()
    mf = full_c.rolling(ma_fast).mean().loc[df.index]
    ms = full_c.rolling(ma_slow).mean().loc[df.index]

    fig, (a1, a2) = plt.subplots(2, 1, figsize=(11, 5.6), sharex=True,
                                 gridspec_kw={"height_ratios": [3.2, 1]})
    fig.patch.set_facecolor(SURFACE)
    for ax in (a1, a2):
        style_ax(ax)
    x = mdates_num(df.index)
    w = 0.7
    for xi, (_, r) in zip(x, df.iterrows()):
        col = UP if r.c >= r.o else DOWN
        a1.vlines(xi, r.l, r.h, color=col, lw=0.8)
        a1.add_patch(Rectangle((xi - w / 2, min(r.o, r.c)), w, max(abs(r.c - r.o), 1e-12),
                               color=col, lw=0))
        a2.bar(xi, r.v, width=w, color=col, alpha=0.6)
    a1.plot(x, mf.values, color=SERIES[0], lw=1.2, label=f"MA{ma_fast}")
    a1.plot(x, ms.values, color=SERIES[3], lw=1.2, label=f"MA{ma_slow}")

    if blen:
        bx0, bx1 = mdates_num([base_start])[0] - 0.5, mdates_num([sig])[0] - 0.5
        for ax in (a1, a2):
            ax.axvspan(bx0, bx1, color="#bdbcb6", alpha=0.35, lw=0)
        a1.hlines(tr.base_high, bx0, bx1 + 1, color=INK2, lw=1.2, ls="--")
        a1.annotate(f"base {blen}d  high {tr.base_high:.4g}", (bx0, tr.base_high),
                    xytext=(0, 6), textcoords="offset points", fontsize=8, color=INK2)
        bvol = v.loc[base_start:sig - pd.Timedelta(days=1)].mean()
        a2.hlines(bvol, bx0, bx1, color=INK2, lw=1.2, ls="--")
    sx = mdates_num([sig])[0]
    a1.annotate("breakout", (sx, h.loc[sig]), xytext=(0, 14), textcoords="offset points",
                ha="center", fontsize=8, color=INK,
                arrowprops=dict(arrowstyle="-|>", color=INK, lw=0.8))

    ex = mdates_num([pd.Timestamp(tr.entry_date)])[0]
    xx = mdates_num([pd.Timestamp(tr.exit_date)])[0]
    a1.scatter([ex], [tr.entry_price], marker="^", s=70, color=SERIES[0], zorder=5,
               edgecolor=SURFACE, label="buy")
    # 止损线:初始止损,部分止盈后移到成本价
    if tr.partial_taken and not pd.isna(tr.partial_date):
        px = mdates_num([pd.Timestamp(tr.partial_date)])[0]
        a1.hlines(tr.stop, ex, px, color=DOWN, lw=1.4, label="stop")
        a1.hlines(tr.entry_price, px, xx, color=DOWN, lw=1.4, ls=":")
        a1.scatter([px], [tr.partial_price], marker="v", s=60, color=SERIES[1], zorder=5,
                   edgecolor=SURFACE, label="sell 1/3")
    else:
        a1.hlines(tr.stop, ex, xx, color=DOWN, lw=1.4, label="stop")
    a1.scatter([xx], [tr.exit_price], marker="X", s=70, color=SERIES[1], zorder=5,
               edgecolor=SURFACE, label=f"exit ({tr.exit_reason})")
    a1.set_title(title, color=INK, fontsize=10, loc="left")
    a1.legend(frameon=False, fontsize=7, labelcolor=INK2, loc="upper left", ncol=6)
    a2.set_ylabel("volume", color=INK2, fontsize=8)
    ticks = x[:: max(1, len(x) // 10)]
    a2.set_xticks(ticks)
    a2.set_xticklabels([pd.Timestamp.fromordinal(int(t)).strftime("%Y-%m-%d") for t in ticks],
                       rotation=0, fontsize=7)
    fig.tight_layout()
    fig.savefig(path, dpi=110, facecolor=SURFACE)
    plt.close(fig)


def mdates_num(idx) -> np.ndarray:
    return np.array([pd.Timestamp(t).toordinal() for t in idx], dtype=float)


def plot_mc_hist(samples: np.ndarray, hist: float, median: float, p95: float, path: Path,
                 title: str):
    fig, ax = plt.subplots(figsize=(9, 3.8))
    fig.patch.set_facecolor(SURFACE)
    style_ax(ax)
    ax.hist(samples * 100, bins=40, color=SERIES[0], alpha=0.85, edgecolor=SURFACE, lw=1)
    top = ax.get_ylim()[1]
    for (x, lab, ls), y in zip(((median, "median", "--"), (p95, "95th pct", "-"),
                                (hist, "actual order", ":")), (0.92, 0.80, 0.68)):
        ax.axvline(x * 100, color=INK, lw=1.4, ls=ls)
        ax.annotate(f"{lab} {x:.1%}", (x * 100, top * y), xytext=(4, 0),
                    textcoords="offset points", fontsize=8, color=INK,
                    bbox=dict(boxstyle="square,pad=0.15", fc=SURFACE, ec="none"))
    ax.set_xlabel("max drawdown % (trade-by-trade equity)", color=INK2, fontsize=9)
    ax.set_ylabel("simulations", color=INK2, fontsize=9)
    ax.set_title(title, color=INK, fontsize=10, loc="left")
    fig.tight_layout()
    fig.savefig(path, dpi=120, facecolor=SURFACE)
    plt.close(fig)
