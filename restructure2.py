#!/usr/bin/env python3
"""将7月日历文件改造为单文件多月份版本"""

import os
import re

REPO = "/tmp/nb-calendar"

def restructure(src_name, dst_name, title, cm=7):
    src = os.path.join(REPO, src_name)
    dst = os.path.join(REPO, dst_name)
    
    with open(src, "r", encoding="utf-8") as f:
        html = f.read()
    
    # 1. 改title
    html = re.sub(r'<title>.*?</title>', f'<title>{title}</title>', html)
    
    # 2. 加CSS
    css = """
        /* 多月份切换 */
        .month-section { display: none; }
        .month-section.active { display: block; }
        .month-nav .nav-link.month-btn { cursor: pointer; }
        .month-nav .nav-link.month-btn:hover { background: #1f2937; }
"""
    html = html.replace("</style>", css + "</style>")
    
    # 3. 找到关键位置
    first_table = html.find('<table class="week-table">')
    
    # 找到 "月度汇总" 标记行
    summary_marker = html.find('<!-- 月度汇总 -->')
    if summary_marker < 0:
        summary_marker = html.find('📊 月度汇总')
    
    # 从summary_marker往前找最近的</div>，这是日历表格的结尾
    cal_end = html.rfind('</div>', 0, summary_marker) + 6  # +6 for </div>
    
    # 从summary_marker开始找<h3>，这是月度汇总的开始
    sum_h3 = html.find('<h3', summary_marker)
    # 找到月度汇总的结束：下一个<div class="footer-note"前最近的一个</div>
    footer_start = html.find('class="footer-note"')
    sum_end = html.rfind('</div>', sum_h3, footer_start) + 6
    
    # 4. 提取各部分
    header_part = html[:first_table]
    calendar_tables = html[first_table:cal_end]
    summary_part = html[sum_h3:sum_end]
    footer_part = html[sum_end:]
    
    # 5. 包装
    month_content = f'<div class="month-section active" id="month-{cm}">\n{calendar_tables}\n        </div>'
    sum_content = f'<div class="month-section active" id="summary-{cm}">\n{summary_part}\n        </div>'
    
    # 6. 重组合
    html = header_part + month_content + '\n' + sum_content + footer_part
    
    # 7. 改导航
    pm, nm = cm - 1, cm + 1
    nav_old = r'<div class="month-nav">.*?</div>'
    nav_new = f'''<div class="month-nav">
                <span class="nav-link month-btn" onclick="switchMonth({pm})" id="nav-prev" style="display:none;">← {pm}月</span>
                <span class="nav-current" id="nav-year">2026年 7月</span>
                <span class="nav-link month-btn" onclick="switchMonth({nm})" id="nav-next">{nm}月 →</span>
            </div>'''
    html = re.sub(nav_old, nav_new, html, flags=re.DOTALL)
    
    # 8. 加JS
    js = '''
    <script>
        var curM = 7, yr = 2026, mn = ['','1月','2月','3月','4月','5月','6月','7月','8月','9月','10月','11月','12月'];
        function switchMonth(m) {
            var o = document.getElementById('month-'+curM), s = document.getElementById('summary-'+curM);
            if(o) o.classList.remove('active'); if(s) s.classList.remove('active');
            var n = document.getElementById('month-'+m), ns = document.getElementById('summary-'+m);
            if(n) n.classList.add('active'); if(ns) ns.classList.add('active');
            curM = m; document.getElementById('nav-year').textContent = yr + '年 ' + mn[m];
            var p = document.getElementById('nav-prev'), nx = document.getElementById('nav-next');
            if(p) { if(m>1) { p.style.display=''; p.innerHTML='← '+mn[m-1]; p.onclick=function(){switchMonth(m-1);}; }
                    else { p.style.display='none'; } }
            if(nx) { if(m<12) { nx.style.display=''; nx.innerHTML=mn[m+1]+' →'; nx.onclick=function(){switchMonth(m+1);}; }
                     else { nx.style.display='none'; } }
        }
    </script>'''
    html = html.replace('</body>', js + '\n</body>')
    
    with open(dst, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"✅ {dst_name}")

restructure("7月北向资金日历.html", "北向资金日历.html", "龙虎榜北向席位日历")
restructure("7月机游共振日历.html", "机游共振日历.html", "机游共振日历")
print("✅ Done!")