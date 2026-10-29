"""真实行情规则样本：同时显示通过率、淘汰率和单项边界。"""

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import plotly.graph_objects as go

from src.config.settings import Settings, StrategyConfig
from src.data.storage import DataStore, write_json
from src.reporting.charts import save_static_chart
from src.reporting.local_html import write_local_page
from src.reporting.rule_charts import boundary_figure, price_levels
from src.reporting.rule_details import rule_details
from src.strategy.rules import RULE_LABELS
from src.strategy.screening import screen_market

TARGET_RULES = ["no_acceleration", "close_distance", "touch_ma5", "support"]


def boundary_margin(
    frame: pd.DataFrame, rule: str, config: StrategyConfig
) -> pd.Series:
    """计算与目标门槛的距离；加速条件使用两个归一化超限量的较小值。"""
    if rule == "no_acceleration":
        return pd.concat(
            [
                frame["acceleration_ratio"] / config.max_acceleration_ratio - 1,
                frame["acceleration_return"] / config.acceleration_min_return - 1,
            ],
            axis=1,
        ).min(axis=1)
    if rule == "close_distance":
        return config.max_close_ma5_distance - frame["max_close_distance"]
    if rule == "touch_ma5":
        return frame["touch_ratio"] - config.min_touch_ratio
    return frame["min_close_distance"] + config.max_below_ma5


def pick_samples(
    frame: pd.DataFrame, rule: str, config: StrategyConfig
) -> pd.DataFrame:
    """每侧选两个临界样本，再优先补入其余规则全通过的样本。"""
    frame = frame.copy()
    frame["boundary_distance"] = boundary_margin(frame, rule, config).abs()
    frame["other_failed"] = frame["failed_count"] - (
        ~frame[f"pass_{rule}"].astype(bool)
    ).astype(int)
    parts = []
    for passed in [True, False]:
        pool = frame.loc[frame[f"pass_{rule}"].eq(passed)].sort_values(
            ["boundary_distance", "other_failed", "order_book_id"]
        )
        near = pool.head(2)
        remaining = pool.loc[~pool.index.isin(near.index)]
        obvious = (
            remaining.loc[
                remaining["boundary_distance"]
                >= remaining["boundary_distance"].quantile(0.75)
            ]
            .sort_values(["other_failed", "boundary_distance", "order_book_id"])
            .head(1)
        )
        relevant = remaining.loc[remaining["other_failed"].eq(0)].head(1)
        parts.extend([near, relevant if not relevant.empty else obvious])
    return pd.concat(parts)


def acceleration_scatter(frame: pd.DataFrame, config: StrategyConfig) -> dict:
    """显示加速双门槛的联合分布，右上区域才会被这一条淘汰。"""
    fig = go.Figure(
        go.Scatter(
            x=frame["acceleration_ratio"],
            y=frame["acceleration_return"],
            mode="markers",
            text=frame["order_book_id"] + " " + frame["symbol"],
            marker={
                "color": frame["pass_no_acceleration"].map(
                    {True: "#059669", False: "#dc2626"}
                ),
                "size": 5,
                "opacity": 0.65,
            },
            hovertemplate="%{text}<br>加速%{x:.2f}倍<br>末段涨幅%{y:.2%}<extra></extra>",
        )
    )
    fig.add_vline(x=config.max_acceleration_ratio, line_dash="dash")
    fig.add_hline(y=config.acceleration_min_return, line_dash="dash")
    fig.update_layout(
        title="加速倍数与末段涨幅：同时越过两条线才淘汰",
        template="plotly_white",
        height=450,
        xaxis_title="加速倍数",
        yaxis_title="末段复合涨幅",
        yaxis_tickformat=".0%",
    )
    return json.loads(fig.to_json())


