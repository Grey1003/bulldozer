"""单股全历史逐日诊断，复用正式选股引擎。"""

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from src.config.settings import Settings
from src.data.storage import DataStore, write_json
from src.reporting.local_html import write_local_page
from src.reporting.rule_details import rebound_details, rule_details
from src.strategy.screening import diagnose_stock


def resolve_stock(query: str, instruments: pd.DataFrame) -> pd.Series:
    """精确匹配中文名或股票代码；名称模糊匹配不唯一时列出候选。"""
    query = query.strip()
    code = query.upper().replace(".SH", ".XSHG").replace(".SZ", ".XSHE")
    exact = (instruments["symbol"] == query) | (
        instruments["order_book_id"] == query.upper()
    )
    exact |= instruments["order_book_id"] == code
    if query.isdigit():
        exact |= instruments["order_book_id"].str.split(".").str[0] == query.zfill(6)
    matches = instruments.loc[exact]
    if matches.empty:
        matches = instruments.loc[
            instruments["symbol"].str.contains(query, regex=False)
        ]
    if len(matches) != 1:
        choices = matches[["order_book_id", "symbol"]].head(12).to_dict("records")
        raise ValueError(f"请输入唯一的中文名或代码，共匹配{len(matches)}只：{choices}")
    return matches.iloc[0]


def build_stock_payload(
    bars: pd.DataFrame,
    factors: pd.DataFrame,
    instrument: pd.Series,
    calendar: pd.Series,
    settings: Settings,
) -> dict:
    """逐交易日截断后诊断；浏览器只展示预计算的同源规则结果。"""
    diagnoses = {}
    for date in calendar:
        recent_dates = calendar.loc[calendar <= date].tail(settings.history_bars)
        window = bars.loc[bars["date"].between(recent_dates.iloc[0], date)]
        row, _ = diagnose_stock(
            instrument, window, factors, date, calendar, settings.strategy
        )
        diagnoses[str(date.date())] = {
            "selected": bool(row["selected"]),
            "eligible": bool(row["eligible"]),
            "reason": row["failed_rules"],
            "details": rule_details(row, settings.strategy),
            "annotations": (
                [rebound_details(row, settings.strategy)] if row["eligible"] else []
            ),
        }
    prices = pd.merge_asof(
        bars.sort_values("date"),
        factors[["ex_date", "ex_cum_factor"]].sort_values("ex_date"),
        left_on="date",
        right_on="ex_date",
        direction="backward",
    )
    prices["factor"] = prices["ex_cum_factor"].fillna(1.0)
    prices["daily_return"] = prices["close"] / prices["prev_close"] - 1
    for field in ["open", "high", "low", "close"]:
        prices[field] = prices[field] * prices["factor"]
    for period in [5, 10, 20, 60]:
        prices[f"ma{period}"] = prices["close"].rolling(period).mean()
    prices["date"] = prices["date"].dt.strftime("%Y-%m-%d")
    fields = [
        "date",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "factor",
        "daily_return",
        "ma5",
        "ma10",
        "ma20",
        "ma60",
    ]
    records = json.loads(prices[fields].to_json(orient="records", double_precision=12))
    return {
        "code": instrument["order_book_id"],
        "name": instrument["symbol"],
        "calendar": calendar.dt.strftime("%Y-%m-%d").tolist(),
        "prices": records,
        "diagnoses": diagnoses,
        "parameters": settings.to_dict(),
    }


def export_stock_inspection(
    settings: Settings, query: str, date: str | None = None
) -> Path:
    """读取本地单股缓存并导出可选择日期的离线复核HTML。"""
    store = DataStore(Path(settings.data.root))
    calendar = pd.read_parquet(store.root / "calendar.parquet")["date"]
    instrument = resolve_stock(
        query, pd.read_parquet(store.root / "instruments.parquet")
    )
    bars, factors = store.read_stock(instrument["order_book_id"])
    payload = build_stock_payload(bars, factors, instrument, calendar, settings)
    payload["initial_date"] = str(
        store.resolve_date(date or store.read_metadata()["end_date"]).date()
    )
    stamp = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d_%H%M%S_%f")
    root = (
        Path(settings.report.root)
        / "stock_inspection"
        / f"{instrument['order_book_id']}_{stamp}"
    )
    body = """<p>输入日期表示该日收盘后；非交易日映射到此前交易日。图和结果止于所选日期。</p>
<p class="notice">二波反弹仅提示，不参与筛选；新增近期最低价触及或跌破MA20即淘汰。
实际窗口及涨幅门槛见下表。本页是参数快照，改参数后需重新生成。</p>
<section class="toolbar"><button id="previous">上一交易日</button>
<input id="date" type="date"><button id="next">下一交易日</button>
<select id="boundary"><option value="close_distance">收盘上偏离8%</option>
<option value="touch_ma5">低点贴线±3.5%</option><option value="support">收盘破线-2%</option>
<option value="no_acceleration">末3日加速</option>
<option value="no_touch_ma20">最低价与MA20</option></select></section>
<section><h2 id="status"></h2><p id="reason"></p><p id="effective-date"></p></section>
<div id="chart"></div><section><h2>逐项条件</h2><table><thead><tr>
<th>条件</th><th>实际值</th><th>门槛</th><th>结果</th></tr></thead>
<tbody id="rules"></tbody></table></section>
<section><h2>形态提示（不参与筛选）</h2><p id="annotations"></p></section>
<section><h2>最近8日贴线与破线明细</h2><table><thead><tr><th>日期</th><th>收盘距MA5</th>
<th>低点距MA5</th><th>低点贴线</th><th>收盘上偏离</th><th>收盘支撑</th></tr></thead>
<tbody id="daily"></tbody></table></section>"""
    write_local_page(
        root / "index.html",
        f"单股推土机条件复核 · {instrument['symbol']} "
        f"{instrument['order_book_id']}",
        body,
        payload,
        "stock_inspector.js",
    )
    write_json(root / "parameters.json", settings.to_dict())
    return root / "index.html"
