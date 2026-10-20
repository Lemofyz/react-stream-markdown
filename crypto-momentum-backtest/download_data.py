"""下载币安公开历史日线 K 线(data.binance.vision),包含已下架交易对。

只访问公开的静态数据仓库,不需要 API 密钥,不连接任何账户。

用法:
    python download_data.py                # 默认 2019-06 起(给 6 个月动量留预热期)
    python download_data.py --start 2019-06 --workers 16
"""
from __future__ import annotations

import argparse
import io
import re
import time
import xml.etree.ElementTree as ET
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd
import requests

from cmb.universe import is_excluded_symbol

# data.binance.vision 是一个 S3 bucket;直接走 S3 端点可以列目录
BUCKET = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"
NS = {"s3": "http://s3.amazonaws.com/doc/2006-03-01/"}
COLS = ["open_time", "open", "high", "low", "close", "volume", "close_time",
        "quote_volume", "trades", "taker_base", "taker_quote", "ignore"]

session = requests.Session()


def _get(url: str, params: dict | None = None, retries: int = 5) -> requests.Response:
    for i in range(retries):
        try:
            r = session.get(url, params=params, timeout=30)
            if r.status_code == 200:
                return r
            if r.status_code == 404:
                return r
        except requests.RequestException:
            pass
        time.sleep(2 ** i)
    raise RuntimeError(f"failed: {url} {params}")


def list_prefix(prefix: str, delimiter: str | None = "/") -> tuple[list[str], list[str]]:
    """返回 (子目录列表, 文件 key 列表),自动翻页。"""
    dirs, keys, marker = [], [], ""
    while True:
        params = {"prefix": prefix, "marker": marker}
        if delimiter:
            params["delimiter"] = delimiter
        root = ET.fromstring(_get(BUCKET, params).content)
        dirs += [e.text for e in root.findall("s3:CommonPrefixes/s3:Prefix", NS)]
        keys += [e.text for e in root.findall("s3:Contents/s3:Key", NS)]
        if root.findtext("s3:IsTruncated", namespaces=NS) != "true":
            return dirs, keys
        marker = root.findtext("s3:NextMarker", namespaces=NS) or (keys or dirs)[-1]


def read_zip(content: bytes) -> pd.DataFrame:
    with zipfile.ZipFile(io.BytesIO(content)) as z:
        with z.open(z.namelist()[0]) as f:
            df = pd.read_csv(f, header=None)
    if not str(df.iloc[0, 0]).isdigit():  # 个别文件带表头
        df = df.iloc[1:]
    df = df.iloc[:, :12]
    df.columns = COLS
    return df


def fetch_symbol(symbol: str, start: str, out_dir: Path) -> tuple[str, int]:
    out = out_dir / f"{symbol}.csv.gz"
    _, keys = list_prefix(f"data/spot/monthly/klines/{symbol}/1d/", delimiter=None)
    monthly = sorted(k for k in keys if k.endswith(".zip") and k[-11:-4] >= start)
    frames = [read_zip(_get(f"{BUCKET}/{k}").content) for k in monthly]
    last_month = monthly[-1][-11:-4] if monthly else start
    # 本月(月度文件尚未生成)用日度文件补齐
    _, dkeys = list_prefix(f"data/spot/daily/klines/{symbol}/1d/", delimiter=None)
    daily = sorted(k for k in dkeys if k.endswith(".zip") and k[-14:-7] > last_month)
    frames += [read_zip(_get(f"{BUCKET}/{k}").content) for k in daily]
    if not frames:
        return symbol, 0
    df = pd.concat(frames, ignore_index=True)
    ts = pd.to_numeric(df["open_time"])
    # 2025 年起币安现货数据时间戳改为微秒
    ts = ts.where(ts < 1e14, ts // 1000)
    df["date"] = pd.to_datetime(ts, unit="ms", utc=True).dt.tz_localize(None).dt.normalize()
    df = df[["date", "open", "high", "low", "close", "volume", "quote_volume", "trades"]]
    df = df.drop_duplicates("date").sort_values("date")
    df.to_csv(out, index=False, compression="gzip")
    return symbol, len(df)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2019-06", help="起始月份 YYYY-MM")
    ap.add_argument("--out", default="data/raw")
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--refresh", action="store_true", help="重新下载已存在的文件")
    args = ap.parse_args()
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    dirs, _ = list_prefix("data/spot/monthly/klines/")
    symbols = sorted(d.split("/")[-2] for d in dirs)
    usdt = [s for s in symbols if s.endswith("USDT")]
    keep = [s for s in usdt if not is_excluded_symbol(s)]
    print(f"全部现货交易对 {len(symbols)},USDT 交易对 {len(usdt)},"
          f"剔除稳定币/杠杆代币/法币后 {len(keep)}")
    (out_dir.parent / "excluded_symbols.txt").write_text(
        "\n".join(sorted(set(usdt) - set(keep))) + "\n")

    todo = [s for s in keep if args.refresh or not (out_dir / f"{s}.csv.gz").exists()]
    done = 0
    with ThreadPoolExecutor(args.workers) as ex:
        futs = {ex.submit(fetch_symbol, s, args.start, out_dir): s for s in todo}
        for fut in as_completed(futs):
            done += 1
            try:
                sym, n = fut.result()
            except Exception as e:  # noqa: BLE001
                print(f"[{done}/{len(todo)}] {futs[fut]} 失败: {e}")
                continue
            if done % 25 == 0 or n == 0:
                print(f"[{done}/{len(todo)}] {sym}: {n} 根日线")
    print("完成")


if __name__ == "__main__":
    main()
