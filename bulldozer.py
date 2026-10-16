"""推土机：BaoStock 日线 + 无行情磁盘缓存的本地回测。Python 3.10+。

选股只读截至昨日的数据；今日 OHLC 仅由撮合器模拟预先确定的订单。
不尝试用日线恢复集合竞价、盘中高点、9:40 截止或逐笔排队。
"""
from __future__ import annotations

import argparse
import copy
import gc
import hashlib
import itertools
import json
import logging
import math
import socket
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP, ROUND_FLOOR, ROUND_CEILING
from importlib.metadata import version, PackageNotFoundError
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

LOG = logging.getLogger("bulldozer")
PRICE_COLS = ["open", "high", "low", "close"]
DEFAULT_FILE = Path(__file__).with_name("config.json")


def deep_merge(base: dict, extra: dict) -> dict:
    result = copy.deepcopy(base)
    for key, val in extra.items():
        if key not in result:
            raise ValueError(f"未知配置项：{key}")
        if isinstance(val, dict) and isinstance(result[key], dict):
            if key == "grid":
                result[key] = copy.deepcopy(val)
            else:
                result[key] = deep_merge(result[key], val)
        else:
            result[key] = val
    return result


def set_parameter(config: dict, path: str, value: Any) -> None:
    parts = path.split(".")
    obj = config
    for key in parts[:-1]:
        obj = obj[key]
    if parts[-1] not in obj:
        raise ValueError(f"未知参数：{path}")
    obj[parts[-1]] = value


def load_config(path: str | Path | None = None) -> dict:
    cfg = json.loads(DEFAULT_FILE.read_text(encoding="utf-8"))
    if path and Path(path).resolve() != DEFAULT_FILE.resolve():
        cfg = deep_merge(cfg, json.loads(Path(path).read_text(encoding="utf-8")))
    validate_config(cfg)
    return cfg


def validate_config(c: dict) -> None:
    s, e, d = c["strategy"], c["execution"], c["data"]
    if not (1 < s["fast_ma"] < s["middle_ma"] < s["slow_ma"]):
        raise ValueError("均线周期须满足 1 < fast_ma < middle_ma < slow_ma")
    if not (0 <= s["min_touch_days"] <= s["trend_days"] and
            0 <= s["min_rising_days"] <= s["trend_days"]):
        raise ValueError("满足天数不能超过观察天数")
    required = max(s["slow_ma"] + s["slope_days"],
                   s["fast_ma"] + s["trend_days"], s["wave_lookback"],
                   s["limit_lookback"], s["consecutive_lookback"], s["amplitude_days"]) + 2
    if d["warmup_sessions"] < required:
        raise ValueError(f"warmup_sessions 至少为 {required}")
    if d["chunk_months"] < 1 or d["network_timeout_seconds"] <= 0 or d["retries"] < 0:
        raise ValueError("数据分段、超时、重试参数不合法")
    if e["entry_mode"] not in ("limit_touch", "open_only"):
        raise ValueError("entry_mode 只能为 limit_touch 或 open_only")
    if e["exit_mode"] not in ("next_open", "next_close"):
        raise ValueError("exit_mode 只能为 next_open 或 next_close")
    if e["buy_price_mode"] not in ("offset", "position"):
        raise ValueError("buy_price_mode 只能为 offset 或 position")
    if s["wave_mode"] not in ("strict", "near_high"):
        raise ValueError("wave_mode 只能为 strict 或 near_high")
    for key in ("daily_buy_fraction", "single_position_fraction", "max_total_exposure",
                "max_volume_fraction"):
        if not 0 < e[key] <= 1:
            raise ValueError(f"{key} 必须在 (0,1] 内")
    if e["lot_size"] != 100 or e["max_new_positions"] < 1 or e["tick_size"] != 0.01:
        raise ValueError("本版本仅支持普通主板 A 股：100 股买入单位，0.01 元报价单位")
    if not 0 <= e["buy_position"] <= 1 or e["buy_offset"] < 0:
        raise ValueError("买价参数不合法")
    if not 0 <= e["dividend_tax_assumption"] <= 1 or e["limit_penetration_ticks"] < 0:
        raise ValueError("股息税假设或触价参数不合法")
    for key in ("slippage_bps", "commission_rate", "minimum_commission"):
        if e[key] < 0:
            raise ValueError(f"{key} 不能为负")
    if c["research"]["initial_cash"] <= 0:
        raise ValueError("初始资金须为正")
    if e["max_holding_sessions"] is not None and e["max_holding_sessions"] < 1:
        raise ValueError("最大持有天数须为空或正整数")
    prev_end = None
    for name in ("train", "validation", "test"):
        start, end = map(pd.Timestamp, c["periods"][name])
        if start > end or (prev_end is not None and start <= prev_end):
            raise ValueError("训练、验证、测试须按时间排列且不得重叠")
        if start < pd.Timestamp("2015-08-01"):
            raise ValueError("示例费率表仅覆盖 2015-08-01 起；更早历史需扩展制度与费率模型")
        prev_end = end
    for key in ("stamp_tax_schedule", "transfer_fee_schedule"):
        table = e[key]
        dates = [pd.Timestamp(row[0]) for row in table]
        if not table or dates != sorted(dates) or any(row[1] < 0 for row in table):
            raise ValueError(f"{key} 须为按日期升序排列的非负费率表")
        if dates[0] > pd.Timestamp(c["periods"]["train"][0]):
            raise ValueError(f"{key} 未覆盖训练期起点")


