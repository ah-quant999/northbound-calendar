#!/usr/bin/env python3
"""
生成北向资金回测展示页面
"""

import json
import os
from datetime import datetime

HOLD_PERIODS = [5, 10, 20, 30, 60, 90]


def fmt_pct(v):
    if v is None:
        return "--"
    sign = "+" if v > 0 else ""
    return f"{sign}{v:.2f}%"


def fmt_ratio(v):
    if v is None:
        return "--"
    return f"{v:.2f}"


def color_cls(v):
    if v is None or v == 0:
        return ""
    return "up" if v > 0 else "down"


def generate_page(data_path, output_path):
    with open(data_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    overall = data["overall"]
    by_industry = data["by_industry"]
    by_bucket = data["by_net_buy_bucket"]
    config = data["config"]

    update_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # 总体统计表格
    overall_rows = ""
    for p in HOLD_PERIODS:
        key = str(p)
        d = overall.get(key, {})
        avg_cls = color_cls(d.get("avg_return_pct"))
        med_cls = color_cls(d.get("median_return_pct"))
        win_cls = "up" if d.get("win_rate", 0) >= 50 else "down"
        overall_rows += f"""<tr>
            <td>T+{p}</td>
            <td>{d.get('sample_count', 0)}</td>
            <td class="{win_cls}">{d.get('win_rate', 0):.2f}%</td>
            <td class="{avg_cls}">{fmt_pct(d.get('avg_return_pct'))}</td>
            <td class="{med_cls}">{fmt_pct(d.get('median_return_pct'))}</td>
            <td>{fmt_ratio(d.get('profit_loss_ratio'))}</td>
            <td class="up">{fmt_pct(d.get('max_return_pct'))}</td>
            <td class="down">{fmt_pct(d.get('min_return_pct'))}</td>
        </tr>"""

    # 行业统计（按T+20胜率排序，样本≥5）
    industry_list = []
    for name, periods in by_industry.items():
        if name == "未分类":
            continue
        # T+20 样本≥5才算
        t20 = periods.get("20", {})
        if t20.get("sample_count", 0) < 5:
            continue
        industry_list.append((name, periods))
    # 按T+20胜率排序
    industry_list.sort(key=lambda x: x[1].get("20", {}).get("win_rate", 0), reverse=True)

    industry_rows = ""
    for i, (name, periods) in enumerate(industry_list):
        t5 = periods.get("5", {})
        t10 = periods.get("10", {})
        t20 = periods.get("20", {})
        t30 = periods.get("30", {})
        t60 = periods.get("60", {})
        t90 = periods.get("90", {})
        industry_rows += f"""<tr>
            <td>{i+1}</td>
            <td class="ind-name">{name}</td>
            <td>{t20.get('sample_count', 0)}</td>
            <td class="{'up' if t5.get('win_rate',0)>=50 else 'down'}">{t5.get('win_rate', 0):.1f}%</td>
            <td class="{color_cls(t5.get('avg_return_pct'))}">{fmt_pct(t5.get('avg_return_pct'))}</td>
            <td class="{'up' if t10.get('win_rate',0)>=50 else 'down'}">{t10.get('win_rate', 0):.1f}%</td>
            <td class="{color_cls(t10.get('avg_return_pct'))}">{fmt_pct(t10.get('avg_return_pct'))}</td>
            <td class="{'up' if t20.get('win_rate',0)>=50 else 'down'}">{t20.get('win_rate', 0):.1f}%</td>
            <td class="{color_cls(t20.get('avg_return_pct'))}">{fmt_pct(t20.get('avg_return_pct'))}</td>
            <td class="{'up' if t60.get('win_rate',0)>=50 else 'down'}">{t60.get('win_rate', 0):.1f}%</td>
            <td class="{color_cls(t60.get('avg_return_pct'))}">{fmt_pct(t60.get('avg_return_pct'))}</td>
            <td class="{'up' if t90.get('win_rate',0)>=50 else 'down'}">{t90.get('win_rate', 0):.1f}%</td>
            <td class="{color_cls(t90.get('avg_return_pct'))}">{fmt_pct(t90.get('avg_return_pct'))}</td>
        </tr>"""

    # 档位统计
    bucket_names = ["1000-3000万", "3000-5000万", "5000万-1亿", "1亿以上"]
    bucket_rows = ""
    for bn in bucket_names:
        periods = by_bucket.get(bn, {})
        t20 = periods.get("20", {})
        t5 = periods.get("5", {})
        t10 = periods.get("10", {})
        t30 = periods.get("30", {})
        t60 = periods.get("60", {})
        t90 = periods.get("90", {})
        bucket_rows += f"""<tr>
            <td>{bn}</td>
            <td>{t5.get('sample_count', 0)}</td>
            <td class="{'up' if t5.get('win_rate',0)>=50 else 'down'}">{t5.get('win_rate', 0):.1f}%</td>
            <td class="{color_cls(t5.get('avg_return_pct'))}">{fmt_pct(t5.get('avg_return_pct'))}</td>
            <td class="{'up' if t10.get('win_rate',0)>=50 else 'down'}">{t10.get('win_rate', 0):.1f}%</td>
            <td class="{color_cls(t10.get('avg_return_pct'))}">{fmt_pct(t10.get('avg_return_pct'))}</td>
            <td class="{'up' if t20.get('win_rate',0)>=50 else 'down'}">{t20.get('win_rate', 0):.1f}%</td>
            <td class="{color_cls(t20.get('avg_return_pct'))}">{fmt_pct(t20.get('avg_return_pct'))}</td>
            <td class="{'up' if t30.get('win_rate',0)>=50 else 'down'}">{t30.get('win_rate', 0):.1f}%</td>
            <td class="{color_cls(t30.get('avg_return_pct'))}">{fmt_pct(t30.get('avg_return_pct'))}</td>
            <td class="{'up' if t60.get('win_rate',0)>=50 else 'down'}">{t60.get('win_rate', 0):.1f}%</td>
            <td class="{color_cls(t60.get('avg_return_pct'))}">{fmt_pct(t60.get('avg_return_pct'))}</td>
            <td class="{'up' if t90.get('win_rate',0)>=50 else 'down'}">{t90.get('win_rate', 0):.1f}%</td>
            <td class="{color_cls(t90.get('avg_return_pct'))}">{fmt_pct(t90.get('avg_return_pct'))}</td>
        </tr>"""

    # 胜率柱状图数据（总体）
    win_rate_data = [overall.get(str(p), {}).get("win_rate", 0) for p in HOLD_PERIODS]
    avg_return_data = [overall.get(str(p), {}).get("avg_return_pct", 0) for p in HOLD_PERIODS]
    max_wr = max(win_rate_data) if win_rate_data else 50
    max_ret = max(abs(r) for r in avg_return_data) if avg_return_data else 1

    # 胜率图
    wr_bars = ""
    for p, wr in zip(HOLD_PERIODS, win_rate_data):
        h = max(wr / 100 * 140 + 4, 4)
        wr_bars += f'<div class="bar-item"><div class="bar {"up" if wr>=50 else "down"}" style="height:{h:.0f}px"></div><div class="bar-val">{wr:.1f}%</div><div class="bar-label">T+{p}</div></div>'

    # 收益率图
    ret_bars = ""
    for p, ret in zip(HOLD_PERIODS, avg_return_data):
        h = max(abs(ret) / max_ret * 140 + 4, 4) if max_ret > 0 else 4
        ret_bars += f'<div class="bar-item"><div class="bar {"up" if ret>=0 else "down"}" style="height:{h:.0f}px"></div><div class="bar-val">{ret:+.1f}%</div><div class="bar-label">T+{p}</div></div>'

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>北向资金回测 - 龙虎榜北向席位</title>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif; background: #0d1117; color: #e6edf3; padding: 16px; }}
.container {{ max-width: 1200px; margin: 0 auto; }}
.breadcrumb {{ margin-bottom: 16px; font-size: 13px; color: #8b949e; }}
.breadcrumb a {{ color: #58a6ff; text-decoration: none; }}
.header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; padding-bottom: 12px; border-bottom: 1px solid #30363d; }}
.header h1 {{ font-size: 20px; font-weight: 600; }}
.update-time {{ color: #8b949e; font-size: 12px; }}
.section {{ background: #161b22; border-radius: 8px; padding: 16px; margin-bottom: 20px; border: 1px solid #30363d; }}
.section h2 {{ font-size: 16px; margin-bottom: 12px; }}
.section .subtitle {{ font-size: 12px; color: #8b949e; margin-bottom: 12px; }}

/* 参数卡片 */
.params {{ display: flex; gap: 20px; flex-wrap: wrap; margin-bottom: 12px; }}
.param-item {{ background: #21262d; padding: 8px 14px; border-radius: 6px; font-size: 13px; }}
.param-item .label {{ color: #8b949e; margin-right: 6px; }}
.param-item .value {{ font-weight: 500; }}

/* 表格 */
.data-table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
.data-table th {{ padding: 10px 8px; text-align: right; background: #21262d; color: #8b949e; font-weight: 500; border-bottom: 1px solid #30363d; white-space: nowrap; }}
.data-table th:first-child {{ text-align: left; }}
.data-table td {{ padding: 8px; border-bottom: 1px solid #21262d; text-align: right; }}
.data-table td:first-child {{ text-align: left; }}
.data-table tbody tr:hover {{ background: #1f252d; }}
.up {{ color: #f85149; }}
.down {{ color: #3fb950; }}
.ind-name {{ font-weight: 500; }}

/* 双栏图表 */
.chart-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 20px; margin-bottom: 20px; }}
.chart-card {{ background: #161b22; border-radius: 8px; padding: 16px; border: 1px solid #30363d; }}
.chart-card h3 {{ font-size: 14px; margin-bottom: 12px; }}
.bar-chart {{ display: flex; align-items: flex-end; height: 200px; gap: 8px; padding: 0 8px; border-bottom: 1px solid #30363d; justify-content: center; }}
.bar-item {{ flex: 1; display: flex; flex-direction: column; align-items: center; min-width: 0; max-width: 60px; }}
.bar {{ width: 100%; max-width: 36px; border-radius: 3px 3px 0 0; min-height: 3px; }}
.bar.up {{ background: #f85149; }}
.bar.down {{ background: #3fb950; }}
.bar-val {{ font-size: 11px; margin-top: 6px; font-weight: 500; }}
.bar-label {{ font-size: 10px; color: #8b949e; margin-top: 4px; }}

/* 折叠 */
.collapsible-header {{ cursor: pointer; display: flex; align-items: center; gap: 8px; user-select: none; }}
.collapsible-header .arrow {{ transition: transform 0.2s; font-size: 12px; color: #8b949e; }}
.collapsible-header.open .arrow {{ transform: rotate(90deg); }}
.collapsible-content {{ display: none; margin-top: 12px; }}
.collapsible-content.open {{ display: block; }}

.table-wrap {{ overflow-x: auto; }}

@media (max-width: 768px) {{
    .chart-grid {{ grid-template-columns: 1fr; }}
}}
</style>
</head>
<body>
<div class="container">
    <div class="breadcrumb">
        <a href="portal.html">← 返回首页</a>
    </div>
    <div class="header">
        <h1>📈 北向资金回测（龙虎榜北向席位）</h1>
        <div class="update-time">更新于 {update_time}</div>
    </div>

    <div class="section">
        <div class="params">
            <div class="param-item"><span class="label">回测区间</span><span class="value">{config['start_date']} ~ {config['end_date']}</span></div>
            <div class="param-item"><span class="label">买入门槛</span><span class="value">北向净买入≥{config['threshold_wan']:.0f}万</span></div>
            <div class="param-item"><span class="label">总信号数</span><span class="value">{data.get('signal_count', 0)}</span></div>
            <div class="param-item"><span class="label">持有周期</span><span class="value">T+5/10/20/30/60/90</span></div>
        </div>
    </div>

    <div class="chart-grid">
        <div class="chart-card">
            <h3>各周期胜率</h3>
            <div class="bar-chart">
                {wr_bars}
            </div>
        </div>
        <div class="chart-card">
            <h3>各周期平均收益率</h3>
            <div class="bar-chart">
                {ret_bars}
            </div>
        </div>
    </div>

    <div class="section">
        <h2>📊 总体统计</h2>
        <div class="table-wrap">
            <table class="data-table">
                <thead>
                    <tr>
                        <th>周期</th><th>样本数</th><th>胜率</th><th>平均收益</th>
                        <th>中位数</th><th>盈亏比</th><th>最大收益</th><th>最大亏损</th>
                    </tr>
                </thead>
                <tbody>
                    {overall_rows}
                </tbody>
            </table>
        </div>
    </div>

    <div class="section">
        <div class="collapsible-header" onclick="this.classList.toggle('open'); this.nextElementSibling.classList.toggle('open')">
            <span class="arrow">▶</span>
            <h2>🏭 分行业统计（按T+20胜率排序，样本≥5）</h2>
        </div>
        <div class="collapsible-content">
            <div class="table-wrap">
                <table class="data-table">
                    <thead>
                        <tr>
                            <th>#</th><th>行业</th><th>T+20样本</th>
                            <th>T+5胜率</th><th>T+5收益</th>
                            <th>T+10胜率</th><th>T+10收益</th>
                            <th>T+20胜率</th><th>T+20收益</th>
                            <th>T+60胜率</th><th>T+60收益</th>
                            <th>T+90胜率</th><th>T+90收益</th>
                        </tr>
                    </thead>
                    <tbody>
                        {industry_rows}
                    </tbody>
                </table>
            </div>
        </div>
    </div>

    <div class="section">
        <div class="collapsible-header" onclick="this.classList.toggle('open'); this.nextElementSibling.classList.toggle('open')">
            <span class="arrow">▶</span>
            <h2>💰 分净买入档位统计</h2>
        </div>
        <div class="collapsible-content">
            <div class="table-wrap">
                <table class="data-table">
                    <thead>
                        <tr>
                            <th>档位</th><th>T+5样本</th>
                            <th>T+5胜率</th><th>T+5收益</th>
                            <th>T+10胜率</th><th>T+10收益</th>
                            <th>T+20胜率</th><th>T+20收益</th>
                            <th>T+30胜率</th><th>T+30收益</th>
                            <th>T+60胜率</th><th>T+60收益</th>
                            <th>T+90胜率</th><th>T+90收益</th>
                        </tr>
                    </thead>
                    <tbody>
                        {bucket_rows}
                    </tbody>
                </table>
            </div>
        </div>
    </div>
</div>
</body>
</html>"""

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"✅ 页面已生成: {output_path}")


if __name__ == "__main__":
    generate_page("data/northbound_backtest.json", "northbound-backtest.html")
