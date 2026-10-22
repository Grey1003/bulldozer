# 推土机选股研究工具

2026-10-22更新：默认缓存 `data/raw_20261020` 已覆盖更新，行情截至2026-10-21。
目录名称保留不变，实际日期以其中的 `metadata.json` 为准。
原 `data/raw` 缓存仍保留。输入日期不能晚于当前缓存终点；更新终点时，
先使用新数据目录完成下载，再切换默认配置。

使用米筐下载近五年日频数据，输入一个日期，生成推土机候选股、K线图和
全市场逐项诊断。现阶段专注形态筛选与人工审美校准，不包含回测与交易执行。

## 开始使用

要求 Python 3.12+。本机已使用 Python 3.12.3 实际运行。
入口脚本可从 VS Code 或任意目录使用绝对路径启动；默认配置、数据、凭据和
报告中的相对路径统一以项目根目录为基准，无须切换终端工作目录。
下方简写命令在项目根目录执行。

```powershell
python -m pip install -r requirements.txt
```

现有 `米筐设置.txt` 可直接使用，程序仅解析其中 `rq.init(...)` 的字符串参数，
不执行文本内容、不输出凭据；该文件已加入 `.gitignore`。
也可通过 `RQDATAC_URI` 或 `RQDATAC_USERNAME` / `RQDATAC_PASSWORD` 环境变量配置。

已下载的数据不需要再次下载。首次下载或新建数据集：

```powershell
python main.py download --end 2026-10-19
```

数据下载区间是终点往前五个自然年，涵盖区间内存在过的股票，包括退市股票。
同一区间再次运行支持按批次续传；变更下载终点或批次大小时，请在配置中设置
新的 `data.root`（例如 `data/raw_20261020`），保存不同版本，避免覆盖混用。
不带终点的便捷下载入口为 `python download_data.py`，默认终点为上海时间昨日。
如果本地缓存已有不同终点，需要先配置新的数据目录。

按指定日期选股：

```powershell
python main.py screen --date 2026-10-19
```

也可运行以下脚本，然后直接输入日期：

```powershell
python select_stocks.py
```

输入日期表示**该日收盘后进行选股**。例如输入 `2026-10-19`，使用19日及之前
的日线生成候选，供下一交易日人工观察。若要研究19日开盘前可见的候选，
应输入其前一交易日。周末与假期映射到此前交易日；缓存外日期明确报错。
缓存开头的日期因缺少足够均线预热历史，通常没有入选股票。

## 看图和调参

每次运行生成独立的 `output/日期_运行时间/`，打开其中 `index.html`：

- 入选股票：全部生成 PNG 与可缩放的离线交互 K线。
- 未入选复核样本：按未通过规则数量排序，默认展示前12只，与入选股明确区分。
- `selected.csv`：入选清单及各项指标。
- `diagnostics.csv`：全市场资格、指标、每条规则是否通过与淘汰原因。
- `parameters.json`、`run.json`：完整参数快照、数据版本和各规则淘汰数量。

PNG 绘制 MA5/10/20/60，浅黄色区域为最近8日形态检查窗口，红色三角表示
收盘涨停日；成交量保留原始口径，**成交量大小不作为选股条件**。
报告股票名称是下载时的名称，仅作显示，历史 ST 判断来自历史标记。
交互图使用同目录 `plotly.min.js`，本地打开即可，无须联网。

修改 [configs/default.json](configs/default.json)，再执行选股命令即可比较。
比例使用小数，例如 `0.05` 表示5%；窗口以交易日计，`min_listing_days` 为自然日。
建议一次只改一组参数，结合报告中的规则淘汰数和未入选样本观察影响。

常用参数：