def rounded(price: float, tick: float = .01, direction: str = "nearest") -> float:
    mode = {"nearest": ROUND_HALF_UP, "down": ROUND_FLOOR, "up": ROUND_CEILING}[direction]
    units = Decimal(str(price)) / Decimal(str(tick))
    return float(units.quantize(Decimal("1"), rounding=mode) * Decimal(str(tick)))


def main_board(code: str) -> bool:
    return code.startswith(("sh.600", "sh.601", "sh.603", "sh.605", "sz.000", "sz.001", "sz.002", "sz.003"))


def reference_close(frame: pd.DataFrame) -> pd.Series:
    # 官方日线文档：除权日 preclose 是交易所公布的除权除息参考价，
    # 并非昨日未复权实际收盘。它在开盘前可知，不依赖今日收盘反推。
    return frame["preclose"].where(frame["preclose"] > 0)


def enrich_raw(raw: pd.DataFrame, adjusted: pd.DataFrame) -> pd.DataFrame:
    f = raw.copy()
    for col in f.columns:
        f[col] = pd.to_numeric(f[col], errors="coerce")
    adj = adjusted[PRICE_COLS].apply(pd.to_numeric, errors="coerce")
    if not f.index.equals(adj.index):
        raise RuntimeError("原始与复权行情日期不一致，拒绝静默丢失数据")
    for col in PRICE_COLS:
        f["a_" + col] = adj[col]
    f["scale"] = f["a_close"] / f["close"]
    f["reference"] = reference_close(f)
    # 本引擎仅支持成熟主板普通股；ST 旧持仓使用历史 isST 的 5% 规则。
    f["limit_rate"] = np.where(f["isST"].eq(1), .05, .10)
    f["upper"] = [rounded(p * (1+r)) if np.isfinite(p) else np.nan
                  for p, r in zip(f["reference"], f["limit_rate"])]
    f["lower"] = [rounded(p * (1-r)) if np.isfinite(p) else np.nan
                  for p, r in zip(f["reference"], f["limit_rate"])]
    f["limit_up"] = (f["close"] - f["upper"]).abs().lt(.0051) & f["tradestatus"].eq(1)
    f["previous_volume"] = f["volume"].shift(1)
    f["factor_changed"] = f["scale"].div(f["scale"].shift(1)).sub(1).abs().gt(1e-6)
    f["previous_raw_close"] = f["close"].shift(1)
    return f