def export_rule_comparison(settings: Settings, requested: str | None = None) -> Path:
    """生成四组单项条件的真实K线和全市场诊断快照。"""
    store = DataStore(Path(settings.data.root))
    date = store.resolve_date(requested or store.read_metadata()["end_date"])
    bars = store.read_window(date, settings.history_bars)
    factors = store.read_factors(date)
    instruments = pd.read_parquet(store.root / "instruments.parquet")
    result = screen_market(bars, factors, instruments, date, settings.strategy)
    eligible = result.diagnostics.loc[result.diagnostics["eligible"]].copy()
    stamp = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d_%H%M%S_%f")
    root = Path(settings.report.root) / f"rule_comparison_{date:%Y%m%d}_{stamp}"
    (root / "charts").mkdir(parents=True)
    groups, sample_records = [], []
    for rule in TARGET_RULES:
        passed_count = int(eligible[f"pass_{rule}"].sum())
        target_failed = eligible[f"pass_{rule}"].eq(False)
        other_passed = eligible["failed_count"] - target_failed.astype(int) == 0
        samples = []
        for _, row in pick_samples(eligible, rule, settings.strategy).iterrows():
            symbol = row["order_book_id"]
            passed = bool(row[f"pass_{rule}"])
            details = next(
                item
                for item in rule_details(row.to_dict(), settings.strategy)
                if item["key"] == rule
            )
            status = "本条通过" if passed else "本条未通过"
            title = f"{symbol} {row['symbol']} | {date.date()} | {status}"
            history = result.histories[symbol].tail(60)
            png = f"charts/{rule}_{symbol}.png"
            save_static_chart(
                history,
                title + "\n" + details["value"] + "；" + details["threshold"],
                root / png,
                settings.strategy.trend_days,
                levels=price_levels(rule, settings.strategy),
                split_days=(
                    settings.strategy.acceleration_days
                    if rule == "no_acceleration"
                    else 0
                ),
            )
            samples.append(
                {
                    "title": title,
                    "passed": passed,
                    "png": png,
                    "detail": details,
                    "selected": bool(row["selected"]),
                    "other_failures": row["failed_rules"],
                    "figure": boundary_figure(history, rule, title, settings.strategy),
                }
            )
            sample_records.append(
                {
                    "rule": rule,
                    "order_book_id": symbol,
                    "passed": passed,
                    "value": details["value"],
                    "threshold": details["threshold"],
                }
            )
        groups.append(
            {
                "key": rule,
                "label": RULE_LABELS[rule],
                "passed": passed_count,
                "failed": len(eligible) - passed_count,
                "total": len(eligible),
                "other_rules_passed": int(other_passed.sum()),
                "target_only_rejected": int((other_passed & target_failed).sum()),
                "samples": samples,
            }
        )
    payload = {
        "date": str(date.date()),
        "groups": groups,
        "scatter": acceleration_scatter(eligible, settings.strategy),
        "selected_count": int(result.diagnostics["selected"].sum()),
    }
    body = """<p class="notice">图中“本条通过”只代表目标规则，不等于全部推土机条件通过。
样本包括临界值，并优先补入其他条件都通过的股票；没有使用未来收益。
统计分母为全部具备资格股票。</p>
<p>二波反弹仅展示；涨幅范围和近期最低价触及MA20剔除条件以本次参数快照为准。</p>
<div id="summary"></div><div id="scatter"></div><div id="groups"></div>"""
    write_local_page(
        root / "index.html",
        f"推土机规则门槛对照 · {date.date()}",
        body,
        payload,
        "rule_comparison.js",
    )
    result.diagnostics.to_csv(
        root / "diagnostics.csv", index=False, encoding="utf-8-sig"
    )
    pd.DataFrame(sample_records).to_csv(
        root / "samples.csv", index=False, encoding="utf-8-sig"
    )
    write_json(root / "parameters.json", settings.to_dict())
    write_json(
        root / "summary.json",
        {
            key: value
            for key, value in payload.items()
            if key != "scatter" and key != "groups"
        }
        | {
            "groups": [
                {key: value for key, value in group.items() if key != "samples"}
                for group in groups
            ]
        },
    )
    return root / "index.html"
