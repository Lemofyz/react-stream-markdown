"""下载币安 USDT 本位永续合约的公开历史数据(做空实验用),包含已下架合约。

只访问公开的静态数据仓库 data.binance.vision,不需要 API 密钥,不连接任何账户。

两步:
    python download_futures.py daily     # 全部 USDT 永续的日线(约 15 分钟)
    python download_futures.py detail    # 只为出现过暴涨的合约下载 4 小时线 + 真实资金费率
                                         # (暴涨后 45 天内,覆盖等待期 + 最长持仓期)
"""
from __future__ import annotations

import argparse
import io
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd

from download_data import BUCKET, _get, list_prefix, read_zip
from cmb.universe import is_excluded_symbol

FUT = "data/futures/um"
OUT = Path("data/futures")
# 合约指数类标的,不是单一币种
INDEX_SYMBOLS = {"BTCDOMUSDT", "DEFIUSDT", "FOOTBALLUSDT", "BLUEBIRDUSDT", "USDCUSDT"}


def kline_frame(frames: list[pd.DataFrame]) -> pd.DataFrame:
    df = pd.concat(frames, ignore_index=True)
    ts = pd.to_numeric(df["open_time"])
    ts = ts.where(ts < 1e14, ts // 1000)
    df["time"] = pd.to_datetime(ts, unit="ms")
    df = df[["time", "open", "high", "low", "close", "volume", "quote_volume"]]
    return df.drop_duplicates("time").sort_values("time")


def fetch_klines(symbol: str, interval: str, months: list[str] | None, start: str) -> pd.DataFrame:
    _, keys = list_prefix(f"{FUT}/monthly/klines/{symbol}/{interval}/", delimiter=None)
    have = sorted(k for k in keys if k.endswith(".zip"))
    if months is None:
        want = [k for k in have if k[-11:-4] >= start]
    else:
        want = [k for k in have if k[-11:-4] in months]
    frames = [read_zip(_get(f"{BUCKET}/{k}").content) for k in want]
    last = have[-1][-11:-4] if have else start
    if months is None or any(m > last for m in months):
        # 当月尚未生成月度文件:用日度文件补齐
        _, dkeys = list_prefix(f"{FUT}/daily/klines/{symbol}/{interval}/", delimiter=None)
        frames += [read_zip(_get(f"{BUCKET}/{k}").content) for k in sorted(dkeys)
                   if k.endswith(".zip") and k[-14:-7] > last
                   and (months is None or k[-14:-7] in months)]
    return kline_frame(frames) if frames else pd.DataFrame()


def fetch_funding(symbol: str, months: list[str]) -> pd.DataFrame:
    frames = []
    for m in months:
        r = _get(f"{BUCKET}/{FUT}/monthly/fundingRate/{symbol}/{symbol}-fundingRate-{m}.zip")
        if r.status_code != 200:
            continue
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            frames.append(pd.read_csv(z.open(z.namelist()[0])))
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    df["time"] = pd.to_datetime(pd.to_numeric(df["calc_time"]), unit="ms")
    return df[["time", "last_funding_rate"]].rename(columns={"last_funding_rate": "rate"})


def stage_daily(args):
    out = OUT / "1d"
    out.mkdir(parents=True, exist_ok=True)
    dirs, _ = list_prefix(f"{FUT}/monthly/klines/")
    syms = sorted(d.split("/")[-2] for d in dirs)
    keep = [s for s in syms if s.endswith("USDT") and "_" not in s
            and s not in INDEX_SYMBOLS and not is_excluded_symbol(s)]
    print(f"USDT 永续合约 {len(keep)} 个")
    todo = [s for s in keep if args.refresh or not (out / f"{s}.csv.gz").exists()]

    def job(s):
        df = fetch_klines(s, "1d", None, args.start)
        if df.empty:
            return s, 0
        df = df.rename(columns={"time": "date"})
        df.to_csv(out / f"{s}.csv.gz", index=False, compression="gzip")
        return s, len(df)
    run_pool(job, todo, args.workers)


def stage_detail(args):
    from cmb.short_engine import ShortParams, pump_events
    from cmb.universe import TRADFI_PERPS, load_panel
    panel = load_panel(OUT / "1d")
    ev = pump_events(panel, ShortParams(pump_threshold=args.min_threshold, start="2019-09-01"))
    print(f"暴涨事件(≥{args.min_threshold:.0%}){len(ev)} 个,涉及 {ev.symbol.nunique()} 个合约")
    need_4h, need_fund = {}, {}
    for _, e in ev.iterrows():
        d = e.detect_date
        m4 = pd.date_range(d, d + pd.Timedelta(days=45), freq="D").strftime("%Y-%m")
        mf = pd.date_range(d, d + pd.Timedelta(days=45), freq="D").strftime("%Y-%m")
        need_4h.setdefault(e.symbol, set()).update(m4)
        need_fund.setdefault(e.symbol, set()).update(mf)
    (OUT / "4h").mkdir(parents=True, exist_ok=True)
    (OUT / "funding").mkdir(parents=True, exist_ok=True)

    def job(s):
        k = fetch_klines(s, "4h", sorted(need_4h[s]), "")
        if not k.empty:
            k.to_csv(OUT / "4h" / f"{s}.csv.gz", index=False, compression="gzip")
        f = fetch_funding(s, sorted(need_fund[s]))
        if not f.empty:
            f.to_csv(OUT / "funding" / f"{s}.csv.gz", index=False, compression="gzip")
        return s, len(k)
    run_pool(job, sorted(need_4h), args.workers)


def run_pool(job, items, workers):
    done = 0
    with ThreadPoolExecutor(workers) as ex:
        futs = {ex.submit(job, s): s for s in items}
        for fut in as_completed(futs):
            done += 1
            try:
                s, n = fut.result()
            except Exception as e:  # noqa: BLE001
                print(f"[{done}/{len(items)}] {futs[fut]} 失败: {e}")
                continue
            if done % 25 == 0:
                print(f"[{done}/{len(items)}] {s}: {n} 行")
    print("完成")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=["daily", "detail"])
    ap.add_argument("--start", default="2019-09")
    ap.add_argument("--min-threshold", type=float, default=1.0)
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args()
    stage_daily(args) if args.stage == "daily" else stage_detail(args)


if __name__ == "__main__":
    main()