| 参数 | 默认 | 调整意义 |
| --- | --- | --- |
| `trend_days` | 8 | 最近若干交易日沿线爬坡 |
| `min_ma5_up_ratio` | 0.875 | 最近8日允许1日MA5不升；设1要求全部上升 |
| `max_close_ma5_distance` | 0.08 | 检查窗口内收盘距MA5的最大上偏离 |
| `max_low_ma5_distance` | 0.035 | K线最低价相对MA5的绝对偏离上限 |
| `min_touch_ratio` | 0.75 | 至少75%的检查日低点接近MA5 |
| `max_last_ma5_distance` | 0.05 | 选股日收盘不能离MA5过远 |
| `max_last_return` | 0.065 | 选股日涨幅上限，排除末日大涨 |
| `min_mean_amplitude` / `max_mean_amplitude` | 0.02 / 0.09 | 排除过闷或过于剧烈 |
| `activity_days` / `min_limit_up_count` | 15 / 1 | 近15日应至少有一次收盘涨停 |
| `max_consecutive_limit_up` | 1 | 近15日不允许出现2连板 |
| `rebound_min_drawdown` | 0.18 | 旧高后回撤达到18%，视为潜在二波 |
| `rebound_peak_tolerance` | 0.08 | 潜在二波未超过旧高8%时排除 |
| `report.near_miss_count` | 12 | 展示多少只未入选样本，不影响选股 |
| `report.chart_bars` | 120 | 每张图展示的历史交易日数量 |

原文没有规定这些精确阈值，默认值是明确可改的工程起点。
“二波”“长颈鹿”“漂亮”只能做近似识别，需要你按图继续校准，
程序不会为凑够股票数量自动放宽参数。

## 模块结构

```text
推土机/
├── main.py                     # 标准命令行
├── download_data.py            # 便捷下载入口
├── select_stocks.py            # 交互输入日期
├── configs/default.json        # 集中参数
├── src/
│   ├── cli.py                  # 流程编排
│   ├── config/settings.py      # 配置数据类
│   ├── data/
│   │   ├── ricequant.py        # 米筐接口和凭据读取
│   │   ├── download.py         # 下载流程、续传与清单
│   │   └── storage.py          # Parquet与时点读取
│   ├── strategy/
│   │   ├── indicators.py       # 复权、均线、回撤等基础计算
│   │   ├── rules.py            # 形态指标与逐项规则
│   │   └── screening.py        # 日期资格、全市场筛选
│   └── reporting/
│       ├── charts.py           # PNG及交互K线
│       └── report.py           # HTML、CSV与参数快照
├── data/raw/                   # 本地行情，已忽略Git
├── output/                     # 每次独立报告，已忽略Git
├── docs/                       # 策略映射、数据约定、架构规范
└── tests/                      # 日期边界、复权及典型形态测试
```

不增加空的数据库、异常、交易或回测抽象。函数职责单一，配置使用数据类，
类型注解与精简文档字符串齐全，文件低于500行，格式宽度88字符。
更多命名与扩展规则见 [docs/architecture.md](docs/architecture.md)。

## 日期与数据对齐

- 先截断行情与除权事件，再计算指标；滚动窗口只向历史取值。
- 原始价用于历史涨跌幅、涨停与振幅；研究日前复权价用于形态和K线。
- 只用研究日已生效的累计因子，首次除权前基准为1，不反向填充未来因子。
- 使用实际历史涨停价识别收盘涨停，避免用固定10%近似代替历史价格。
- 近期停牌或缺失日线不参与候选，不用未来记录补齐；历史ST按当日标记判断。
- 默认只筛沪深主板，排除ST及上市不足120自然日的股票。
- `next_ma5_estimate` 仅假设次日价格等于当前收盘价，公式为
  `(最近4个收盘价之和 + 当前收盘价) / 5`；不是次日真实均线或成交价。
- 供应商可能回修历史数据，本地快照能固定本次研究输入，不能声称已经恢复
  每个历史时刻米筐当时数据库中的版本。

完整口径见 [docs/data_contract.md](docs/data_contract.md)，
原文到规则的映射见 [docs/strategy_mapping.md](docs/strategy_mapping.md)。

## 验证

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest -q
python -m black --check src tests main.py download_data.py select_stocks.py
python -m isort --check-only src tests main.py download_data.py select_stocks.py
python -m flake8 src tests main.py download_data.py select_stocks.py
```

测试覆盖未来行情/因子不变性、除权生效日期、无除权基准、周末、缓存边界、
历史ST，以及末日大涨、偏离均线、低振幅、无涨停、连板、加速和二波代理规则。
代码已进行自动测试和自查，人工图形审阅由使用者在本地报告中完成。


## GitHub仓库中的两个入口

本说明对应米筐模块化选股工具 `select_stocks.py`。仓库原有的
BaoStock程序 `bulldozer.py`、`config.json`、`quickstart.json` 及说明文档
继续保留；其使用方式见 `README_中文.md` 与 `VALIDATION.md`。
两套入口独立运行，行情缓存与本地账号不通过Git同步。
