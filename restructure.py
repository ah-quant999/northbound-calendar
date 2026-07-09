#!/usr/bin/env python3
"""将7月北向资金日历.html 和 7月机游共振日历.html 改造为单文件多月份版本"""

import os
import re

REPO_DIR = "/tmp/nb-calendar"

def restructure_calendar(src_name, dst_name, title, year=2026, current_month=7):
    src_path = os.path.join(REPO_DIR, src_name)
    dst_path = os.path.join(REPO_DIR, dst_name)
    
    with open(src_path, "r", encoding="utf-8") as f:
        html = f.read()
    
    # 1. 修改title
    html = re.sub(r'<title>.*?</title>', f'<title>{title}</title>', html)
    
    # 2. 添加CSS
    css_insert = """
        /* 多月份切换 */
        .month-section { display: none; }
        .month-section.active { display: block; }
        .month-nav .nav-link.month-btn { cursor: pointer; }
        .month-nav .nav-link.month-btn:hover { background: #1f2937; }
"""
    html = html.replace("</style>", css_insert + "\n</style>")
    
    # 3. 找到关键位置
    first_table = html.find('<table class="week-table">')
    
    # 找到月度汇总区域
    summary_marker = html.find('<!-- 月度汇总 -->')
    if summary_marker < 0:
        summary_marker = html.find('📊 月度汇总')
    
    # 找到summary前面的闭合div (即最后一个week-table的容器结束)
    # 从first_table到summary_marker之间找最后一个</div><!-- 月度汇总 -->
    summary_div_start = html.rfind('<div', 0, summary_marker)
    # 确保找到的是月度汇总的起始div
    # 往上找最近的一个<div class="...">
    
    # 更准确：找包含月度汇总的div的开始
    # 从summary_marker往前找<h3或<div
    search_start = summary_marker - 500
    if search_start < 0:
        search_start = 0
    section_before = html[search_start:summary_marker]
    last_div = section_before.rfind('<div ')
    last_div_h3 = section_before.rfind('<h3')
    if last_div_h3 > 0 and last_div_h3 > last_div:
        # 月度汇总从h3开始，没有div包装
        summary_div_start = search_start + last_div
    else:
        summary_div_start = search_start + last_div
    
    # 找到footer-note
    footer_start = html.find('class="footer-note"')
    if footer_start < 0:
        footer_start = html.find('footer-note')
    
    # summary结束位置：从summary_div_start到footer_start之间匹配嵌套div
    part = html[summary_div_start:footer_start]
    depth = 0
    summary_end = 0
    i = 0
    while i < len(part):
        if part[i:i+5] == '<div ' or part[i:i+4] == '<div':
            depth += 1
            i += 1
        elif part[i:i+6] == '</div>':
            depth -= 1
            i += 6
            if depth == 0:
                summary_end = summary_div_start + i
                break
        else:
            i += 1
    
    if summary_end == 0:
        # fallback
        summary_end = footer_start
    
    # 4. 日历表格部分包装
    calendar_tables = html[first_table:summary_div_start]
    month_section = f'<div class="month-section active" id="month-{current_month}">\n{calendar_tables}\n        </div>'
    
    # 5. 月度汇总包装
    monthly_summary = html[summary_div_start:summary_end]
    sum_section = f'<div class="month-section active" id="summary-{current_month}">\n{monthly_summary}\n        </div>'
    
    html = html[:first_table] + month_section + html[summary_div_start:summary_div_start]
    html = html[:summary_div_start] + sum_section + html[summary_end:]
    
    # 6. 修改导航栏
    prev_month = current_month - 1
    next_month = current_month + 1
    prev_label = f"{prev_month}月" if prev_month >= 1 else ""
    next_label = f"{next_month}月" if next_month <= 12 else ""
    
    nav_pattern = r'<div class="month-nav">.*?</div>'
    new_nav = f'''<div class="month-nav">
                <span class="nav-link month-btn" onclick="switchMonth({prev_month})" id="nav-prev" style="display:none;">← {prev_label}</span>
                <span class="nav-current" id="nav-year">{year}年 7月</span>
                <span class="nav-link month-btn" onclick="switchMonth({next_month})" id="nav-next">{next_label} →</span>
            </div>'''
    html = re.sub(nav_pattern, new_nav, html, flags=re.DOTALL)
    
    # 7. 添加JS
    js_code = f'''
    <script>
        var currentMonth = {current_month};
        var year = {year};
        var monthNames = ['','1月','2月','3月','4月','5月','6月','7月','8月','9月','10月','11月','12月'];
        
        function switchMonth(month) {{
            var oldM = document.getElementById('month-' + currentMonth);
            var oldS = document.getElementById('summary-' + currentMonth);
            if (oldM) oldM.classList.remove('active');
            if (oldS) oldS.classList.remove('active');
            
            var newM = document.getElementById('month-' + month);
            var newS = document.getElementById('summary-' + month);
            if (newM) newM.classList.add('active');
            if (newS) newS.classList.add('active');
            
            currentMonth = month;
            document.getElementById('nav-year').textContent = year + '年 ' + monthNames[month];
            
            var prevBtn = document.getElementById('nav-prev');
            var nextBtn = document.getElementById('nav-next');
            if (prevBtn) {{
                if (month > 1) {{
                    prevBtn.style.display = '';
                    prevBtn.innerHTML = '← ' + monthNames[month-1];
                    prevBtn.setAttribute('onclick', 'switchMonth(' + (month-1) + ')');
                }} else {{
                    prevBtn.style.display = 'none';
                }}
            }}
            if (nextBtn) {{
                if (month < 12) {{
                    nextBtn.style.display = '';
                    nextBtn.innerHTML = monthNames[month+1] + ' →';
                    nextBtn.setAttribute('onclick', 'switchMonth(' + (month+1) + ')');
                }} else {{
                    nextBtn.style.display = 'none';
                }}
            }}
        }}
    </script>
'''
    html = html.replace('</body>', js_code + '\n</body>')
    
    with open(dst_path, "w", encoding="utf-8") as f:
        f.write(html)
    
    print(f"✅ 已创建: {dst_path}")

restructure_calendar("7月北向资金日历.html", "北向资金日历.html", "龙虎榜北向席位日历")
restructure_calendar("7月机游共振日历.html", "机游共振日历.html", "机游共振日历")

print("✅ 全部完成！")