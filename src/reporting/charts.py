"""以同一截断历史生成静态和交互 K 线。"""

from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import plotly.graph_objects as go
from matplotlib.collections import LineCollection, PolyCollection
from plotly.subplots import make_subplots

plt.switch_backend("Agg")

MA_COLORS = {5: "#d99400", 10: "#2563eb", 20: "#b438b4", 60: "#64748b"}


def save_static_chart(
    history: pd.DataFrame,
    title: str,
    path: Path,
    trend_days: int,
    levels: dict[str, float] | None = None,
    split_days: int = 0,
) -> None:
    """绘制截至研究日的前复权 K 线、均线和未复权成交量。"""
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    fig, (axis, volume_axis) = plt.subplots(
        2,
        1,
        figsize=(14, 8),
        sharex=True,
        gridspec_kw={"height_ratios": [4, 1]},
        layout="constrained",
    )
    x = np.arange(len(history))
    colors = np.where(history["close"] >= history["open"], "#dc2626", "#059669")
    wicks = [
        ((i, low), (i, high))
        for i, low, high in zip(x, history["low"], history["high"], strict=True)
    ]
    bodies = [
        [
            (i - 0.32, open_price),
            (i - 0.32, close),
            (i + 0.32, close),
            (i + 0.32, open_price),
        ]
        for i, open_price, close in zip(
            x, history["open"], history["close"], strict=True
        )
    ]
    axis.add_collection(LineCollection(wicks, colors=colors, linewidths=0.9))
    axis.add_collection(PolyCollection(bodies, facecolors=colors, edgecolors=colors))
    axis.hlines(history["close"], x - 0.32, x + 0.32, colors=colors, linewidth=0.8)
    for window, color in MA_COLORS.items():
        axis.plot(x, history[f"ma{window}"], label=f"MA{window}", color=color, lw=1.2)
    for label, multiple in (levels or {}).items():
        axis.plot(
            x,
            history["ma5"] * multiple,
            label=label,
            color="#9333ea",
            linestyle="--",
            linewidth=1,
        )
    axis.axvspan(
        len(history) - trend_days - 0.5,
        len(history) - 0.5,
        color="#fde68a",
        alpha=0.24,
        label="形态检查窗口",
    )
    if split_days:
        axis.axvspan(
            len(history) - split_days - 0.5,
            len(history) - 0.5,
            color="#fb923c",
            alpha=0.2,
            label="加速检查末段",
        )
    limit_days = history["limit_up_hit"].to_numpy()
    axis.scatter(
        x[limit_days],
        history.loc[limit_days, "high"] * 1.015,
        marker="v",
        color="#dc2626",
        s=22,
        label="收盘涨停",
    )
    axis.set_title(title, fontsize=13, loc="left")
    axis.set_ylabel("研究日时点前复权价格 / 元")
    axis.autoscale_view()
    axis.legend(loc="upper left", ncol=4 if levels or split_days else 6, fontsize=9)
    axis.grid(alpha=0.15)
    volume_axis.bar(x, history["volume"] / 1e6, color=colors, width=0.65)
    volume_axis.set_ylabel("原始成交量\n百万股")
    ticks = np.linspace(0, len(history) - 1, min(9, len(history)), dtype=int)
    volume_axis.set_xticks(ticks, history["date"].dt.strftime("%Y-%m-%d").iloc[ticks])
    volume_axis.grid(axis="y", alpha=0.15)
    fig.savefig(path, dpi=130)
    plt.close(fig)


def save_interactive_chart(history: pd.DataFrame, title: str, path: Path) -> None:
    """导出可缩放 K 线，与 PNG 使用完全相同的历史窗口。"""
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.8, 0.2])
    dates = history["date"].dt.strftime("%Y-%m-%d")
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
    for window, color in MA_COLORS.items():
        fig.add_trace(
            go.Scatter(
                x=dates,
                y=history[f"ma{window}"],
                name=f"MA{window}",
                line={"color": color},
            ),
            row=1,
            col=1,
        )
    colors = np.where(history["close"] >= history["open"], "#dc2626", "#059669")
    fig.add_trace(
        go.Bar(x=dates, y=history["volume"], marker_color=colors, name="原始成交量"),
        row=2,
        col=1,
    )
    fig.update_layout(
        title=title,
        template="plotly_white",
        height=800,
        xaxis_rangeslider_visible=False,
        hovermode="x unified",
    )
    fig.update_xaxes(type="category", nticks=12)
    fig.write_html(path, include_plotlyjs="directory", config={"displaylogo": False})
