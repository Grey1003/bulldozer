"""导出候选股、淘汰原因、参数快照和离线看图入口。"""

from datetime import datetime
from html import escape
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from src.config.settings import Settings
from src.data.storage import write_json
from src.reporting.charts import save_interactive_chart, save_static_chart
from src.strategy.rules import RULE_LABELS
from src.strategy.screening import ScreenResult


def build_gallery(
    rows: pd.DataFrame,
    result: ScreenResult,
    directory: Path,
    settings: Settings,
) -> str:
    """为给定股票生成图片、交互文件和 HTML 卡片。"""
    cards = []
    for _, row in rows.iterrows():
        symbol = row["order_book_id"]
        history = result.histories[symbol].tail(settings.report.chart_bars)
        status = "入选" if row["selected"] else "未入选 / 参数复核"
        title = (
            f"{symbol} {row['symbol']} | {history['date'].iloc[-1].date()}"
            f" | {status}"
        )
        save_static_chart(
            history, title, directory / f"{symbol}.png", settings.strategy.trend_days
        )
        save_interactive_chart(history, title, directory / f"{symbol}.html")
        reason = row["failed_rules"] or "全部形态规则通过"
        cards.append(
            f"<article><h3>{escape(title)}</h3><p>{escape(reason)}</p>"
            f'<p>{settings.strategy.trend_days}日窗口涨幅：{row["trend_return"]:.1%}；'
            f'末日距MA5：{row["last_ma5_distance"]:.1%}；'
            f'贴线比例：{row["touch_ratio"]:.0%}；'
            f'近期涨停：{row["limit_up_count"]:.0f} 次</p>'
            f'<a href="charts/{symbol}.html">打开可缩放K线</a>'
            f'<img src="charts/{symbol}.png" loading="lazy" alt="{escape(symbol)} K线">'
            "</article>"
        )
    return "\n".join(cards) or "<p>本组无股票。</p>"


def export_report(
    result: ScreenResult,
    settings: Settings,
    requested: str,
    date: pd.Timestamp,
    data_metadata: dict,
) -> Path:
    """导出独立运行目录，保留全量诊断与未入选样本，不自动放宽阈值。"""
    stamp = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d_%H%M%S_%f")
    root = Path(settings.report.root) / f"{date:%Y%m%d}_{stamp}"
    chart_dir = root / "charts"
    chart_dir.mkdir(parents=True)
    diagnostics = result.diagnostics
    selected = diagnostics.loc[diagnostics["selected"]]
    near_misses = diagnostics.loc[
        diagnostics["eligible"] & ~diagnostics["selected"]
    ].head(settings.report.near_miss_count)
    diagnostics.to_csv(root / "diagnostics.csv", index=False, encoding="utf-8-sig")
    selected.to_csv(root / "selected.csv", index=False, encoding="utf-8-sig")
    write_json(root / "parameters.json", settings.to_dict())
    counts = {
        label: int(
            diagnostics.loc[diagnostics["eligible"], f"pass_{key}"].eq(False).sum()
        )
        for key, label in RULE_LABELS.items()
        if f"pass_{key}" in diagnostics
    }
    write_json(
        root / "run.json",
        {
            "requested_date": requested,
            "effective_date": str(date.date()),
            "selected_count": len(selected),
            "eligible_count": int(diagnostics["eligible"].sum()),
            "universe_count": len(diagnostics),
            "data": data_metadata,
            "rule_failure_counts": counts,
        },
    )
    selected_cards = build_gallery(selected, result, chart_dir, settings)
    near_cards = build_gallery(near_misses, result, chart_dir, settings)
    count_table = "".join(
        f"<tr><td>{escape(label)}</td><td>{count}</td></tr>"
        for label, count in counts.items()
    )
    html = f"""<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<title>推土机形态复核 {date.date()}</title><style>
body{{font:16px/1.7 'Microsoft YaHei',sans-serif;margin:32px auto;max-width:1200px;
padding:0 20px;background:#f4f6fa;color:#172033}}a{{color:#2457c5}}
article,section{{background:white;border-radius:12px;padding:24px;margin:24px 0}}
img{{width:100%;height:auto}}td,th{{padding:6px 20px;border-bottom:1px solid #ddd}}
.notice{{border-left:4px solid #d99400;padding:12px 18px;background:#fff7df}}
</style><h1>推土机 · 单日形态复核</h1>
<p>输入日期 {escape(requested)} → 实际交易日 {date.date()} 收盘后；
候选 {len(selected)} 只 / 具备资格 {int(diagnostics['eligible'].sum())} 只。</p>
<p class="notice">所有图和指标止于研究日。阈值是原文描述的可调量化近似，
二波与加速识别仍需人工检查。未计算回测收益或交易信号。</p>
<p><a href="selected.csv">入选清单</a> · <a href="diagnostics.csv">全市场逐项诊断</a> ·
<a href="parameters.json">本次参数</a> · <a href="run.json">运行信息</a></p>
<section><h2>规则未通过数量（同股可重复计数）</h2><table>{count_table}</table></section>
<h2>入选股票</h2>{selected_cards}
<h2>未入选复核样本</h2><p>按未通过规则数量升序展示，辅助调整阈值。</p>{near_cards}
<p>图中名称为下载时名称，仅作显示；ST 按研究日历史状态过滤。
次日均线估计仅假设次日价格等于研究日收盘，不使用次日实际行情。</p></html>"""
    (root / "index.html").write_text(html, encoding="utf-8")
    return root
