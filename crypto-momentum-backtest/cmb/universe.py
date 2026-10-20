"""交易标的过滤与数据面板构建。"""
from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pandas as pd

# 稳定币、法币、锚定资产(黄金)、包装币等不应参与动量排名的标的
STABLE_OR_PEGGED = {
    "USDC", "BUSD", "TUSD", "USDP", "PAX", "DAI", "FDUSD", "USDE", "USDS", "USDSB",
    "SUSD", "UST", "USTC", "PYUSD", "USD1", "RLUSD", "BFUSD", "XUSD", "EURI", "AEUR",
    "EUR", "GBP", "AUD", "BRL", "TRY", "RUB", "UAH", "NGN", "BIDR", "IDRT", "BVND",
    "PAXG", "XAUT", "WBTC", "WBETH", "BETH", "USDQ", "USDT", "FRAX", "LUSD", "GUSD",
}
LEVERAGED_RE = re.compile(r"^(.+(UP|DOWN)|.*BULL|.*BEAR)$")
LEVERAGED_UNDERLYING = {
    "BTC", "ETH", "BNB", "ADA", "XRP", "DOT", "LINK", "TRX", "XTZ", "EOS", "LTC",
    "BCH", "FIL", "YFI", "SUSHI", "UNI", "AAVE", "SXP", "XLM", "1INCH",
}


# 2026 年币安上线的“传统金融”永续合约(股票、ETF、大宗商品、外汇、未上市公司等),不是加密资产。
# 名单按合约名人工整理,只用于永续合约做空实验。
TRADFI_PERPS = set("""
AAOI AAPL ACN ADBE AGPU ALAB AMAT AMC AMD AMZN ANET ANTHROPIC APLD APP ARM ASML ASTS AVGO AXTI
BABA BE BILL BITO BMNR BNC BRKB BWET BYD CAT CBRS CIEN CL COHR COIN COPPER COST CRCL CRDO CRM
CRML CRWD CRWV CSCO CSOPSAMSUNG2L CSOPSKHYNIX2L CVNA CXMT CYPH DDOG DELL DIS DJT DKNG DRAM EBAY
EWJ EWT EWY EWZ FLEX FLNC FWDI GDX GEV GIGADEV GLW GME GOOGL GPRO GS GTLB HANMI HD HIMS HK0625
HK0700 HK0992 HK1810 HOOD HPE HUT HYUNDAI IBM INTC INTW IONQ IREN IWM JPM KLAC KO KODEX200 KORU
KUAISHOU LGELECTRONICS LITE LLY LRCX MARA MDB MEITUAN META MINIMAX MP MRK MRNA MRVL MSFT MSTR MU
MUU NATGAS NAVER NBIS NET NFLX NKE NOK NOW NVDA NVDL NVO OKLO ONDS OPENAI ORCL OURA PANW PATH PDD
PLTR POPMART PYPL QCOM QQQ RDDT RIVN RKLB RUM SAMSUNG SAMSUNGEM SHOP SKDD SKHY SKHYNIX SKUU SLX
SMCI SMH SNDK SNOW SOFI SONY SOXL SOXS SPCX SPY SQQQ STRC TBT TEAM TEM TENCENT TER TMF TQQQ TSLA
TSLL TSM TTWO TWST TXN TZA UBER UNH UNITREE URNM USAR USDBRL UVXY V VRT VST WDC WMT XAG XAU XBI
XLE XOM XPD XPT ZHIPU ZHONGJI ZM ZS
""".split())


def is_leveraged(base: str) -> bool:
    if base.endswith("BULL") or base.endswith("BEAR"):
        return True
    for suf in ("UP", "DOWN"):
        if base.endswith(suf) and base[: -len(suf)] in LEVERAGED_UNDERLYING:
            return True
    return False


def is_excluded_symbol(symbol: str) -> bool:
    base = symbol[:-4] if symbol.endswith("USDT") else symbol
    return base in STABLE_OR_PEGGED or is_leveraged(base)


def looks_pegged(close: pd.Series) -> bool:
    """数据层面的兜底:价格长期贴着 1 美元且几乎不波动 → 视为稳定币。"""
    c = close.dropna()
    if len(c) < 30:
        return False
    in_band = ((c > 0.97) & (c < 1.03)).mean()
    vol = np.log(c).diff().abs().median()
    return in_band > 0.9 and vol < 0.002


def looks_tokenized_stock(symbol: str, df: pd.DataFrame) -> bool:
    """2026 年中起币安上线了一批代币化美股(AAPLB、NVDAB、CRCLB…,基础资产名以 B 结尾)。
    它们不是加密资产,不参与排名。按“以 B 结尾 + 2026-06 之后才有数据”识别。"""
    base = symbol[:-4]
    return base.endswith("B") and len(base) > 2 and df.index.min() >= pd.Timestamp("2026-06-01")


def load_panel(raw_dir: str | Path, exclude: set[str] = frozenset()) -> dict[str, pd.DataFrame]:
    """读取全部 csv,返回 {字段: 日期×币种 的宽表}。exclude: 额外剔除的基础资产名。"""
    raw_dir = Path(raw_dir)
    frames = {}
    for f in sorted(raw_dir.glob("*.csv.gz")):
        sym = f.name.split(".")[0]
        if is_excluded_symbol(sym) or sym[:-4] in exclude:
            continue
        df = pd.read_csv(f, parse_dates=["date"]).set_index("date")
        df = df[~df.index.duplicated()].sort_index()
        if df.empty or looks_pegged(df["close"]) or looks_tokenized_stock(sym, df):
            continue
        frames[sym] = df
    if not frames:
        raise SystemExit(f"{raw_dir} 下没有数据,请先运行 python download_data.py")
    fields = ["open", "high", "low", "close", "volume", "quote_volume"]
    full = pd.concat(frames, axis=1)  # MultiIndex 列 (symbol, field)
    full = full.sort_index()
    idx = pd.date_range(full.index.min(), full.index.max(), freq="D")
    full = full.reindex(idx)
    panel = {fld: full.xs(fld, axis=1, level=1).astype(float) for fld in fields}
    # 偶发的停牌/维护日:不前向填充价格,保留 NaN,由回测引擎处理
    return panel