class BaoStockProvider:
    """仅通过 API 读取内存；没有行情文件、数据库或磁盘缓存代码。"""
    def __init__(self, config: dict):
        self.cfg = config
        self.bs = None
        self.basic = pd.DataFrame()
        self.calendar = pd.DatetimeIndex([])
        self._dividends: dict[tuple[str, int], pd.DataFrame] = {}
        self.actual_data_end: str | None = None

    def __enter__(self):
        import baostock as bs
        self.bs = bs
        socket.setdefaulttimeout(self.cfg["data"]["network_timeout_seconds"])
        self._login()
        return self

    def __exit__(self, *args):
        if self.bs:
            try:
                self.bs.logout()
            except Exception:
                pass

    def _login(self):
        response = self.bs.login()
        if response.error_code != "0":
            raise ConnectionError(f"BaoStock 登录失败：{response.error_code} {response.error_msg}")

    def _query(self, method: str, **kwargs) -> pd.DataFrame:
        last_error = None
        for attempt in range(self.cfg["data"]["retries"] + 1):
            try:
                time.sleep(self.cfg["data"]["request_pause_seconds"])
                rs = getattr(self.bs, method)(**kwargs)
                if rs.error_code != "0":
                    raise ConnectionError(f"{method}: {rs.error_code} {rs.error_msg}")
                rows = []
                while rs.next():
                    rows.append(rs.get_row_data())
                if rs.error_code != "0":
                    raise ConnectionError(f"{method}: 分页读取失败 {rs.error_code} {rs.error_msg}")
                return pd.DataFrame(rows, columns=rs.fields)
            except Exception as exc:
                last_error = exc
                if attempt < self.cfg["data"]["retries"]:
                    LOG.warning("接口失败，重新登录后重试：%s", exc)
                    time.sleep(min(2 ** attempt, 5))
                    self._login()
        raise ConnectionError(f"接口读取失败，不以空数据继续回测：{method} {kwargs}") from last_error

    def prepare(self, start: pd.Timestamp, end: pd.Timestamp) -> None:
        today = pd.Timestamp.now(tz=ZoneInfo("Asia/Shanghai")).normalize().tz_localize(None)
        if end >= today:
            raise ValueError("为避免读取尚未更新的当日日线，结束日期必须早于上海时区的今天")
        first = start - pd.Timedelta(days=max(700, self.cfg["data"]["warmup_sessions"] * 3))
        dates = self._query("query_trade_dates", start_date=str(first.date()), end_date=str(end.date()))
        self.calendar = pd.DatetimeIndex(pd.to_datetime(dates.loc[dates["is_trading_day"].eq("1"), "calendar_date"]))
        self.basic = self._query("query_stock_basic")
        self.basic = self.basic.loc[self.basic["type"].eq("1") & self.basic["code"].map(main_board)].copy()
        # 不按当前 status=1 过滤：保留历史已退市股票，避免显式幸存者偏差。
        for col in ("ipoDate", "outDate"):
            self.basic[col] = pd.to_datetime(self.basic[col], errors="coerce")
        self.basic = self.basic.set_index("code", verify_integrity=True)
        chosen = self.cfg["data"]["symbols"]
        if chosen:
            missing = set(chosen) - set(self.basic.index)
            if missing:
                raise ValueError(f"代码不属于支持的主板股票或数据源未收录：{sorted(missing)}")
            self.basic = self.basic.loc[sorted(set(chosen))]
        # 用指数验证结束日更新状态；不能悄悄把多日缺行情当停牌。
        expected = self.calendar[(self.calendar >= start) & (self.calendar <= end)]
        if len(expected) == 0:
            raise ValueError("选定区间没有交易日")
        probe = self._history("sh.000001", expected[-1], expected[-1], "3", "date,close")
        if probe.empty:
            raise RuntimeError(f"数据源尚未提供 {expected[-1].date()} 的基准日线；请缩短结束日期")
        self.actual_data_end = str(expected[-1].date())
        LOG.info("股票全集 %d 只；最后交易日 %s", len(self.basic), self.actual_data_end)

    def _history(self, code, start, end, flag, fields):
        df = self._query("query_history_k_data_plus", code=code, fields=fields,
                         start_date=str(start.date()), end_date=str(end.date()),
                         frequency="d", adjustflag=flag)
        if df.empty:
            return pd.DataFrame(columns=fields.split(",")[1:], index=pd.DatetimeIndex([]))
        df["date"] = pd.to_datetime(df["date"])
        return df.set_index("date", verify_integrity=True).sort_index()

    def load_chunk(self, start: pd.Timestamp, end: pd.Timestamp) -> dict[str, pd.DataFrame]:
        before = self.calendar[self.calendar < start]
        n = self.cfg["data"]["warmup_sessions"]
        if len(before) < n:
            raise ValueError("预热交易日不足，请增大交易日历查询范围")
        warm_start = before[-n]
        result = {}
        fields = "date,open,high,low,close,preclose,volume,amount,tradestatus,pctChg,isST"
        for count, (code, meta) in enumerate(self.basic.iterrows(), 1):
            if meta.ipoDate > end or (pd.notna(meta.outDate) and meta.outDate < warm_start):
                continue
            raw = self._history(code, warm_start, end, "3", fields)
            if raw.empty:
                if meta.ipoDate <= end and (pd.isna(meta.outDate) or meta.outDate >= start):
                    raise RuntimeError(f"应有数据但接口返回空表：{code}；拒绝当作无信号跳过")
                continue
            adjusted = self._history(code, warm_start, end, "1", "date,open,high,low,close")
            f = enrich_raw(raw, adjusted)
            # 保存历史是否满足最低上市交易日数，而不利用当前名称判断历史 ST。
            listing_idx = self.calendar.searchsorted(meta.ipoDate)
            f["listing_sessions"] = self.calendar.searchsorted(f.index) - listing_idx + 1
            expected = self.calendar[(self.calendar >= max(start, meta.ipoDate)) & (self.calendar <= end)]
            if pd.notna(meta.outDate):
                expected = expected[expected < meta.outDate]
            missing = expected.difference(f.index)
            if len(missing):
                raise RuntimeError(f"{code} 缺少交易日日线 {missing[0].date()}；无法区分停牌与接口缺失，已停止")
            result[code] = f
            if count % 100 == 0:
                LOG.info("当前分段读取进度 %d/%d", count, len(self.basic))
        return result

    def dividend_events(self, code: str, day: pd.Timestamp) -> list[dict]:
        key = (code, day.year)
        if key not in self._dividends:
            self._dividends[key] = self._query("query_dividend_data", code=code, year=str(day.year), yearType="operate")
        df = self._dividends[key]
        if df.empty or "dividOperateDate" not in df:
            return []
        return df.loc[df["dividOperateDate"].eq(str(day.date()))].to_dict("records")


def second_wave(values: np.ndarray, s: dict) -> float:
    """仅查看窗口内历史：先上涨、再回撤、后反弹。不是未来确认的拐点。"""
    bottom = peak = float(values[0])
    rising = armed = False
    trough = math.inf
    for p in values[1:]:
        p = float(p)
        if not rising:
            bottom = min(bottom, p)
            if p / bottom - 1 >= s["wave_prior_rise"]:
                rising, peak = True, p
        elif not armed:
            peak = max(peak, p)
            if p / peak - 1 <= -s["wave_drawdown"]:
                armed, trough = True, p
        else:
            trough = min(trough, p)
    if not armed or values[-1] / trough - 1 < s["wave_rebound"]:
        return 0.0
    if s["wave_mode"] == "strict":
        return 1.0
    return float(abs(values[-1] / peak - 1) <= s["prior_high_band"])


