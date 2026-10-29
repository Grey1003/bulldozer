"""无需服务器的本地HTML封装，图表库和研究数据随文件保存。"""

import json
from html import escape
from pathlib import Path

from plotly.offline import get_plotlyjs

ASSET_ROOT = Path(__file__).with_name("assets")


def write_local_page(
    path: Path, title: str, body: str, payload: dict, script: str
) -> None:
    """生成可离线打开的单文件HTML，安全嵌入JSON载荷。"""
    data = json.dumps(payload, ensure_ascii=False, allow_nan=False).replace(
        "<", "\\u003c"
    )
    css = (ASSET_ROOT / "inspection.css").read_text(encoding="utf-8")
    javascript = (ASSET_ROOT / script).read_text(encoding="utf-8")
    content = f"""<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(title)}</title><style>{css}</style><body>
<h1>{escape(title)}</h1>{body}
<script>{get_plotlyjs()}</script>
<script id="research-data" type="application/json">{data}</script>
<script>{javascript}</script></body></html>"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
