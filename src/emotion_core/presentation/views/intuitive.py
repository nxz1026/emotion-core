"""直观层视图：今日结论 + 推荐 + 买点。"""
from __future__ import annotations


def render(context: dict) -> str:
    """渲染直观层 HTML 片段。"""
    phase = context.get("phase", "未知")
    recommendation = context.get("recommendation")
    if recommendation:
        action = f"<p>推荐：{recommendation.get('code', '')}</p>"
    else:
        action = "<p>今日无推荐</p>"
    return f"""
    <div class="card">
        <h2>今日结论</h2>
        <p>市场处于<strong>{phase}</strong>阶段</p>
        {action}
    </div>
    """
