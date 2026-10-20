# crypto-momentum-backtest

把 Qullamaggie 的「动量 + 横盘 + 突破」交易方法放到币安 USDT 现货上做**历史回测**,
检验它有没有正的期望值。

- **只做模拟**:只读取币安公开的历史 K 线文件(data.binance.vision),
  不连接任何账户、不下任何订单、不需要 API 密钥。
- 结果报告:[`results/REPORT.md`](results/REPORT.md)

## 运行

需要 Python 3.10+。

```bash
cd crypto-momentum-backtest
pip install -r requirements.txt

# 1) 下载数据(约 680 个 USDT 交易对的日线,含已下架币,~40 MB,10–20 分钟)
python download_data.py            # 默认从 2019-06 开始,已下载的会跳过;--refresh 强制重下

# 2) 跑全部实验并生成报告(约 10 分钟)
python run_backtest.py

# 只跑两组主结果 / 自定义一次回测(输出 results/trades_custom.csv 和 equity_custom.png)
python run_backtest.py --quick
python run_backtest.py --top-pct 0.05 --ma-exit 10 --risk 0.01 --stop atr --stop-atr-mult 2
python run_backtest.py --selection top_gainers --stop atr
```

如果 `data.binance.vision` 打不开,脚本直接访问它背后的 S3 地址
`s3-ap-northeast-1.amazonaws.com/data.binance.vision`(同一份公开数据)。

## 输出(`results/`)

| 文件 | 内容 |
|---|---|
| `REPORT.md` | 完整报告:指标、回撤持续时间 / 恢复时间 / 最长连亏、蒙特卡洛回撤分布、分年份、最好/最差 10 笔、选币×止损对比、条件消融、敏感性、K 线图、样本外、小资金分析、中文总结 |
| `equity_curve.png` | 10,000 USDT 资金曲线(严格版 / 放宽版)vs BTC 买入持有 + 回撤 |
| `equity_oos.png` | 样本外(2024 至今)资金曲线 |
| `trade_charts/*.png` | 单笔交易 K 线图:横盘区间、突破日、买入、止损、部分止盈、离场 |
| `mc_drawdown.png`, `monte_carlo_drawdowns.csv` | 交易顺序打乱 1000 次的最大回撤分布(刹车线参考) |
| `trades_strict.csv` / `trades_relaxed.csv` | 全部交易明细 |
| `equity_daily.csv` | 每日权益 |
| `selection_x_stop.csv` | 选币方式(原方案 / 24h 涨幅榜前 10 / 24h 成交额榜前 10)× 止损方式(低点 / 1ATR / 2ATR) |
| `ablation_*.csv` | 逐个去掉横盘/买入条件后的结果 |
| `sensitivity.csv`, `bottleneck_grid.csv` | 参数敏感性 |
| `optimization_grid.csv` | 样本外检验用的参数网格,样本内/外指标 |

## 代码结构

```
download_data.py     下载 + 剔除稳定币/杠杆代币/法币
cmb/universe.py      标的过滤规则、读取数据为 日期×币种 宽表
cmb/engine.py        参数 (Params)、向量化信号 (SignalBuilder)、逐日撮合 (run_backtest)
cmb/metrics.py       收益、年化、回撤及持续时间、夏普、胜率、R 倍数、止损比例、连亏、蒙特卡洛、分年份
cmb/charts.py        资金曲线、单笔交易 K 线图
run_backtest.py      所有实验 + 报告
```

## 规则与参数

全部参数在 `cmb/engine.py` 的 `Params` 里,改默认值或用 `Params().with_(...)` 覆盖。

| 规则 | 实现(默认值) | 参数 |
|---|---|---|
| 流动性 | 过去 30 天日均成交额 ≥ 500 万 USDT | `min_avg_quote_volume`, `liquidity_window` |
| 选币方式 | `qulla`(下面的横盘突破)/ `top_gainers`(24h 涨幅榜前 10)/ `top_volume`(24h 成交额榜前 10) | `selection`, `top_n` |
| 横盘 1 之前强势 | 30 天或 90 天涨幅排名前 2% | `top_pct`, `mom_lookbacks`, `rank_mode` |
| 横盘 2a 波动收窄 | 最近 5–15 天 (最高-最低)/当前收盘 < 20% | `cons_min`, `cons_max`, `cons_max_range` |
| 横盘 2b ATR 收缩 | 横盘期 ATR% < 上涨阶段(横盘前 20 天)ATR% | `runup_window`, `atr_contract_ratio` |
| 横盘 3 成交量萎缩 | 横盘均量 < 上涨阶段均量 × 70% | `vol_dryup_ratio` |
| 横盘 4 贴近高点 | 收盘 ≥ 60 日最高 × 90% | `near_high_window`, `near_high_pct` |
| 横盘 5 均线向上 | 收盘 > MA10、MA20,且 MA20 高于 5 天前 | `ma_fast`, `ma_slow`, `ma_rising_lookback` |
| 买入 | 收盘 > 横盘最高价 且 成交量 ≥ 横盘均量 × 1.5 → 次日开盘买入 | `breakout_vol_mult` |
| 消融 | 关闭任意条件:`disabled=("vol_dryup",)` 等,名称见 `CONDITIONS` | `disabled` |
| 止损 | `low`:突破日最低价,距离 > 1×ATR(14) 放弃;`atr`:成交价 − k×ATR | `stop_mode`, `atr_mult`, `stop_atr_mult` |
| 仓位 | 账户 × 0.5% ÷ 止损距离,上限 25% 账户,不超过现金 | `risk_per_trade`, `max_position_pct` |
| 止盈 | 第 3 根日线收盘卖 1/3、止损移到成本;之后收盘破 MA20 次日开盘全卖 | `partial_days`, `partial_frac`, `ma_exit` |
| 成本 | 每次买卖 0.1% 手续费 + 0.05% 滑点 | `fee`, `slippage` |
| 小资金 | 最小下单额(0 = 关闭) | `min_notional`, `min_notional_mode` |

报告里有两个版本:**严格版** = 上表默认值;**放宽版** = 强势前 10%、横盘区间 < 40%、1×ATR 止损
(严格版 6.8 年只有个位数交易,无法统计,原因见报告第 2 节)。

实现细节 / 假设:

- 日线按 UTC 00:00 切分(币安原始数据即如此);1/3 个月 = 30/90 个自然日。
- 不用未来数据:横盘条件用突破前一天(t−1)收盘为止的数据判断;t 日收盘确认突破,t+1 日开盘成交。
- 在 5–15 天内取所有条件都满足的最长横盘区间。
- 止损盘中触发,按止损价成交;若开盘直接跳空到止损下方,按开盘价成交(亏损会超过 1R)。
- 同一天多笔信号按动量强弱排序,依次开仓直到现金用完;同一币种同时只持有一笔。
- 下架币:持仓期间数据结束,按最后一根收盘价平仓。
- 剔除:稳定币、法币(EUR/GBP/…)、PAXG/XAUT、WBTC/WBETH、杠杆代币;价格长期贴 1 美元的也剔除;
  2026 年上线的代币化美股(AAPLB 等)剔除。
- 夏普 = 日收益均值 / 标准差 × √365,无风险利率按 0。

## 局限

- 日线回测看不到盘中先触发止损还是先创新高;滑点用固定比例,极端行情下实际更差。
- 2019-06 之前就下架的币不在数据里(对 2020 年之后的交易没有影响)。
- 没有模拟 LOT_SIZE 步长、交易所维护停机等细节。
- 历史表现不代表未来;本项目仅用于研究,不构成投资建议。