def make_signals(f: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """每行信号在该日收盘后才成立；引擎只能在后续交易日使用。"""
    s = cfg["strategy"]
    c = f["a_close"]
    fast, mid, slow = [c.rolling(s[k]).mean() for k in ("fast_ma", "middle_ma", "slow_ma")]
    n = s["trend_days"]
    trend = (fast > mid) & (mid > slow)
    for ma in (fast, mid, slow):
        trend &= ma > ma.shift(s["slope_days"])
    rising = fast.diff().gt(0).rolling(n).sum()
    body_distance = f[["a_open", "a_close"]].min(axis=1) / fast - 1
    touches = body_distance.between(s["touch_min"], s["touch_max"]).rolling(n).sum()
    bias = c / fast - 1
    # 复权序列只参与比值；映射回昨日原始价格尺度后再计算挂单。
    amp = (f["a_high"] - f["a_low"]) / c.shift(1)
    avg_amp = amp.rolling(s["amplitude_days"]).mean()
    limit_count = f["limit_up"].rolling(s["limit_lookback"]).sum()
    consecutive = f["limit_up"].rolling(s["exclude_consecutive"]).sum().ge(s["exclude_consecutive"])
    recent_consecutive = consecutive.rolling(s["consecutive_lookback"]).max().eq(1)
    recent_active = f["tradestatus"].eq(1).rolling(n).sum().eq(n)
    wave = pd.Series(False, index=f.index)
    if s["first_wave_filter"]:
        wave = c.rolling(s["wave_lookback"]).apply(lambda x: second_wave(x, s), raw=True).eq(1)
    eligible = (trend & rising.ge(s["min_rising_days"]) & touches.ge(s["min_touch_days"])
                & bias.between(0, s["close_bias_max"])
                & avg_amp.between(s["amplitude_min"], s["amplitude_max"])
                & limit_count.ge(s["min_limit_count"]) & ~recent_consecutive & ~wave
                & f["pctChg"].div(100).le(s["yesterday_return_max"])
                & f["isST"].eq(0) & recent_active
                & f["listing_sessions"].ge(cfg["data"]["min_listing_sessions"])
                & f["amount"].rolling(s["amplitude_days"]).mean().ge(s["min_average_amount"]))
    # 通用 n 日：前 n-1 个收盘，加上以昨收替代的今日预估价。
    predicted_ma = (c.rolling(s["fast_ma"] - 1).sum() + c) / s["fast_ma"] / f["scale"]
    # 次日信号必须来自恰好上一交易日；不允许停牌期间保留过时买单。
    return pd.DataFrame({"eligible": eligible.fillna(False), "touches": touches,
                         "bias": bias, "predicted_ma": predicted_ma, "previous_close": f["close"]}, index=f.index)


def buy_limit(signal: pd.Series, e: dict) -> float | None:
    ma, prev = float(signal["predicted_ma"]), float(signal["previous_close"])
    if not np.isfinite(ma) or ma > prev or ma <= 0:
        return None
    if e["buy_price_mode"] == "offset":
        target = min(prev, ma * (1 + e["buy_offset"]))
    else:
        target = ma + e["buy_position"] * (prev - ma)
    return rounded(target, e["tick_size"], "down")


def valid_bar(b: pd.Series) -> bool:
    return (b["tradestatus"] == 1 and b["volume"] > 0 and
            all(np.isfinite(b[k]) and b[k] > 0 for k in PRICE_COLS))


def entry_fill(b: pd.Series, limit: float, e: dict) -> tuple[float, str] | None:
    """撮合预先有效的日内限价单：严禁用今日最低价生成该订单。"""
    if not valid_bar(b) or b["isST"] != 0:
        return None
    if b["open"] >= b["upper"] - .005:
        return None  # 不假设开盘涨停排队买入成功
    if b["high"] - b["low"] < .005 and b["open"] <= b["lower"] + .005:
        return None
    if b["open"] <= limit + 1e-9:
        fill = min(limit, rounded(b["open"] * (1 + e["slippage_bps"] / 10000), direction="up"))
        return fill, "open_limit"
    penetration = e["limit_penetration_ticks"] * e["tick_size"]
    if e["entry_mode"] == "limit_touch" and b["low"] <= limit - penetration + 1e-9:
        return limit, "intraday_limit_model"
    return None


def rate_at(table: list, day: pd.Timestamp) -> float:
    matches = [float(value) for date, value in table if pd.Timestamp(date) <= day]
    if not matches:
        raise ValueError(f"费率表未覆盖 {day.date()}")
    return matches[-1]


def fees(notional: float, sell: bool, day: pd.Timestamp, e: dict) -> float:
    commission = max(e["minimum_commission"], notional * e["commission_rate"])
    tax = notional * rate_at(e["stamp_tax_schedule"], day) if sell else 0
    transfer = notional * rate_at(e["transfer_fee_schedule"], day)
    return commission + tax + transfer


@dataclass
class Position:
    shares: int
    cost: float
    entry_day: pd.Timestamp
    entry_session: int
    mark: float
    last_scale: float
    force_exit: bool = False
    dividend_credit: float = 0.0
    locked: list[tuple[pd.Timestamp, int]] = field(default_factory=list)

    def sellable(self, day: pd.Timestamp) -> int:
        if day <= self.entry_day:
            return 0
        return self.shares - sum(qty for date, qty in self.locked if date > day)


@dataclass
class BacktestResult:
    equity: pd.DataFrame
    trades: pd.DataFrame
    diagnostics: pd.DataFrame
    metrics: dict
    positions: dict


class DailyEngine:
    def __init__(self, cfg: dict, label="run"):
        self.cfg, self.label = cfg, label
        self.e = cfg["execution"]
        self.cash = float(cfg["research"]["initial_cash"])
        self.positions: dict[str, Position] = {}
        self.receivables: list[tuple[pd.Timestamp, float, str]] = []
        self.equity_rows, self.trade_rows, self.diag_rows = [], [], []
        self.session = 0
        self.previous_equity = self.cash
        self.previous_day: pd.Timestamp | None = None
        self.total_fees = 0.0
        self.stats = Counter()

    def _equity(self) -> float:
        return self.cash + sum(x.shares*x.mark for x in self.positions.values()) + sum(x[1] for x in self.receivables)

    def _corporate_action(self, code, p, b, day, provider):
        if abs(b["scale"] / p.last_scale - 1) <= 1e-6:
            return
        events = provider.dividend_events(code, day)
        if len(events) != 1:
            raise RuntimeError(f"{code} {day.date()} 持仓遇到复权变化但无法唯一解释公司行动，拒绝伪造收益")
        event = events[0]
        def num(key):
            value = event.get(key, "")
            return 0.0 if value in ("", None) else float(value)
        cash_ps = num("dividCashPsBeforeTax")
        bonus_ps = num("dividStocksPs") + num("dividReserveToStockPs")
        theoretical = (p.mark - cash_ps) / (1 + bonus_ps)
        if abs(theoretical - b["reference"]) > max(.03, theoretical * .002):
            raise RuntimeError(f"{code} 公司行动无法与除权参考价核对；可能存在配股或特殊重组，已停止")
        gross_bonus = p.shares * bonus_ps
        bonus = int(round(gross_bonus))
        if abs(gross_bonus - bonus) > .001:
            raise RuntimeError(f"{code} 出现零碎送转股，本版本拒绝自动猜测登记结算分配")
        if bonus:
            listing = pd.to_datetime(event.get("dividStockMarketDate", ""), errors="coerce")
            if pd.isna(listing):
                raise RuntimeError(f"{code} 缺少送转股份上市日，无法判断可卖数量")
            p.locked.append((listing, bonus))
        credit = p.shares * cash_ps * (1 - self.e["dividend_tax_assumption"])
        if credit:
            pay = pd.to_datetime(event.get("dividPayDate", ""), errors="coerce")
            if pd.isna(pay):
                raise RuntimeError(f"{code} 缺少现金分红到账日期")
            self.receivables.append((pay, credit, code))
            p.dividend_credit += credit
        p.shares += bonus
        p.mark = theoretical
        self.stats["corporate_actions"] += 1

    def _sell(self, code, b, day, price, reason, capacity):
        p = self.positions[code]
        # 开盘跌停时保守不成交，不能利用当天后来开板来倒推开盘可卖。
        if not valid_bar(b) or price <= b["lower"] + .005:
            self.stats["blocked_exit"] += 1
            p.force_exit = True
            return
        qty = min(p.sellable(day), capacity)
        if qty < p.sellable(day):
            qty = qty // self.e["lot_size"] * self.e["lot_size"]
        if qty <= 0:
            self.stats["blocked_exit"] += 1
            return
        price = max(b["lower"], rounded(price * (1-self.e["slippage_bps"]/10000), direction="down"))
        value = qty * price
        charge = fees(value, True, day, self.e)
        portion = qty / p.shares
        basis, dividend = p.cost*portion, p.dividend_credit*portion
        pnl = value - charge - basis + dividend
        self.cash += value-charge
        self.total_fees += charge
        self.trade_rows.append(dict(date=str(day.date()), code=code, side="SELL", shares=qty, price=price,
                                    fee=charge, reason=reason, pnl=pnl, cost_basis=basis,
                                    dividend_attribution=dividend, entry_date=str(p.entry_day.date()),
                                    holding_sessions=self.session-p.entry_session))
        p.shares -= qty
        p.cost -= basis
        p.dividend_credit -= dividend
        p.force_exit = True
        # 只有上市日之后的锁定股份才可能被卖出；已解锁记录无需继续保存。
        p.locked = [(date, n) for date, n in p.locked if date > day]
        if p.shares == 0:
            del self.positions[code]

    def step(self, day, bars, candidates, provider, last_day=False):
        self.session += 1
        e = self.e
        old_codes = set(self.positions)
        # 结算只处理在当天发生的公司行动，不把将来分红信息交给选股器。
        for code, p in list(self.positions.items()):
            if code not in bars:
                raise RuntimeError(f"持仓 {code} 在 {day.date()} 缺行情：可能退市，禁止静默保留旧价")
            b = bars[code]
            self._corporate_action(code, p, b, day, provider)
        paid = [item for item in self.receivables if item[0] <= day]
        self.cash += sum(item[1] for item in paid)
        self.receivables = [item for item in self.receivables if item[0] > day]
        # 买入预算在旧仓卖出前冻结，绝不使用当日收盘卖出的资金回填早盘买单。
        reserved_budget = min(e["daily_buy_fraction"]*self.previous_equity, max(0, self.cash),
                              max(0, e["max_total_exposure"]*self.previous_equity -
                                  sum(p.shares*p.mark for p in self.positions.values())))
        sold_today, deferred_close = set(), []
        for code in sorted(old_codes):
            p, b = self.positions[code], bars[code]
            age = self.session-p.entry_session
            if p.sellable(day) <= 0:
                continue
            max_age = e["max_holding_sessions"]
            if max_age is not None and age >= max_age:
                p.force_exit = True
            opened_up = valid_bar(b) and b["open"] >= b["upper"] - .005
            if e["hold_open_limit_up"] and opened_up and not p.force_exit:
                self.stats["limit_up_hold_days"] += 1
                # 只有收盘后才知道日内曾开板；该信息仅影响下一个交易日。
                if b["low"] < b["upper"] - .005:
                    p.force_exit = True
                continue
            cap = max(0, int(np.nan_to_num(b["previous_volume"]) * e["max_volume_fraction"]))
            if e["exit_mode"] == "next_close":
                deferred_close.append(code)
            else:
                before = p.shares
                self._sell(code, b, day, b["open"], "next_open", cap)
                if code not in self.positions or self.positions[code].shares < before:
                    sold_today.add(code)
        eligible_count = len(candidates)
        placed, filled, spent = 0, 0, 0.0
        # 入选列表和排序均在昨收时确定，不能用今天的振幅或成交量排序。
        chosen = [(code, sig) for code, sig in candidates
                  if code not in old_codes and code not in sold_today][:e["max_new_positions"]]
        slot = min(e["single_position_fraction"]*self.previous_equity,
                   reserved_budget/e["max_new_positions"])
        if not last_day:
            for code, signal in chosen:
                b = bars.get(code)
                if b is None:
                    raise RuntimeError(f"候选 {code} 缺少当日行情")
                if e["block_on_ex_rights_entry"] and b["factor_changed"]:
                    self.stats["ex_rights_entries_skipped"] += 1
                    continue  # 当日除权已公开；不能直接沿用昨日价格尺度的买单
                limit = buy_limit(signal, e)
                if limit is None:
                    continue
                placed += 1
                outcome = entry_fill(b, limit, e)
                if outcome is None:
                    continue
                price, reason = outcome
                # 开盘成交用昨日成交量作保守容量参考；日内限价匹配用当日量作撮合上界。
                volume = b["previous_volume"] if reason == "open_limit" else b["volume"]
                capacity = max(0, int(np.nan_to_num(volume) * e["max_volume_fraction"]))
                budget = min(slot, reserved_budget-spent, self.cash)
                qty = min(int(budget/price), capacity)//e["lot_size"]*e["lot_size"]
                while qty > 0 and qty*price+fees(qty*price, False, day, e) > budget+1e-8:
                    qty -= e["lot_size"]
                if qty <= 0:
                    continue
                charge = fees(qty*price, False, day, e)
                cost = qty*price+charge
                self.cash -= cost
                self.total_fees += charge
                spent += cost
                self.positions[code] = Position(qty, cost, day, self.session, price, b["scale"])
                self.trade_rows.append(dict(date=str(day.date()), code=code, side="BUY", shares=qty,
                                            price=price, fee=charge, reason=reason, pnl=None,
                                            entry_date=str(day.date()), limit=limit))
                if price < signal["predicted_ma"]:
                    self.stats["fills_below_predicted_ma"] += 1
                filled += 1
        for code in deferred_close:
            p, b = self.positions[code], bars[code]
            cap = max(0, int(b["volume"] * e["max_volume_fraction"]))
            self._sell(code, b, day, b["close"], "scheduled_next_close", cap)
        for code, p in self.positions.items():
            b = bars[code]
            if np.isfinite(b["close"]) and b["close"] > 0:
                p.mark = float(b["close"])
            p.last_scale = float(b["scale"])
        equity = self._equity()
        if self.cash < -1e-6 or not np.isfinite(equity):
            raise AssertionError("账户出现负现金或非有限净资产")
        exposure = sum(p.shares*p.mark for p in self.positions.values())
        self.equity_rows.append(dict(date=str(day.date()), equity=equity, cash=self.cash,
                                     market_value=exposure, receivable=sum(x[1] for x in self.receivables),
                                     exposure=exposure/equity if equity else 0, positions=len(self.positions)))
        self.diag_rows.append(dict(date=str(day.date()), eligible=eligible_count, orders=placed,
                                   fills=filled, buy_budget=reserved_budget, buy_spent=spent))
        self.stats["orders"] += placed
        self.stats["fills"] += filled
        self.previous_equity = equity
        self.previous_day = day

    def finish(self) -> BacktestResult:
        equity = pd.DataFrame(self.equity_rows)
        trades = pd.DataFrame(self.trade_rows)
        diag = pd.DataFrame(self.diag_rows)
        initial = self.cfg["research"]["initial_cash"]
        values = np.r_[initial, equity["equity"].to_numpy(float)]
        returns = values[1:]/values[:-1]-1
        drawdown = values/np.maximum.accumulate(values)-1
        annual = (values[-1]/initial)**(252/len(returns))-1 if len(returns) else 0
        sell = trades.loc[trades["side"].eq("SELL")] if not trades.empty else pd.DataFrame()
        pnl = sell["pnl"].astype(float).to_numpy() if not sell.empty else np.array([])
        wins, losses = pnl[pnl > 0], pnl[pnl < 0]
        ret_std = returns.std(ddof=1) if len(returns) > 1 else 0
        metrics = dict(initial_cash=initial, final_equity=values[-1], total_return=values[-1]/initial-1,
                       annualized_return=annual, max_drawdown=float(-drawdown.min()),
                       sharpe=float(returns.mean()/ret_std*np.sqrt(252)) if ret_std > 0 else None,
                       sell_fills=len(pnl), win_rate=float((pnl > 0).mean()) if len(pnl) else None,
                       profit_factor=float(wins.sum()/-losses.sum()) if len(losses) else None,
                       mean_sell_fill_pnl=float(pnl.mean()) if len(pnl) else None,
                       top_5_sell_fill_pnl=float(np.sort(pnl)[-5:].sum()) if len(pnl) else 0,
                       total_fees=self.total_fees, open_positions=len(self.positions),
                       pending_dividend_receivable=sum(x[1] for x in self.receivables),
                       order_fill_rate=self.stats["fills"]/self.stats["orders"] if self.stats["orders"] else 0,
                       average_exposure=float(equity["exposure"].mean()), diagnostics=dict(self.stats))
        # 部分卖出按成交笔统计，而不是伪称完整交易胜率。
        metrics["metric_note"] = "胜率/盈亏比按卖出成交笔统计；分红采用配置税率假设；未平仓以市值计入净资产。"
        positions = {code: dict(shares=p.shares, cost=p.cost, mark=p.mark,
                                entry_day=str(p.entry_day.date()), force_exit=p.force_exit,
                                locked=[(str(day.date()), qty) for day, qty in p.locked])
                     for code, p in self.positions.items()}
        return BacktestResult(equity, trades, diag, metrics, positions)


def chunks(days: pd.DatetimeIndex, months: int):
    current = days[0]
    while current <= days[-1]:
        boundary = current + pd.DateOffset(months=months)
        selected = days[(days >= current) & (days < boundary)]
        if len(selected):
            yield selected
        remaining = days[days >= boundary]
        if len(remaining) == 0:
            break
        current = remaining[0]


def run_variants(provider, configs: list[dict], segment: str, labels: list[str] | None = None):
    start, end = map(pd.Timestamp, configs[0]["periods"][segment])
    provider.prepare(start, end)
    days = provider.calendar[(provider.calendar >= start) & (provider.calendar <= end)]
    engines = [DailyEngine(cfg, labels[i] if labels else f"variant_{i}") for i, cfg in enumerate(configs)]
    for block_days in chunks(days, configs[0]["data"]["chunk_months"]):
        LOG.info("%s 分段 %s ~ %s", segment, block_days[0].date(), block_days[-1].date())
        frames = provider.load_chunk(block_days[0], block_days[-1])
        signal_cache = {}
        for engine in engines:
            key = json.dumps([engine.cfg["strategy"], engine.cfg["data"]["min_listing_sessions"]], sort_keys=True)
            if key not in signal_cache:
                by_day = defaultdict(list)
                for code, frame in frames.items():
                    sigs = make_signals(frame, engine.cfg)
                    for day in block_days:
                        idx = provider.calendar.searchsorted(day)-1
                        if idx < 0:
                            continue
                        previous = provider.calendar[idx]
                        if previous in sigs.index and sigs.at[previous, "eligible"]:
                            by_day[day].append((code, sigs.loc[previous]))
                for pool in by_day.values():
                    pool.sort(key=lambda x: (-x[1]["touches"], x[1]["bias"], x[0]))
                signal_cache[key] = by_day
            pools = signal_cache[key]
            # 把同一分段交给各变体；行情只读取一次，训练变体共用内存中的数据。
            for day in block_days:
                bars = {code: frame.loc[day] for code, frame in frames.items() if day in frame.index}
                engine.step(day, bars, pools.get(day, []), provider, last_day=day == days[-1])
        del frames, signal_cache
        gc.collect()
    return [engine.finish() for engine in engines]


def json_safe(obj):
    if isinstance(obj, dict):
        return {str(k): json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [json_safe(v) for v in obj]
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating, float)):
        return float(obj) if np.isfinite(obj) else None
    return obj


