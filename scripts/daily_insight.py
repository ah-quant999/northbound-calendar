#!/usr/bin/env python3
"""
每日市场洞察 — 从机游信号分析 + 北向分析页面提取数据，生成一屏式洞察页面。

页面结构（深色主题，卡片式，一屏看完）：
  顶部：市场温度（一句话总评 + 关键数据）
  左列：短线·机游共振（最强共振 + 风险警示 + 结论）
  右列：中长线·北向资金（最强行业 + 连续加仓龙头 + 结论）
  底部：明日关注点

数据来源：
  - jiyou-signal-analysis.html  → signalData, continuousData
  - northbound-analysis.html   → nbDailyData, nbAnalysis

用法：
  python3 scripts/daily_insight.py --jiyou-html jiyou-signal-analysis.html --nb-html northbound-analysis.html --output daily-insight.html --repo-dir .
  python3 scripts/daily_insight.py --date 2026-07-17
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path


# ─── 日志 ───────────────────────────────────────────────
def log_info(msg: str) -> None:
    print(f"🟢 {msg}")

def log_warn(msg: str) -> None:
    print(f"🟡 {msg}")

def log_error(msg: str) -> None:
    print(f"🔴 {msg}", file=sys.stderr)


# ─── 数据提取 ──────────────────────────────────────────
def extract_json_var(html_content: str, var_name: str) -> dict | list | None:
    """从HTML中提取指定JS变量的JSON值（支持对象和数组）"""
    # 匹配对象 {...}
    patterns = [
        rf"var\s+{re.escape(var_name)}\s*=\s*(\{{.*?\}})\s*;",
        rf"(?:let|const)?\s*{re.escape(var_name)}\s*=\s*(\{{.*?\}})\s*;",
        rf"var\s+{re.escape(var_name)}\s*=\s*(\[.*?\])\s*;",
        rf"(?:let|const)?\s*{re.escape(var_name)}\s*=\s*(\[.*?\])\s*;",
    ]
    for pat in patterns:
        m = re.search(pat, html_content, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(1))
            except json.JSONDecodeError:
                continue
    return None


def load_html(path: str) -> str:
    if not os.path.isfile(path):
        return ""
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


# ─── 洞察计算 ──────────────────────────────────────────
def fmt_wan(val: float) -> str:
    """万元格式化：<1亿用万，>=1亿用亿"""
    if val is None:
        return "—"
    abs_val = abs(val)
    sign = "+" if val > 0 else ("-" if val < 0 else "")
    if abs_val >= 10000:
        return f"{sign}{abs_val / 10000:.2f}亿"
    return f"{sign}{abs_val:,.0f}万"


def val_sign(val_str: str) -> str:
    """根据金额字符串返回 up/down 类名"""
    if val_str.startswith("-"):
        return "down"
    return "up"


def compute_market_temp(signal_data: dict, nb_daily: dict, latest_date: str,
                        continuous_data: dict, nb_analysis: dict) -> dict:
    """
    计算市场温度
    返回：{ level, label, score, summary, metrics: [...] }
    """
    latest_jiyou = signal_data.get(latest_date, {}) if signal_data else {}
    latest_nb = nb_daily.get(latest_date, {}) if nb_daily else {}
    basic = latest_jiyou.get("basic_signals", {})
    stats = latest_jiyou.get("stats", {})
    nb_total_net = latest_nb.get("total_net_wan", 0)

    # 热度评分（0-100）
    score = 50  # 中性起点

    # 北向净额
    if nb_total_net > 500000:
        score += 20
    elif nb_total_net > 200000:
        score += 10
    elif nb_total_net > 0:
        score += 5
    elif nb_total_net < -300000:
        score -= 20
    elif nb_total_net < -100000:
        score -= 10
    elif nb_total_net < 0:
        score -= 5

    # 机游共振买入/卖出数量
    res_buy = len(basic.get("resonance_buy", []))
    res_sell = len(basic.get("resonance_sell", []))
    if res_buy >= 5:
        score += 15
    elif res_buy >= 3:
        score += 10
    elif res_buy >= 1:
        score += 5
    if res_sell >= 5:
        score -= 15
    elif res_sell >= 3:
        score -= 10
    elif res_sell >= 1:
        score -= 5

    # 龙虎榜热度（个股数量）
    stock_count = stats.get("total_billboard_stocks", 0)
    if stock_count >= 80:
        score += 10
    elif stock_count >= 60:
        score += 5
    elif stock_count < 30:
        score -= 10

    score = max(0, min(100, score))

    if score >= 75:
        level = "hot"
        label = "🔥 火热"
        summary = "市场情绪高涨，多方力量占优，短线机会活跃。"
    elif score >= 60:
        level = "warm"
        label = "☀️ 偏暖"
        summary = "市场情绪偏暖，结构性机会较多，建议顺势而为。"
    elif score >= 45:
        level = "neutral"
        label = "🌤️ 中性"
        summary = "市场情绪中性，多空相对平衡，精选个股为主。"
    elif score >= 30:
        level = "cool"
        label = "⛅ 偏冷"
        summary = "市场情绪偏冷，观望氛围较重，注意控制仓位。"
    else:
        level = "cold"
        label = "❄️ 寒冷"
        summary = "市场情绪低迷，资金流出明显，建议谨慎防御。"

    metrics = [
        {"label": "北向净买入", "value": fmt_wan(nb_total_net), "trend": "up" if nb_total_net > 0 else "down"},
        {"label": "机游共振买入", "value": f"{res_buy}只", "trend": "up" if res_buy > res_sell else "down"},
        {"label": "龙虎榜个股", "value": f"{stock_count}只", "trend": "flat"},
    ]

    return {
        "level": level,
        "label": label,
        "score": score,
        "summary": summary,
        "metrics": metrics,
    }


def compute_jiyou_insight(signal_data: dict, continuous_data: dict, latest_date: str) -> dict:
    """
    短线·机游共振洞察
    """
    latest = signal_data.get(latest_date, {}) if signal_data else {}
    basic = latest.get("basic_signals", {})
    sub = latest.get("sub_signals", {})
    industry = latest.get("industry", {})

    # 最强共振（买入）
    res_buy = basic.get("resonance_buy", [])[:2]

    # 风险警示（共振卖出 + 机构派发）
    res_sell = basic.get("resonance_sell", [])[:2]
    inst_distribute = sub.get("inst_distribute", [])[:1]
    risk_items = []
    for s in res_sell[:2]:
        risk_items.append({
            "code": s["code"],
            "name": s["name"],
            "reason": "机游共振卖出",
            "detail": f"机构{fmt_wan(s['inst_net_wan'])} 游资{fmt_wan(s['youzi_net_wan'])}",
            "severity": "high",
        })
    for s in inst_distribute:
        net = s.get("inst_net_wan", s.get("net_wan", 0))
        risk_items.append({
            "code": s["code"],
            "name": s["name"],
            "reason": "机构派发",
            "detail": f"机构净卖{fmt_wan(abs(net) if net < 0 else net)}",
            "severity": "mid",
        })
    risk_items = risk_items[:2]

    # 游资接力榜
    youzi_relay = continuous_data.get("youzi_relay", [])[:2] if continuous_data else []

    # 结论
    res_buy_count = len(basic.get("resonance_buy", []))
    res_sell_count = len(basic.get("resonance_sell", []))
    youzi_solo = len(sub.get("youzi_solo_buy", []))
    inst_solo = len(sub.get("inst_solo_buy", []))

    if res_buy_count >= 3 and res_buy_count > res_sell_count:
        conclusion = f"短线情绪偏强，机游共振买入{res_buy_count}只，游资活跃度高，可关注主流热点龙头。"
    elif res_sell_count >= 3 and res_sell_count > res_buy_count:
        conclusion = f"短线风险偏高，机游共振卖出{res_sell_count}只，资金分歧加大，宜降低仓位规避风险。"
    elif res_buy_count == 0 and res_sell_count == 0:
        conclusion = "短线机游无明显共振方向，游资独立行情为主，建议精选个股、快进快出。"
    else:
        conclusion = f"短线多空交织，共振买入{res_buy_count}只、卖出{res_sell_count}只，关注有持续接力的强势股。"

    return {
        "conclusion": conclusion,
        "top_resonance": [
            {
                "code": s["code"],
                "name": s["name"],
                "inst_net": fmt_wan(s["inst_net_wan"]),
                "youzi_net": fmt_wan(s["youzi_net_wan"]),
                "change_pct": f"{s['change_pct']:+.2f}%",
                "total": fmt_wan(s["inst_net_wan"] + s["youzi_net_wan"]),
            }
            for s in res_buy
        ],
        "risk_warning": risk_items,
        "youzi_relay": [
            {
                "code": s["code"],
                "name": s["name"],
                "relay_days": f"{s['relay_days']}天",
                "total_net": fmt_wan(s["total_net_wan"]),
            }
            for s in youzi_relay
        ],
    }


def compute_northbound_insight(nb_analysis: dict, nb_daily: dict, latest_date: str) -> dict:
    """
    中长线·北向资金洞察
    """
    week = nb_analysis.get("week", {}) if nb_analysis else {}
    latest = nb_daily.get(latest_date, {}) if nb_daily else {}

    # 最强行业（过滤"未分类"）
    industry_trend = week.get("industry_trend", {})
    top_buy_industry = industry_trend.get("top_buy", [])
    filtered = [i for i in top_buy_industry if i.get("industry") and i["industry"] != "未分类"]
    top_industry = filtered[0] if filtered else (top_buy_industry[0] if top_buy_industry else None)

    # 连续加仓龙头
    continuous_buy = week.get("continuous_buy", [])[:2]

    # 北向+机构共振
    resonance = week.get("resonance", [])[:1]

    # 北向净额
    total_net = latest.get("total_net_wan", 0)
    stocks = latest.get("stocks", [])
    buy_count = sum(1 for s in stocks if s.get("net_wan", 0) > 0)
    sell_count = len(stocks) - buy_count

    # 结论
    if total_net > 300000:
        conclusion = f"北向资金大幅净流入{fmt_wan(total_net)}，中长线资金进场积极，可重点关注连续加仓方向。"
    elif total_net > 0:
        conclusion = f"北向资金小幅净流入{fmt_wan(total_net)}，中长线资金态度偏暖，精选优质标的布局。"
    elif total_net > -100000:
        conclusion = f"北向资金小幅净流出{fmt_wan(abs(total_net))}，中长线资金小幅调仓，关注结构性机会。"
    else:
        conclusion = f"北向资金大幅净流出{fmt_wan(abs(total_net))}，中长线资金撤退明显，宜控制仓位耐心等待。"

    return {
        "conclusion": conclusion,
        "top_industry": {
            "name": top_industry["industry"],
            "net_buy": fmt_wan(top_industry["net_buy_wan"]),
        } if top_industry else None,
        "continuous_buy": [
            {
                "code": s["code"],
                "name": s["name"],
                "streak_days": f"{s['streak_days']}天",
                "total_net": fmt_wan(s["total_net_wan"]),
                "change_pct": f"{s['change_pct']:+.2f}%",
            }
            for s in continuous_buy
        ],
        "resonance": [
            {
                "code": s["code"],
                "name": s["name"],
                "nb_net": fmt_wan(s["nb_net_wan"]),
                "inst_net": fmt_wan(s["inst_net_wan"]),
                "strength": fmt_wan(s["resonance_strength"]),
            }
            for s in resonance
        ],
        "daily_detail": {
            "total_net": fmt_wan(total_net),
            "buy_count": buy_count,
            "sell_count": sell_count,
        }
    }


def compute_focus_points(jiyou_insight: dict, nb_insight: dict, market_temp: dict) -> list:
    """生成明日关注点"""
    points = []

    # 关注点1：最强方向
    if jiyou_insight.get("top_resonance"):
        top = jiyou_insight["top_resonance"][0]
        points.append({
            "icon": "⚡",
            "title": "短线关注",
            "content": f"机游共振最强标的 {top['name']}({top['code']})，合计净买{top['total']}，观察次日溢价持续性。",
        })
    elif nb_insight.get("continuous_buy"):
        top = nb_insight["continuous_buy"][0]
        points.append({
            "icon": "📈",
            "title": "中长线关注",
            "content": f"北向连续加仓龙头 {top['name']}({top['code']})，连续{top['streak_days']}，累计净买{top['total_net']}。",
        })

    # 关注点2：风险或行业
    if jiyou_insight.get("risk_warning"):
        risk = jiyou_insight["risk_warning"][0]
        points.append({
            "icon": "⚠️",
            "title": "风险警示",
            "content": f"{risk['name']}({risk['code']})出现{risk['reason']}，{risk['detail']}，警惕回调风险。",
        })
    elif nb_insight.get("top_industry"):
        ind = nb_insight["top_industry"]
        points.append({
            "icon": "🏭",
            "title": "行业方向",
            "content": f"北向资金本周最看好 {ind['name']} 板块，净买入{ind['net_buy']}，可关注板块龙头。",
        })

    if not points:
        points.append({
            "icon": "📊",
            "title": "市场观察",
            "content": "今日市场信号清淡，建议观望为主，等待明确方向信号。",
        })

    return points[:2]


# ─── HTML生成 ──────────────────────────────────────────
def generate_html(market_temp: dict, jiyou_insight: dict, nb_insight: dict,
                  focus_points: list, latest_date: str, update_time: str) -> str:
    """生成每日洞察HTML页面"""

    temp_colors = {
        "hot": "#f85149",
        "warm": "#ff7a00",
        "neutral": "#ff7a00",
        "cool": "#ffa940",
        "cold": "#58a6ff",
    }
    temp_color = temp_colors.get(market_temp["level"], "#ff7a00")

    # 指标
    metrics_html = ""
    for m in market_temp["metrics"]:
        tc = "up" if m["trend"] == "up" else ("down" if m["trend"] == "down" else "")
        metrics_html += f"""
            <div class="metric-card">
                <div class="metric-label">{m['label']}</div>
                <div class="metric-value {tc}">{m['value']}</div>
            </div>"""

    # 机游最强共振
    jiyou_buy_html = ""
    if jiyou_insight.get("top_resonance"):
        for s in jiyou_insight["top_resonance"]:
            inst_cls = val_sign(s['inst_net'])
            youzi_cls = val_sign(s['youzi_net'])
            chg_cls = val_sign(s['change_pct'])
            jiyou_buy_html += f"""
            <div class="stock-item">
                <div class="stock-name">{s['name']} <span class="stock-code">{s['code']}</span></div>
                <div class="stock-detail">
                    <span class="tag {inst_cls}">机构 {s['inst_net']}</span>
                    <span class="tag {youzi_cls}">游资 {s['youzi_net']}</span>
                </div>
                <div class="stock-meta">
                    <span>合计 {s['total']}</span>
                    <span class="change-pct {chg_cls}">{s['change_pct']}</span>
                </div>
            </div>"""
    else:
        jiyou_buy_html = '<div class="empty-text">今日无明确共振买入</div>'

    # 机游风险
    jiyou_risk_html = ""
    if jiyou_insight.get("risk_warning"):
        for s in jiyou_insight["risk_warning"]:
            jiyou_risk_html += f"""
            <div class="risk-item">
                <div class="risk-name">{s['name']} <span class="stock-code">{s['code']}</span></div>
                <div class="risk-reason">{s['reason']}</div>
                <div class="risk-detail">{s['detail']}</div>
            </div>"""
    else:
        jiyou_risk_html = '<div class="empty-text">暂无明显风险信号</div>'

    # 游资接力
    jiyou_relay_html = ""
    if jiyou_insight.get("youzi_relay"):
        for s in jiyou_insight["youzi_relay"]:
            net_cls = val_sign(s['total_net'])
            jiyou_relay_html += f"""
            <div class="relay-item">
                <span class="relay-name">{s['name']}</span>
                <span class="relay-badge">接力{s['relay_days']}</span>
                <span class="relay-net {net_cls}">{s['total_net']}</span>
            </div>"""

    # 北向行业
    nb_industry_html = ""
    if nb_insight.get("top_industry"):
        ind = nb_insight["top_industry"]
        ind_cls = val_sign(ind['net_buy'])
        nb_industry_html = f"""
            <div class="industry-highlight">
                <div class="industry-name">{ind['name']}</div>
                <div class="industry-net {ind_cls}">周净买入 {ind['net_buy']}</div>
            </div>"""
    else:
        nb_industry_html = '<div class="empty-text">行业数据待补充</div>'

    # 北向连续加仓
    nb_continuous_html = ""
    if nb_insight.get("continuous_buy"):
        for s in nb_insight["continuous_buy"]:
            chg_cls = val_sign(s['change_pct'])
            net_cls = val_sign(s['total_net'])
            nb_continuous_html += f"""
            <div class="stock-item">
                <div class="stock-name">{s['name']} <span class="stock-code">{s['code']}</span></div>
                <div class="stock-detail">
                    <span class="tag streak">连续加仓{s['streak_days']}</span>
                    <span class="tag {net_cls}">累计{s['total_net']}</span>
                </div>
                <div class="stock-meta">
                    <span>区间涨幅</span>
                    <span class="change-pct {chg_cls}">{s['change_pct']}</span>
                </div>
            </div>"""
    else:
        nb_continuous_html = '<div class="empty-text">暂无连续加仓标的</div>'

    # 北向+机构共振
    nb_resonance_html = ""
    if nb_insight.get("resonance"):
        for s in nb_insight["resonance"]:
            nb_resonance_html += f"""
            <div class="resonance-item">
                <div class="res-name">{s['name']} <span class="stock-code">{s['code']}</span></div>
                <div class="res-detail">
                    <span class="mini-tag">北向{s['nb_net']}</span>
                    <span class="mini-tag">机构{s['inst_net']}</span>
                </div>
                <div class="res-strength">共振强度 <b>{s['strength']}</b></div>
            </div>"""

    # 明日关注
    focus_html = ""
    for p in focus_points:
        focus_html += f"""
            <div class="focus-item">
                <div class="focus-icon">{p['icon']}</div>
                <div class="focus-content">
                    <div class="focus-title">{p['title']}</div>
                    <div class="focus-desc">{p['content']}</div>
                </div>
            </div>"""

    relay_section = ""
    if jiyou_relay_html:
        relay_section = f'<div class="section-label">🔄 游资接力</div>\n            {jiyou_relay_html}'

    resonance_section = ""
    if nb_resonance_html:
        resonance_section = f'<div class="section-label">🤝 北向+机构共振</div>\n            {nb_resonance_html}'

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>每日市场洞察</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', sans-serif;
            background: #0d1117;
            min-height: 100vh;
            padding: 16px;
            color: #c9d1d9;
        }}
        .container {{
            max-width: 1400px;
            margin: 0 auto;
            background: #161b22;
            border-radius: 12px;
            border: 1px solid #ff7a00;
            padding: 30px;
            box-shadow: 0 0 20px rgba(255, 122, 0, 0.1);
        }}
        .header {{
            text-align: center;
            padding: 20px 0;
            border-bottom: 1px solid #30363d;
            margin-bottom: 25px;
        }}
        .header h1 {{
            font-size: 28px;
            font-weight: 600;
            color: #ff7a00;
            margin-bottom: 8px;
            letter-spacing: 2px;
        }}
        .header .subtitle {{
            color: #8b949e;
            font-size: 13px;
        }}
        /* 图例 */
        .legend {{
            display: flex;
            flex-wrap: wrap;
            gap: 18px;
            padding: 12px 16px;
            background: #161b22;
            border: 1px solid #ff7a00;
            border-radius: 10px;
            margin-bottom: 18px;
        }}
        .legend-item {{
            display: flex;
            align-items: center;
            gap: 6px;
            font-size: 12px;
            color: #8b949e;
        }}
        .legend-dot {{
            width: 10px;
            height: 10px;
            border-radius: 50%;
            display: inline-block;
        }}
        .legend-dot.buy {{ background: #f85149; }}
        .legend-dot.sell {{ background: #3fb950; }}
        .legend-dot.temp {{ background: #ff7a00; }}
        .legend-dot.jiyou {{ background: #f85149; }}
        .legend-dot.nb {{ background: #3fb950; }}
        .legend-dot.focus {{ background: #ff7a00; }}
        .update-time {{
            text-align: center;
            color: #6e7681;
            font-size: 11px;
            margin-bottom: 14px;
        }}
        /* 市场温度 */
        .temp-card {{
            background: linear-gradient(135deg, #161b22 0%, #1c2128 100%);
            border: 1px solid #ff7a00;
            border-radius: 12px;
            padding: 18px 24px;
            margin-bottom: 14px;
            display: flex;
            align-items: center;
            gap: 24px;
            box-shadow: 0 0 15px rgba(255, 122, 0, 0.08);
        }}
        .temp-score {{
            flex-shrink: 0;
            width: 80px;
            height: 80px;
            border-radius: 50%;
            border: 4px solid {temp_color};
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
        }}
        .temp-score .num {{
            font-size: 24px;
            font-weight: 700;
            color: {temp_color};
            line-height: 1;
        }}
        .temp-score .label {{
            font-size: 11px;
            color: #8b949e;
            margin-top: 2px;
        }}
        .temp-info {{ flex: 1; }}
        .temp-info .summary {{
            font-size: 15px;
            color: #f0f6fc;
            margin-bottom: 10px;
            line-height: 1.5;
        }}
        .metrics-row {{
            display: flex;
            gap: 12px;
            flex-wrap: wrap;
        }}
        .metric-card {{
            background: #0d1117;
            border: 1px solid #21262d;
            border-radius: 8px;
            padding: 8px 14px;
            min-width: 100px;
        }}
        .metric-label {{
            font-size: 11px;
            color: #6e7681;
            margin-bottom: 3px;
        }}
        .metric-value {{
            font-size: 16px;
            font-weight: 600;
            color: #f0f6fc;
        }}
        .metric-value.up {{ color: #f85149; }}
        .metric-value.down {{ color: #3fb950; }}

        /* 双列 */
        .columns {{
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 14px;
            margin-bottom: 14px;
        }}
        @media (max-width: 768px) {{
            .columns {{ grid-template-columns: 1fr; }}
        }}
        .column-card {{
            background: #161b22;
            border: 1px solid #ff7a00;
            border-radius: 12px;
            padding: 16px 18px;
            box-shadow: 0 0 12px rgba(255, 122, 0, 0.06);
        }}
        .column-title {{
            font-size: 16px;
            font-weight: 600;
            color: #ff7a00;
            margin-bottom: 4px;
            display: flex;
            align-items: center;
            gap: 8px;
        }}
        .tag-short {{
            font-size: 10px;
            padding: 2px 8px;
            border-radius: 10px;
            background: rgba(248, 81, 73, 0.15);
            color: #f85149;
            font-weight: 500;
        }}
        .tag-long {{
            font-size: 10px;
            padding: 2px 8px;
            border-radius: 10px;
            background: rgba(63, 185, 80, 0.15);
            color: #3fb950;
            font-weight: 500;
        }}
        .column-conclusion {{
            font-size: 13px;
            color: #8b949e;
            margin-bottom: 12px;
            line-height: 1.5;
            padding-bottom: 10px;
            border-bottom: 1px solid #21262d;
        }}
        .section-label {{
            font-size: 12px;
            font-weight: 600;
            color: #ff7a00;
            margin: 10px 0 8px;
        }}
        .section-label.risk {{ color: #f85149; }}
        .section-label.industry {{ color: #ff7a00; }}

        /* 股票项 */
        .stock-item {{
            background: #0d1117;
            border: 1px solid #21262d;
            border-radius: 8px;
            padding: 10px 12px;
            margin-bottom: 8px;
        }}
        .stock-name {{
            font-size: 14px;
            font-weight: 600;
            color: #f0f6fc;
            margin-bottom: 4px;
        }}
        .stock-code {{
            font-size: 11px;
            color: #6e7681;
            font-weight: normal;
        }}
        .stock-detail {{
            display: flex;
            gap: 6px;
            margin-bottom: 6px;
            flex-wrap: wrap;
        }}
        .tag {{
            font-size: 11px;
            padding: 2px 8px;
            border-radius: 4px;
            background: #21262d;
            color: #8b949e;
        }}
        .tag.up {{ background: rgba(248, 81, 73, 0.12); color: #f85149; }}
        .tag.down {{ background: rgba(63, 185, 80, 0.12); color: #3fb950; }}
        .tag.streak {{ background: rgba(63, 185, 80, 0.15); color: #3fb950; }}
        .stock-meta {{
            display: flex;
            justify-content: space-between;
            font-size: 12px;
            color: #6e7681;
        }}
        .change-pct.up {{ color: #f85149; font-weight: 600; }}
        .change-pct.down {{ color: #3fb950; font-weight: 600; }}

        /* 风险项 */
        .risk-item {{
            background: rgba(248, 81, 73, 0.05);
            border: 1px solid rgba(248, 81, 73, 0.2);
            border-radius: 8px;
            padding: 10px 12px;
            margin-bottom: 8px;
        }}
        .risk-name {{
            font-size: 13px;
            font-weight: 600;
            color: #f0f6fc;
            margin-bottom: 3px;
        }}
        .risk-reason {{
            font-size: 12px;
            color: #f85149;
            margin-bottom: 3px;
        }}
        .risk-detail {{
            font-size: 11px;
            color: #8b949e;
        }}

        /* 接力项 */
        .relay-item {{
            display: flex;
            align-items: center;
            gap: 8px;
            padding: 6px 0;
            border-bottom: 1px solid #21262d;
            font-size: 12px;
        }}
        .relay-item:last-child {{ border-bottom: none; }}
        .relay-name {{ flex: 1; color: #c9d1d9; }}
        .relay-badge {{
            font-size: 10px;
            padding: 1px 6px;
            border-radius: 3px;
            background: rgba(248, 81, 73, 0.12);
            color: #f85149;
        }}
        .relay-net {{ font-weight: 600; font-size: 11px; }}
        .relay-net.up {{ color: #f85149; }}

        /* 行业高亮 */
        .industry-highlight {{
            background: linear-gradient(90deg, rgba(255, 122, 0, 0.12) 0%, rgba(255, 122, 0, 0.02) 100%);
            border: 1px solid rgba(255, 122, 0, 0.3);
            border-radius: 8px;
            padding: 12px 14px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }}
        .industry-name {{
            font-size: 15px;
            font-weight: 600;
            color: #ff7a00;
        }}
        .industry-net {{
            font-size: 13px;
            font-weight: 600;
        }}
        .industry-net.up {{ color: #f85149; }}

        /* 共振项 */
        .resonance-item {{
            background: rgba(255, 122, 0, 0.06);
            border: 1px solid rgba(255, 122, 0, 0.25);
            border-radius: 8px;
            padding: 10px 12px;
            margin-bottom: 8px;
        }}
        .res-name {{
            font-size: 13px;
            font-weight: 600;
            color: #f0f6fc;
            margin-bottom: 4px;
        }}
        .res-detail {{
            display: flex;
            gap: 6px;
            margin-bottom: 4px;
        }}
        .mini-tag {{
            font-size: 10px;
            padding: 1px 6px;
            border-radius: 3px;
            background: #21262d;
            color: #8b949e;
        }}
        .res-strength {{
            font-size: 12px;
            color: #ff7a00;
        }}
        .res-strength b {{ color: #ff7a00; }}

        .empty-text {{
            color: #6e7681;
            font-size: 12px;
            text-align: center;
            padding: 14px 0;
        }}

        /* 明日关注 */
        .focus-card {{
            background: #161b22;
            border: 1px solid #ff7a00;
            border-radius: 12px;
            padding: 16px 18px;
            box-shadow: 0 0 15px rgba(255, 122, 0, 0.08);
        }}
        .focus-title-bar {{
            font-size: 15px;
            font-weight: 600;
            color: #ff7a00;
            margin-bottom: 12px;
            display: flex;
            align-items: center;
            gap: 8px;
        }}
        .focus-item {{
            display: flex;
            gap: 12px;
            padding: 10px 0;
            border-bottom: 1px solid #21262d;
        }}
        .focus-item:last-child {{ border-bottom: none; }}
        .focus-icon {{
            font-size: 20px;
            flex-shrink: 0;
            width: 28px;
            text-align: center;
        }}
        .focus-content {{ flex: 1; }}
        .focus-title {{
            font-size: 13px;
            font-weight: 600;
            color: #f0f6fc;
            margin-bottom: 3px;
        }}
        .focus-desc {{
            font-size: 12px;
            color: #8b949e;
            line-height: 1.5;
        }}

        /* 底部 */
        /* 深入分析入口 */
        .deep-links {{
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 14px;
            margin: 20px 0 16px;
        }}
        @media (max-width: 768px) {{
            .deep-links {{ grid-template-columns: 1fr; }}
        }}
        .deep-link {{
            display: flex;
            align-items: center;
            gap: 14px;
            padding: 16px 18px;
            border-radius: 12px;
            text-decoration: none;
            transition: transform .15s, box-shadow .15s;
            border: 1px solid #ff7a00;
            background: #161b22;
        }}
        .deep-link:hover {{
            transform: translateY(-2px);
            box-shadow: 0 4px 15px rgba(255, 122, 0, 0.2);
        }}
        .deep-link.jiyou-link {{ border-color: #e888a0; }}
        .deep-link.jiyou-link:hover {{ box-shadow: 0 4px 15px rgba(232, 136, 160, 0.25); }}
        .deep-link.nb-link {{ border-color: #6cb6ff; }}
        .deep-link.nb-link:hover {{ box-shadow: 0 4px 15px rgba(108, 182, 255, 0.25); }}
        .deep-link-icon {{
            font-size: 28px;
            flex-shrink: 0;
            width: 40px;
            text-align: center;
        }}
        .deep-link-text {{ flex: 1; }}
        .deep-link-title {{
            font-size: 15px;
            font-weight: 600;
            color: #f0f6fc;
            margin-bottom: 4px;
        }}
        .deep-link-desc {{
            font-size: 12px;
            color: #8b949e;
            line-height: 1.4;
        }}
        .deep-link-arrow {{
            font-size: 20px;
            color: #6e7681;
            font-weight: 300;
        }}

        .footer {{
            text-align: center;
            padding: 16px 0 8px;
            color: #6e7681;
            font-size: 11px;
        }}
        .footer a {{ color: #ff7a00; text-decoration: none; }}
        .footer a:hover {{ text-decoration: underline; }}
    </style>
</head>
<body>
<div class="container">
    <div style="margin-bottom:12px;">
        <a href="portal.html" style="display:inline-flex;align-items:center;gap:6px;padding:8px 20px;border-radius:20px;background:linear-gradient(135deg,#ff7a00,#ffa940);color:#4a4a4a;text-decoration:none;font-size:14px;font-weight:600;box-shadow:0 2px 8px rgba(255,122,0,0.35);transition:transform .15s,box-shadow .15s;cursor:pointer;">📅 返回九宝日历精选</a>
        <a href="signal-guide.html" style="display:inline-flex;align-items:center;gap:6px;padding:8px 20px;border-radius:20px;background:linear-gradient(135deg,#d2a8ff,#8957e5);color:#fff;text-decoration:none;font-size:14px;font-weight:600;box-shadow:0 2px 8px rgba(210,168,255,0.35);transition:transform .15s;margin-left:10px;cursor:pointer;">📖 信号说明</a>
    </div>
    <div class="header">
        <h1>📊 每日市场洞察</h1>
        <div class="subtitle">短线+中长线双视角 · 机游共振 × 北向资金</div>
    </div>
    <div class="update-time">数据日期：{latest_date} · 更新时间：{update_time}</div>

    <!-- 图例 -->
    <div class="legend">
        <div class="legend-item"><span class="legend-dot temp"></span><span>市场温度</span></div>
        <div class="legend-item"><span class="legend-dot jiyou"></span><span>机游共振（短线）</span></div>
        <div class="legend-item"><span class="legend-dot nb"></span><span>北向资金（中长线）</span></div>
        <div class="legend-item"><span class="legend-dot focus"></span><span>明日关注</span></div>
        <div class="legend-item"><span style="color:#f85149;font-weight:600;">红色=上涨/买入</span></div>
        <div class="legend-item"><span style="color:#3fb950;font-weight:600;">绿色=下跌/卖出</span></div>
    </div>

    <!-- 明日关注点 -->
    <div class="focus-card">
        <div class="focus-title-bar">🎯 明日关注点</div>
        {focus_html}
    </div>

    <!-- 双列 -->
    <div class="columns">
        <!-- 短线·机游共振 -->
        <div class="column-card">
            <div class="column-title">
                ⚡ 机游方向
                <span class="tag-short">短线</span>
            </div>
            <div class="column-conclusion">{jiyou_insight['conclusion']}</div>

            <div class="section-label">🔥 最强共振</div>
            {jiyou_buy_html}

            <div class="section-label risk">⚠️ 风险警示</div>
            {jiyou_risk_html}

            {relay_section}
        </div>

        <!-- 中长线·北向资金 -->
        <div class="column-card">
            <div class="column-title">
                📈 北向方向
                <span class="tag-long">中长线</span>
            </div>
            <div class="column-conclusion">{nb_insight['conclusion']}</div>

            <div class="section-label industry">🏭 最强行业</div>
            {nb_industry_html}

            <div class="section-label">💎 连续加仓龙头</div>
            {nb_continuous_html}

            {resonance_section}
        </div>
    </div>

    <!-- 市场温度 -->
    <div class="temp-card">
        <div class="temp-score">
            <div class="num">{market_temp['score']}</div>
            <div class="label">{market_temp['label']}</div>
        </div>
        <div class="temp-info">
            <div class="summary">{market_temp['summary']}</div>
            <div class="metrics-row">{metrics_html}
            </div>
        </div>
    </div>

    <!-- 深入分析入口 -->
    <div class="deep-links">
        <a href="jiyou-signal-analysis.html" class="deep-link jiyou-link">
            <div class="deep-link-icon">⚡</div>
            <div class="deep-link-text">
                <div class="deep-link-title">机游信号详细分析</div>
                <div class="deep-link-desc">单日信号 + 连续性追踪 · 4类基础信号 + 5类进阶信号</div>
            </div>
            <div class="deep-link-arrow">→</div>
        </a>
        <a href="northbound-analysis.html" class="deep-link nb-link">
            <div class="deep-link-icon">📈</div>
            <div class="deep-link-text">
                <div class="deep-link-title">北向资金详细分析</div>
                <div class="deep-link-desc">行业趋势 + 连续加仓 + 北向机构共振 · 中线价值视角</div>
            </div>
            <div class="deep-link-arrow">→</div>
        </a>
    </div>

    <div class="footer">
        数据来源：龙虎榜机游信号 &amp; 北向席位资金
    </div>
</div>
</body>
</html>"""

    return html


