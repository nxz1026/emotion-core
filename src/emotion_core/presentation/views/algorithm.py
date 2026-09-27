"""算法层视图：计算公式 + 数据来源。"""
from __future__ import annotations


def render(context: dict) -> str:
    """渲染算法层 HTML 片段。"""
    formulas = context.get("formulas", {})
    rows = "".join(
        f"<tr><td>{k}</td><td><code>{v}</code></td></tr>" for k, v in formulas.items()
    )
    return f"""
    <div class="card">
        <h2>算法口径</h2>
        <table><thead><tr><th>指标</th><th>计算方式</th></tr></thead>
        <tbody>{rows}</tbody></table>
    </div>
    """
