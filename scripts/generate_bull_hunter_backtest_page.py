#!/usr/bin/env python3
"""
大牛股猎手回测页面生成脚本

风格与 northbound-backtest.html、resonance-backtest.html 一致：
  - 暗色主题（#0d1117 背景、#161b22 卡片）
  - 卡片式布局
  - 红涨绿跌
  - 三列布局（对应三类信号），每列展示三个持有期的统计卡片
  - 下方放历史信号明细表（可展开）
"""

import json
import os
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT_DIR = SCRIPT_DIR.parent

HOLD_PERIODS = [5, 10, 20]

SIGNAL_TYPES = [
    {
        "key": "new_sectors",
        "title": "🚀 新赛道发现",
        "subtitle": "行业级 · 北向周度净买入TOP行业",
        "icon": "🚀",
        "name_field": "industry",
        "name_label": "行业",
    },
    {
        "key": "early_signals",
        "title": "📡 早期信号雷达",
        "subtitle": "个股级 · 资金进场初期信号",
        "icon": "📡",
        "name_field": "name",
        "name_label": "股票",
    },
    {
        "key": "core_targets",
        "title": "💎 核心共振标的",
        "subtitle": "个股级 · 机构+北向高端制造龙头",
        "icon": "💎",
        "name_field": "name",
        "name_label": "股票",
    },
]


def fmt_pct(v):
    if v is None:
        return "--"
    sign = "+" if v > 0 else ""
    return f"{sign}{v:.2f}%"


def fmt_ratio(v):
    if v is None or v == 0:
        return "--"
    if v >= 999:
        return "∞"
    return f"{v:.2f}"


def color_cls(v):
    if v is None or v == 0:
        return ""
    return "up" if v > 0 else "down"


def win_color_cls(wr):
    if wr is None or wr == 0:
        return ""
    return "up" if wr >= 50 else "down"


def generate_stat_card(stats, period, label):
    """生成单个持有期的统计卡片"""
    s = stats.get(str(period), {})
    if not s or s.get("sample_count", 0) == 0:
        return f"""
            <div class="stat-card">
                <div class="stat-period">{label}</div>
                <div class="stat-empty">样本不足</div>
            </div>
        """
    avg_cls = color_cls(s.get("avg_return_pct"))
    win_cls = win_color_cls(s.get("win_rate"))
    return f"""
        <div class="stat-card">
            <div class="stat-period">{label}</div>
            <div class="stat-return {avg_cls}">{fmt_pct(s.get('avg_return_pct'))}</div>
            <div class="stat-grid">
                <div class="stat-item">
                    <div class="stat-label">胜率</div>
                    <div class="stat-value {win_cls}">{s.get('win_rate', 0):.1f}%</div>
                </div>
                <div class="stat-item">
                    <div class="stat-label">样本</div>
                    <div class="stat-value">{s.get('sample_count', 0)}</div>
                </div>
                <div class="stat-item">
                    <div class="stat-label">盈亏比</div>
                    <div class="stat-value">{fmt_ratio(s.get('profit_loss_ratio'))}</div>
                </div>
                <div class="stat-item">
                    <div class="stat-label">最大收益</div>
                    <div class="stat-value up">{fmt_pct(s.get('max_return_pct'))}</div>
                </div>
                <div class="stat-item">
                    <div class="stat-label">最大亏损</div>
                    <div class="stat-value down">{fmt_pct(s.get('min_return_pct'))}</div>
                </div>
                <div class="stat-item">
                    <div class="stat-label">中位数</div>
                    <div class="stat-value {color_cls(s.get('median_return_pct'))}">{fmt_pct(s.get('median_return_pct'))}</div>
                </div>
            </div>
        </div>
    """


def generate_detail_table(details, name_field, name_label, period):
    """生成历史信号明细表格"""
    if not details:
        return '<div class="detail-empty">暂无数据</div>'

    rows = ""
    for i, s in enumerate(details):
        name = s.get(name_field, s.get("name", ""))
        code = s.get("code", "")
        ret_cls = color_cls(s.get("return_pct"))
        signal_type = s.get("signal_type", s.get("tag", ""))
        amount = s.get("amount_wan", s.get("week_net", 0))
        amount_str = f"{amount/10000:.2f}亿" if amount >= 10000 else f"{amount:.0f}万"
        industry = s.get("industry", "")
        rows += f"""<tr>
            <td>{i+1}</td>
            <td>{s.get('date', '')}</td>
            <td class="name-cell">
                {name}
                {f'<span class="code">{code}</span>' if code else ''}
            </td>
            <td>{signal_type}</td>
            <td>{industry if industry else '--'}</td>
            <td>{amount_str}</td>
            <td class="{ret_cls}">{fmt_pct(s.get('return_pct'))}</td>
        </tr>"""

    return f"""
    <div class="table-wrap">
        <table class="data-table">
            <thead>
                <tr>
                    <th>#</th>
                    <th>信号日期</th>
                    <th>{name_label}</th>
                    <th>信号类型</th>
                    <th>行业</th>
                    <th>金额</th>
                    <th>T+{period}收益</th>
                </tr>
            </thead>
            <tbody>
                {rows}
            </tbody>
        </table>
    </div>
    """