# ─── 主流程 ────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="每日市场洞察生成")
    parser.add_argument("--date", default="",
                        help="目标日期（默认取数据中最新日期）")
    parser.add_argument("--jiyou-html", default="jiyou-signal-analysis.html",
                        help="机游信号分析HTML路径")
    parser.add_argument("--nb-html", default="northbound-analysis.html",
                        help="北向分析HTML路径")
    parser.add_argument("--output", default="daily-insight.html",
                        help="输出HTML路径")
    parser.add_argument("--repo-dir", default=".",
                        help="仓库根目录")
    args = parser.parse_args()

    repo_dir = str(Path(args.repo_dir).resolve())
    jiyou_html = os.path.join(repo_dir, args.jiyou_html) if not os.path.isabs(args.jiyou_html) else args.jiyou_html
    nb_html = os.path.join(repo_dir, args.nb_html) if not os.path.isabs(args.nb_html) else args.nb_html
    output_path = os.path.join(repo_dir, args.output) if not os.path.isabs(args.output) else args.output

    print("=" * 60)
    print("📊 每日市场洞察 — 生成器")
    print(f"📁 仓库目录: {repo_dir}")
    print(f"📄 机游数据: {jiyou_html}")
    print(f"📄 北向数据: {nb_html}")
    print(f"📄 输出文件: {output_path}")
    print("=" * 60)

    # 1. 提取机游数据
    log_info("提取机游信号数据 ...")
    jiyou_content = load_html(jiyou_html)
    if not jiyou_content:
        log_error(f"机游数据文件不存在: {jiyou_html}")
        sys.exit(1)

    signal_data = extract_json_var(jiyou_content, "signalData")
    continuous_data = extract_json_var(jiyou_content, "continuousData")
    if not continuous_data:
        continuous_data = extract_json_var(jiyou_content, "ct")

    if not signal_data:
        log_error(f"无法从 {jiyou_html} 提取 signalData")
        sys.exit(1)
    print(f"  ✅ signalData: {len(signal_data)} 天")
    print(f"  ✅ continuousData: {'OK' if continuous_data else '无'}")

    # 2. 提取北向数据
    log_info("提取北向分析数据 ...")
    nb_content = load_html(nb_html)
    nb_daily = {}
    nb_analysis = {}
    if nb_content:
        nb_daily = extract_json_var(nb_content, "nbDailyData") or {}
        # nbAnalysis 特殊处理
        m = re.search(r"nbAnalysis\s*=\s*(\{.*?\});", nb_content, re.DOTALL)
        if m:
            try:
                nb_analysis = json.loads(m.group(1))
            except json.JSONDecodeError:
                nb_analysis = {}
    else:
        log_warn(f"北向数据文件不存在: {nb_html}")
    print(f"  ✅ nbDailyData: {len(nb_daily)} 天")
    print(f"  ✅ nbAnalysis: {'OK' if nb_analysis else '无'}")

    # 3. 确定目标日期
    if args.date:
        latest_date = args.date
    else:
        jiyou_dates = sorted(signal_data.keys()) if signal_data else []
        nb_dates = sorted(nb_daily.keys()) if nb_daily else []
        all_dates = set(jiyou_dates) & set(nb_dates)
        if all_dates:
            latest_date = sorted(all_dates)[-1]
        elif jiyou_dates:
            latest_date = jiyou_dates[-1]
        elif nb_dates:
            latest_date = nb_dates[-1]
        else:
            log_error("无法确定目标日期")
            sys.exit(1)
    print(f"  📅 目标日期: {latest_date}")

    # 4. 计算洞察
    log_info("计算市场温度 ...")
    market_temp = compute_market_temp(signal_data, nb_daily, latest_date,
                                       continuous_data or {}, nb_analysis or {})
    print(f"  温度: {market_temp['label']} ({market_temp['score']}分)")

    log_info("计算机游短线洞察 ...")
    jiyou_insight = compute_jiyou_insight(signal_data, continuous_data or {}, latest_date)
    print(f"  最强共振: {len(jiyou_insight['top_resonance'])} 只")
    print(f"  风险警示: {len(jiyou_insight['risk_warning'])} 只")

    log_info("计算北向中长线洞察 ...")
    nb_insight = compute_northbound_insight(nb_analysis or {}, nb_daily, latest_date)
    top_ind = nb_insight['top_industry']['name'] if nb_insight['top_industry'] else '无'
    print(f"  最强行业: {top_ind}")
    print(f"  连续加仓: {len(nb_insight['continuous_buy'])} 只")

    log_info("生成明日关注点 ...")
    focus_points = compute_focus_points(jiyou_insight, nb_insight, market_temp)
    print(f"  关注点: {len(focus_points)} 条")

    # 5. 生成HTML
    log_info("生成HTML页面 ...")
    update_time = datetime.now().strftime("%Y-%m-%d %H:%M")
    html_content = generate_html(market_temp, jiyou_insight, nb_insight,
                                 focus_points, latest_date, update_time)

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html_content)

    print(f"  ✅ 已生成: {output_path}")
    print(f"  📏 文件大小: {os.path.getsize(output_path)} 字节")

    print()
    print("=" * 60)
    print("🎉 每日市场洞察生成完成")
    print(f"📅 数据日期: {latest_date}")
    print(f"🌡️  市场温度: {market_temp['label']} ({market_temp['score']}分)")
    print(f"⚡ 短线: {len(jiyou_insight['top_resonance'])}只买入 / {len(jiyou_insight['risk_warning'])}只风险")
    print(f"📈 北向: {top_ind}")
    print(f"📄 输出: {output_path}")
    print("=" * 60)


if __name__ == "__main__":
    main()
