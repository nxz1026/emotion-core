"""逻辑层视图：市场参数展示。"""
from __future__ import annotations


def render(context: dict) -> str:
    """渲染逻辑层 HTML 片段。"""
    stats = context.get("stats", {})
    rows = "".join(f"<tr><td>{k}</td><td>{v}</td></tr>" for k, v in stats.items())
    return f"""
    <div class="card">
        <h2>市场参数</h2>
        <table><thead><tr><th>指标</th><th>值</th></tr></thead>
        <tbody>{rows}</tbody></table>
    </div>
    """