def generate_page(data_path, output_path):
    with open(data_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    config = data.get("config", {})
    bench = data.get("benchmark_hs300", {})
    update_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # 三列布局
    columns_html = ""
    for st in SIGNAL_TYPES:
        key = st["key"]
        section_data = data.get(key, {})
        stats = section_data.get("stats", {})
        total = section_data.get("total_signals", 0)
        details = section_data.get("details", {})

        cards_html = ""
        for p in HOLD_PERIODS:
            cards_html += generate_stat_card(stats, p, f"T+{p}")

        # 明细（按T+5展示，默认收起）
        t5_details = details.get("5", [])
        detail_html = generate_detail_table(t5_details, st["name_field"], st["name_label"], 5)

        columns_html += f"""
        <div class="signal-column">
            <div class="section">
                <div class="section-header">
                    <div>
                        <h2>{st['title']}</h2>
                        <div class="subtitle">{st['subtitle']}</div>
                    </div>
                    <div class="total-badge">共 {total} 个信号</div>
                </div>
                <div class="stat-cards">
                    {cards_html}
                </div>
            </div>
            <div class="section">
                <div class="collapsible-header" onclick="this.classList.toggle('open'); this.nextElementSibling.classList.toggle('open')">
                    <span class="arrow">▶</span>
                    <h3>📋 历史信号明细（T+5收益排序）</h3>
                </div>
                <div class="collapsible-content">
                    {detail_html}
                </div>
            </div>
        </div>
        """

    # 基准对比
    bench_rows = ""
    for p in HOLD_PERIODS:
        b = bench.get(str(p), {})
        bench_rows += f"""<tr>
            <td>T+{p}</td>
            <td>{b.get('sample_count', 0)}</td>
            <td class="{color_cls(b.get('avg_return_pct'))}">{fmt_pct(b.get('avg_return_pct'))}</td>
        </tr>"""

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>大牛股猎手回测 - 历史胜率与收益表现</title>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif; background: #0d1117; color: #e6edf3; padding: 16px; }}
.container {{ max-width: 1400px; margin: 0 auto; }}
.breadcrumb {{ margin-bottom: 16px; font-size: 13px; color: #8b949e; }}
.breadcrumb a {{ color: #58a6ff; text-decoration: none; }}
.header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; padding-bottom: 12px; border-bottom: 1px solid #30363d; }}
.header h1 {{ font-size: 20px; font-weight: 600; }}
.update-time {{ color: #8b949e; font-size: 12px; }}
.section {{ background: #161b22; border-radius: 8px; padding: 16px; margin-bottom: 20px; border: 1px solid #30363d; }}
.section h2 {{ font-size: 16px; margin-bottom: 4px; }}
.section h3 {{ font-size: 14px; }}
.section-header {{ display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 12px; }}
.subtitle {{ font-size: 12px; color: #8b949e; }}
.total-badge {{ background: #21262d; padding: 4px 10px; border-radius: 12px; font-size: 12px; color: #8b949e; white-space: nowrap; }}

/* 参数卡片 */
.params {{ display: flex; gap: 20px; flex-wrap: wrap; margin-bottom: 12px; }}
.param-item {{ background: #21262d; padding: 8px 14px; border-radius: 6px; font-size: 13px; }}
.param-item .label {{ color: #8b949e; margin-right: 6px; }}
.param-item .value {{ font-weight: 500; }}
.warn-note {{ background: rgba(255,122,0,0.1); border: 1px solid rgba(255,122,0,0.3); color: #ff7a00; padding: 10px 14px; border-radius: 6px; font-size: 13px; margin-bottom: 16px; }}

/* 三列布局 */
.three-col {{ display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 20px; }}
.signal-column {{ display: flex; flex-direction: column; }}
.signal-column .section {{ flex: none; }}

/* 统计卡片 */
.stat-cards {{ display: flex; flex-direction: column; gap: 10px; }}
.stat-card {{ background: #21262d; border-radius: 6px; padding: 12px; border: 1px solid #30363d; }}
.stat-period {{ font-size: 12px; color: #8b949e; margin-bottom: 6px; }}
.stat-return {{ font-size: 22px; font-weight: 700; margin-bottom: 10px; }}
.stat-grid {{ display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 6px 10px; }}
.stat-item {{ text-align: center; }}
.stat-label {{ font-size: 11px; color: #8b949e; margin-bottom: 2px; }}
.stat-value {{ font-size: 13px; font-weight: 500; }}
.stat-empty {{ color: #484f58; font-size: 13px; text-align: center; padding: 12px 0; }}

/* 表格 */
.data-table {{ width: 100%; border-collapse: collapse; font-size: 12px; }}
.data-table th {{ padding: 8px 6px; text-align: right; background: #21262d; color: #8b949e; font-weight: 500; border-bottom: 1px solid #30363d; white-space: nowrap; }}
.data-table th:first-child, .data-table th:nth-child(2), .data-table th:nth-child(3) {{ text-align: left; }}
.data-table td {{ padding: 6px; border-bottom: 1px solid #21262d; text-align: right; }}
.data-table td:first-child, .data-table td:nth-child(2), .data-table td:nth-child(3) {{ text-align: left; }}
.data-table tbody tr:hover {{ background: #1f252d; }}
.up {{ color: #f85149; }}
.down {{ color: #3fb950; }}
.name-cell {{ font-weight: 500; }}
.name-cell .code {{ color: #8b949e; font-size: 11px; margin-left: 4px; font-weight: normal; }}

/* 折叠 */
.collapsible-header {{ cursor: pointer; display: flex; align-items: center; gap: 8px; user-select: none; padding: 4px 0; }}
.collapsible-header .arrow {{ transition: transform 0.2s; font-size: 12px; color: #8b949e; }}
.collapsible-header.open .arrow {{ transform: rotate(90deg); }}
.collapsible-content {{ display: none; margin-top: 12px; }}
.collapsible-content.open {{ display: block; }}
.detail-empty {{ color: #484f58; text-align: center; padding: 20px; font-size: 13px; }}

.table-wrap {{ overflow-x: auto; }}

@media (max-width: 1200px) {{
    .three-col {{ grid-template-columns: 1fr; }}
}}
</style>
</head>
<body>
<div class="container">
    <div class="breadcrumb">
        <a href="portal.html">← 返回首页</a>
    </div>
    <div class="header">
        <h1>🐂 大牛股猎手 · 历史回测</h1>
        <div class="update-time">更新于 {update_time}</div>
    </div>

    <div class="section">
        <div class="params">
            <div class="param-item"><span class="label">回测区间</span><span class="value">{config.get('start_date', '--')} ~ {config.get('end_date', '--')}</span></div>
            <div class="param-item"><span class="label">持有周期</span><span class="value">T+5 / T+10 / T+20</span></div>
            <div class="param-item"><span class="label">新赛道信号</span><span class="value">{config.get('new_sector_count', 0)}</span></div>
            <div class="param-item"><span class="label">早期信号</span><span class="value">{config.get('early_signal_count', 0)}</span></div>
            <div class="param-item"><span class="label">核心标的</span><span class="value">{config.get('core_target_count', 0)}</span></div>
        </div>
        <div class="warn-note">
            ⚠️ {config.get('note', '历史数据有限，回测结果仅供参考')}
        </div>
    </div>

    <!-- 基准对比 -->
    <div class="section">
        <h2>📊 沪深300基准收益</h2>
        <div class="table-wrap" style="max-width: 400px;">
            <table class="data-table">
                <thead>
                    <tr><th>周期</th><th>样本数</th><th>平均收益</th></tr>
                </thead>
                <tbody>
                    {bench_rows}
                </tbody>
            </table>
        </div>
    </div>

    <!-- 三列：三类信号 -->
    <div class="three-col">
        {columns_html}
    </div>
</div>
</body>
</html>"""

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"✅ 页面已生成: {output_path}")


if __name__ == "__main__":
    generate_page(
        str(ROOT_DIR / "data" / "bull_hunter_backtest.json"),
        str(ROOT_DIR / "bull-hunter-backtest.html"),
    )
