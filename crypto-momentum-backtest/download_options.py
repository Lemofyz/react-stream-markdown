"""下载期权实验用的公开数据(data.binance.vision),不需要 API 密钥。

- 期权逐小时快照 EOHSummary(真实买一/卖一价、标记价、隐含波动率、Delta):
  BTC / ETH / BNB / XRP / DOGE,币安只公开了 2023-05-18 ~ 2023-10-23 这一段
- BVOL 指数(币安 BTC / ETH 30 天隐含波动率指数,逐秒)→ 重采样为每小时:2023-06-20 起
- 标的现货 1 小时 K 线(用于到期结算价和 4 小时信号):2023-01 起

用法:
    python download_options.py
"""
from __future__ import annotations

import argparse
import io
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd

from download_data import BUCKET, _get, list_prefix, read_zip

OUT = Path("data/options")
EOH_UNDERLYINGS = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT"]
BVOL = {"BTCUSDT": "BTCBVOLUSDT", "ETHUSDT": "ETHBVOLUSDT"}


def unzip_csv(content: bytes) -> pd.DataFrame:
    with zipfile.ZipFile(io.BytesIO(content)) as z:
        return pd.read_csv(z.open(z.namelist()[0]))


def keys_under(prefix: str) -> list[str]:
    _, keys = list_prefix(prefix, delimiter=None)
    return sorted(k for k in keys if k.endswith(".zip"))


def pool(jobs, workers, label):
    out, done = [], 0
    with ThreadPoolExecutor(workers) as ex:
        futs = [ex.submit(j) for j in jobs]
        for f in as_completed(futs):
            done += 1
            out.append(f.result())
            if done % 100 == 0:
                print(f"  {label} {done}/{len(jobs)}")
    return out


def eoh(workers):
    for u in EOH_UNDERLYINGS:
        dest = OUT / "eoh" / f"{u}.csv.gz"
        if dest.exists():
            continue
        keys = keys_under(f"data/option/daily/EOHSummary/{u}/")
        frames = pool([lambda k=k: unzip_csv(_get(f"{BUCKET}/{k}").content) for k in keys],
                      workers, f"EOH {u}")
        df = pd.concat(frames, ignore_index=True)
        dest.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(dest, index=False, compression="gzip")
        print(f"EOH {u}: {len(keys)} 天, {len(df)} 行")


def bvol(workers):
    for u, sym in BVOL.items():
        dest = OUT / "bvol" / f"{u}.csv.gz"
        if dest.exists():
            continue
        keys = keys_under(f"data/option/daily/BVOLIndex/{sym}/")

        def job(k):
            df = unzip_csv(_get(f"{BUCKET}/{k}").content)
            t = pd.to_datetime(pd.to_numeric(df["calc_time"]), unit="ms")
            s = pd.Series(df["index_value"].astype(float).to_numpy(), index=t)
            return s.resample("1h").last()
        parts = pool([lambda k=k: job(k) for k in keys], workers, f"BVOL {u}")
        s = pd.concat(parts).sort_index()
        s = s[~s.index.duplicated()].dropna()
        dest.parent.mkdir(parents=True, exist_ok=True)
        s.rename("bvol").to_csv(dest, index_label="time", compression="gzip")
        print(f"BVOL {u}: {len(keys)} 天 → {len(s)} 小时")


def spot_1h(workers, start="2023-01"):
    for u in EOH_UNDERLYINGS:
        dest = OUT / "spot1h" / f"{u}.csv.gz"
        if dest.exists():
            continue
        keys = [k for k in keys_under(f"data/spot/monthly/klines/{u}/1h/") if k[-11:-4] >= start]
        frames = pool([lambda k=k: read_zip(_get(f"{BUCKET}/{k}").content) for k in keys],
                      workers, f"1h {u}")
        last = keys[-1][-11:-4]
        dkeys = [k for k in keys_under(f"data/spot/daily/klines/{u}/1h/") if k[-14:-7] > last]
        frames += pool([lambda k=k: read_zip(_get(f"{BUCKET}/{k}").content) for k in dkeys],
                       workers, f"1h daily {u}")
        df = pd.concat(frames, ignore_index=True)
        ts = pd.to_numeric(df["open_time"])
        ts = ts.where(ts < 1e14, ts // 1000)
        df["time"] = pd.to_datetime(ts, unit="ms")
        df = df[["time", "open", "high", "low", "close"]].drop_duplicates("time").sort_values("time")
        dest.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(dest, index=False, compression="gzip")
        print(f"1h {u}: {len(df)} 根")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=16)
    args = ap.parse_args()
    eoh(args.workers)
    spot_1h(args.workers)
    bvol(args.workers)
    print("完成")


if __name__ == "__main__":
    main()