def save_result(result: BacktestResult, cfg: dict, path: Path, segment: str, demo=False):
    path.mkdir(parents=True, exist_ok=True)
    result.equity.to_csv(path/"equity.csv", index=False, encoding="utf-8-sig")
    result.trades.to_csv(path/"trades.csv", index=False, encoding="utf-8-sig")
    result.diagnostics.to_csv(path/"diagnostics.csv", index=False, encoding="utf-8-sig")
    packages = {}
    for package in ("numpy", "pandas", "baostock"):
        try:
            packages[package] = version(package)
        except PackageNotFoundError:
            packages[package] = None
    metadata = dict(metrics=result.metrics, open_positions=result.positions, config=cfg, segment=segment,
                    is_synthetic_demo=demo, packages=packages,
                    source_code_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                    generated_at=pd.Timestamp.now(tz="Asia/Shanghai").isoformat(),
                    limitations=["日线限价触达为成交模型，不能证明真实排队成交", "不恢复盘中路径与集合竞价",
                                 "涨跌停由数据源前收盘参考价和普通主板制度推算，非独立限价字段",
                                 "分红税为固定参数假设；不支持配股、复杂重组、零碎送转股",
                                 "历史股票全集依赖供应商收录完整性；自选股票池存在选择偏差",
                                 "行情仅驻留内存，不写入结果文件；再次运行会重新请求接口"])
    (path/"summary.json").write_text(json.dumps(json_safe(metadata), ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    LOG.info("%s 结果：收益 %.2f%%，最大回撤 %.2f%%，卖出成交 %d 笔，未平仓 %d 只",
             segment, result.metrics["total_return"]*100, result.metrics["max_drawdown"]*100,
             result.metrics["sell_fills"], result.metrics["open_positions"])


def score(result: BacktestResult, minimum: int) -> float | None:
    m = result.metrics
    if m["sell_fills"] < minimum:
        return None
    # 简单、显式、可替换的选择目标；禁止按测试集选择。
    return float(m["annualized_return"] - 2*m["max_drawdown"])


def tune(provider, cfg: dict, output: Path):
    grid = cfg["research"]["grid"]
    keys = list(grid)
    variants = []
    for vals in itertools.product(*(grid[key] for key in keys)):
        variant = copy.deepcopy(cfg)
        for key, value in zip(keys, vals):
            if not key.startswith(("strategy.", "execution.")):
                raise ValueError("调参网格只允许 strategy.* 或 execution.*，不得改变数据与日期")
            set_parameter(variant, key, value)
        validate_config(variant)
        variants.append(variant)
    if not variants or len(variants) > 64:
        raise ValueError("每次仅允许 1~64 个参数组合，避免免费接口和内存被过度占用")
    train = run_variants(provider, variants, "train")
    minimum = cfg["research"]["minimum_closed_trades"]
    ranking = [(i, score(r, minimum)) for i, r in enumerate(train)]
    ranking = [(i, value) for i, value in ranking if value is not None]
    output.mkdir(parents=True, exist_ok=True)
    table = [dict(variant=i, score=score(r, minimum), **r.metrics,
                  parameters=json.dumps({k: _get(v, k) for k in keys}, ensure_ascii=False))
             for i, (v, r) in enumerate(zip(variants, train))]
    pd.DataFrame(table).to_csv(output/"training_comparison.csv", index=False, encoding="utf-8-sig")
    if not ranking:
        raise RuntimeError("训练集所有组合成交样本不足；未进入验证和测试，先检查图形阈值与股票池")
    ranking.sort(key=lambda x: (-x[1], x[0]))
    shortlist = [i for i, _ in ranking[:cfg["research"]["validation_shortlist"]]]
    validation = run_variants(provider, [variants[i] for i in shortlist], "validation")
    valid_rank = [(i, score(r, minimum)) for i, r in zip(shortlist, validation)]
    pd.DataFrame([dict(variant=i, score=sc, **r.metrics) for (i, sc), r in zip(valid_rank, validation)]).to_csv(
        output/"validation_comparison.csv", index=False, encoding="utf-8-sig")
    valid_rank = [(i, sc) for i, sc in valid_rank if sc is not None]
    if not valid_rank:
        raise RuntimeError("验证集成交样本不足，未执行最终测试")
    valid_rank.sort(key=lambda x: (-x[1], x[0]))
    winner = valid_rank[0][0]
    chosen = variants[winner]
    (output/"selected_config.json").write_text(json.dumps(chosen, ensure_ascii=False, indent=2), encoding="utf-8")
    LOG.info("训练、验证完成，冻结组合 %d 后仅执行一次最终测试", winner)
    final = run_variants(provider, [chosen], "test")[0]
    save_result(final, chosen, output/"test", "test")


def _get(cfg, path):
    value = cfg
    for key in path.split("."):
        value = value[key]
    return value


class DemoProvider:
    """固定随机种子的合成数据。仅验证代码流程，不代表任何股票的历史收益。"""
    def __init__(self, cfg):
        self.cfg = cfg
        self.calendar = pd.bdate_range("2022-01-03", "2025-12-31")
        rng = np.random.default_rng(20261016)
        self.frames = {}
        for i in range(8):
            close = (10+i)*np.exp(np.cumsum(rng.normal(.0015, .012, len(self.calendar))))
            previous = np.r_[close[0], close[:-1]]
            opening = previous*np.exp(rng.normal(-.002, .006, len(close)))
            raw = pd.DataFrame(dict(open=opening, high=np.maximum(opening,close)*1.015,
                                    low=np.minimum(opening,close)*.985, close=close,
                                    preclose=previous, volume=np.full(len(close), 10000000),
                                    amount=close*10000000, tradestatus=1, pctChg=(close/previous-1)*100,
                                    isST=0), index=self.calendar)
            self.frames[f"sh.{600000+i:06d}"] = enrich_raw(raw, raw[PRICE_COLS])
            self.frames[f"sh.{600000+i:06d}"]["listing_sessions"] = 1000+np.arange(len(close))

    def prepare(self, start, end):
        if start < self.calendar[180] or end > self.calendar[-1]:
            raise ValueError("合成演示日期超出范围")

    def load_chunk(self, start, end):
        before = self.calendar[self.calendar < start]
        warm = before[-self.cfg["data"]["warmup_sessions"]]
        return {code: f.loc[warm:end].copy() for code, f in self.frames.items()}

    def dividend_events(self, code, day):
        return []


def demo_config(cfg):
    cfg = copy.deepcopy(cfg)
    cfg["periods"] = dict(train=["2023-01-02", "2023-12-29"],
                           validation=["2024-01-01", "2024-12-31"], test=["2025-01-01", "2025-12-31"])
    cfg["strategy"].update(min_limit_count=0, min_rising_days=5, min_touch_days=5,
                            first_wave_filter=False, close_bias_max=.10)
    return cfg


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["backtest", "tune", "demo", "check"])
    parser.add_argument("--config", default=str(DEFAULT_FILE))
    parser.add_argument("--segment", choices=["train", "validation", "test"], default="test")
    parser.add_argument("--output", default="results")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = load_config(args.config)
    if args.command == "demo":
        cfg = demo_config(cfg)
        LOG.warning("以下全部为合成数据演示，不能用于判断策略收益")
        result = run_variants(DemoProvider(cfg), [cfg], "test")[0]
        save_result(result, cfg, Path(args.output)/"synthetic_demo", "test", demo=True)
        return 0
    with BaoStockProvider(cfg) as provider:
        if args.command == "check":
            end = pd.Timestamp(cfg["periods"][args.segment][1])
            provider.prepare(pd.Timestamp(cfg["periods"][args.segment][0]), end)
            last = provider.calendar[provider.calendar <= end][-1]
            sample = provider._history("sh.600000", last-pd.Timedelta(days=10), last, "3", "date,close")
            LOG.info("连通性检查成功：样例 %d 行；仅在内存中读取，没有导出行情", len(sample))
        elif args.command == "tune":
            tune(provider, cfg, Path(args.output)/"research")
        else:
            result = run_variants(provider, [cfg], args.segment)[0]
            save_result(result, cfg, Path(args.output)/args.segment, args.segment)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, RuntimeError, ConnectionError) as error:
        LOG.error("运行停止：%s", error)
        raise SystemExit(2)
