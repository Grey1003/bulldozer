# 数据口径

`data/raw/` 是独立数据快照，包含：

- `download_plan.json`：开始、请求终点和批次大小，防止续传混入其他区间。
- `metadata.json`：全部批次完成后才生成，记录实际交易日终点、行数和下载时间。
- `instruments.parquet`：区间内曾上市的股票，保留退市标的，续传沿用相同顺序。
- `calendar.parquet`：沪深交易日历。
- `daily/0000.parquet` 等：每100只股票一个批次，保留原始日线。
- `factors/0000.parquet` 等：相应批次自上市以来、截至下载终点的全部除权事件。

原始行情字段：`order_book_id`, `date`, `open`, `high`, `low`, `close`,
`volume`, `total_turnover`, `prev_close`, `limit_up`, `limit_down`, `is_st`。
唯一键为 `(order_book_id, date)`，价格不复权、成交量不复权，ST为当日历史状态。
`prev_close` 使用供应商当日涨跌幅基准，除权日不简单用前一日原始收盘价替代。

因子字段为 `order_book_id`, `ex_date`, `ex_cum_factor`。
除权因子没有事件时该股票可能没有记录；由于下载从上市起的完整事件，
首个事件前累计因子确定为1。不得把未来首个事件反向填充至历史价格。

研究日T的前复权价为：

```text
adjusted_price[t; T] = raw_price[t] * cumulative_factor[t] / cumulative_factor[T]
t <= T，并且除权事件生效日 <= T
```

仅对OHLC进行上述处理。成交量维持原始值并明确标注，不作为形态筛选阈值。
涨停与振幅均在原始价格口径下计算；均线与贴线距离均在时点前复权价格下计算。
不进行 `bfill`、居中滚动、负向shift或未来极值计算。

默认下载终点最多为昨日，避免把当天未完成日线写入缓存。
指定终点如为非交易日，元数据终点取区间最后一个交易日。
缓存只含近五年，不附加窗口外预热数据，因此最初约120个交易日不足以筛选。
历史停牌不压缩交易日轴；近期8个交易日有停牌或日线缺失的股票不能入选。

股票代码列表覆盖下载区间存在过的股票，不用当日存续股票列表反推历史。
股票名为下载时版本，只用于展示；不以最新名称或最新状态判断历史ST。
上市、退市日期按研究日期比较。该实现仍使用供应商当前返回的历史快照，
不承诺还原供应商后来修订前的数据；研究时应保留缓存及参数快照。

米筐接口依据官方文档核对：
[跨品种通用API](https://www.ricequant.com/doc/rqdata/python/generic-api)、
[A股与复权因子API](https://www.ricequant.com/doc/rqdata/python/stock-mod)。

读取凭据时仅提取 `rq.init(...)` 的字面量参数，支持现有URI文本格式。
源码、报告、参数快照不写入凭据。
