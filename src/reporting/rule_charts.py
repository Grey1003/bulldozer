"""带价格门槛和日指标子图的规则比较K线。"""

import json

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from src.config.settings import StrategyConfig
from src.reporting.charts import MA_COLORS


def price_levels(rule: str, config: StrategyConfig) -> dict[str, float]:
    """将目标规则转换为MA5上下边界乘数。"""
    return {
        "close_distance": {
            f"收盘上限 +{config.max_close_ma5_distance:.0%}": 1
            + config.max_close_ma5_distance
        },
        "touch_ma5": {
            f"低点上沿 +{config.max_low_ma5_distance:.1%}": 1
            + config.max_low_ma5_distance,
            f"低点下沿 -{config.max_low_ma5_distance:.1%}": 1
            - config.max_low_ma5_distance,
        },
        "support": {f"收盘底线 -{config.max_below_ma5:.0%}": 1 - config.max_below_ma5},
    }.get(rule, {})


def boundary_figure(
    history: pd.DataFrame, rule: str, title: str, config: StrategyConfig
) -> dict:
    """绘制真实历史K线与门槛，全部数据止于样本研究日。"""
    history = history.tail(60)
    dates = history["date"].dt.strftime("%Y-%m-%d")
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.75, 0.25])
    fig.add_trace(
        go.Candlestick(
            x=dates,
            open=history["open"],
            high=history["high"],
            low=history["low"],
            close=history["close"],
            name="日K",
            increasing_line_color="#dc2626",
            decreasing_line_color="#059669",
        ),
        row=1,
        col=1,
    )
    for period in [5, 10, 20]:
        fig.add_trace(
            go.Scatter(
                x=dates,
                y=history[f"ma{period}"],
                name=f"MA{period}",
                line={"color": MA_COLORS[period]},
            ),
            row=1,
            col=1,
        )
    for label, multiple in price_levels(rule, config).items():
        fig.add_trace(
            go.Scatter(
                x=dates,
                y=history["ma5"] * multiple,
                name=label,
                line={"dash": "dot", "color": "#9333ea"},
            ),
            row=1,
            col=1,
        )
    if rule == "no_acceleration":
        fig.add_trace(
            go.Bar(x=dates, y=history["daily_return"], name="真实日收益率"),
            row=2,
            col=1,
        )
        recent = history.tail(config.trend_days)
        positive = recent["daily_return"].clip(lower=0)
        base = max(float(positive.iloc[: -config.acceleration_days].mean()), 0.003)
        speed = float(positive.tail(config.acceleration_days).mean())
        fig.add_trace(
            go.Scatter(
                x=dates.tail(config.trend_days).iloc[: -config.acceleration_days],
                y=[base] * (config.trend_days - config.acceleration_days),
                name="前段正收益均速(最低0.3%)",
                line={"color": "#2563eb"},
            ),
            row=2,
            col=1,
        )
        fig.add_trace(
            go.Scatter(
                x=dates.tail(config.acceleration_days),
                y=[speed] * config.acceleration_days,
                name="末段正收益均速",
                line={"color": "#ea580c"},
            ),
            row=2,
            col=1,
        )
    else:
        field = "low" if rule == "touch_ma5" else "close"
        fig.add_trace(
            go.Scatter(
                x=dates,
                y=history[field] / history["ma5"] - 1,
                name="低点距MA5" if field == "low" else "收盘距MA5",
                mode="lines+markers",
            ),
            row=2,
            col=1,
        )
        for multiple in price_levels(rule, config).values():
            fig.add_hline(
                y=multiple - 1, line_dash="dash", line_color="#b91c1c", row=2, col=1
            )
    for row in [1, 2]:
        fig.add_vrect(
            x0=dates.iloc[-config.trend_days],
            x1=dates.iloc[-1],
            fillcolor="#fde68a",
            opacity=0.2,
            line_width=0,
            row=row,
            col=1,
        )
    fig.update_layout(
        title=title,
        template="plotly_white",
        height=700,
        xaxis_rangeslider_visible=False,
        legend={"orientation": "h"},
    )
    fig.update_xaxes(type="category", nticks=10)
    fig.update_yaxes(tickformat=".0%", row=2, col=1)
    return json.loads(fig.to_json())
