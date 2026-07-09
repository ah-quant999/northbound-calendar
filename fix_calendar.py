#!/usr/bin/env python3
"""
Fix important calendar HTML files:
1. Move 今日事件 block from after legend-section to before legend-section (after day-detail-panel)
2. Update CSS for day-detail-panel and today-events sections
3. Update JS detail panel title format
"""
import re
import glob
import os

FILES = sorted(glob.glob("/tmp/nb-calendar/重要日历_*.html"))
print(f"Found {len(FILES)} files to process")

for fpath in FILES:
    with open(fpath, "r", encoding="utf-8") as f:
        html = f.read()

    original = html
    fname = os.path.basename(fpath)
    print(f"\n--- Processing {fname} ---")

    # ========== STEP 1: Move 今日事件 block to before legend-section ==========
    # Extract the 今日事件 block (including the comment marker)
    today_events_pattern = r'(\s*<!-- ========== 今日事件 ========== -->\s*<div class="today-events-section">.*?</div>\s*</div>)'
    today_events_match = re.search(today_events_pattern, html, re.DOTALL)
    
    if today_events_match:
        today_events_block = today_events_match.group(1)
        # Remove it from its current position
        html = html.replace(today_events_match.group(0), "")
        
        # Find the legend-section comment and insert before it
        legend_comment = '    <!-- ========== 色块标注区 ========== -->'
        if legend_comment in html:
            html = html.replace(legend_comment, today_events_block + "\n\n" + legend_comment)
            print(f"  [OK] Moved 今日事件 block before legend-section")
        else:
            print(f"  [FAIL] Could not find legend comment insertion point!")
            html = original
            continue
    else:
        print(f"  [FAIL] Could not find 今日事件 block!")

    # ========== STEP 2: Update CSS ==========
    
    # 2a. Replace #day-detail-panel CSS block
    old_panel_css_pattern = r'''        #day-detail-panel \{
            display: none;
            background: rgba\(22, 27, 34, 0\.96\);
            border: 1px solid #30363d;
            border-radius: 12px;
            padding: 24px 28px;
            margin: 20px 0;
        \}
        #day-detail-panel\.active \{
            display: block;
        \}
        \.detail-header \{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 18px;
            padding-bottom: 12px;
            border-bottom: 1px solid #30363d;
        \}
        \.detail-title \{
            font-size: 20px;
            font-weight: 700;
            color: #58a6ff;
        \}
        \.detail-close-btn \{
            background: #21262d;
            border: 1px solid #30363d;
            color: #c9d1d9;
            padding: 6px 16px;
            border-radius: 6px;
            cursor: pointer;
            font-size: 14px;
            transition: background 0\.15s;
        \}
        \.detail-close-btn:hover \{
            background: #30363d;
        \}
        \.detail-event-item \{
            display: flex;
            align-items: flex-start;
            gap: 12px;
            padding: 10px 14px;
            margin-bottom: 8px;
            border-radius: 8px;
            background: rgba\(255,255,255,0\.04\);
            border: 1px solid rgba\(255,255,255,0\.06\);
        \}
        \.detail-event-dot \{
            width: 12px;
            height: 12px;
            border-radius: 50%;
            flex-shrink: 0;
            margin-top: 4px;
        \}
        \.detail-event-text \{
            font-size: 16px;
            color: #e6edf3;
            line-height: 1\.5;
            flex: 1;
        \}
        \.detail-event-star \{
            color: #e63946;
            font-size: 16px;
            margin-right: 4px;
        \}
        \.detail-event-cls \{
            font-size: 12px;
            padding: 2px 10px;
            border-radius: 4px;
            white-space: nowrap;
            flex-shrink: 0;
            margin-top: 2px;
        \}'''

    new_panel_css = '''        #day-detail-panel {
            display: none;
            background: rgba(22, 27, 34, 0.96);
            border: 1px solid #30363d;
            border-radius: 12px;
            padding: 20px;
            margin: 20px 0;
            position: relative;
        }
        #day-detail-panel.active {
            display: block;
        }
        .detail-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 16px;
        }
        .detail-title {
            font-size: 20px;
            font-weight: 700;
            color: #58a6ff;
            text-align: left;
        }
        .detail-close-btn {
            position: absolute;
            top: 12px;
            right: 12px;
            width: 28px;
            height: 28px;
            border-radius: 50%;
            background: rgba(255,255,255,0.15);
            border: none;
            color: #fff;
            font-size: 16px;
            cursor: pointer;
            display: flex;
            align-items: center;
            justify-content: center;
            line-height: 1;
            transition: background 0.15s;
        }
        .detail-close-btn:hover {
            background: rgba(255,255,255,0.3);
        }
        .detail-event-item {
            display: flex;
            align-items: center;
            gap: 10px;
            padding: 8px 14px;
            margin-bottom: 6px;
            border-radius: 8px;
            background: rgba(255,255,255,0.06);
            border-left: 3px solid #8b949e;
        }
        .detail-event-dot {
            display: none;
        }
        .detail-event-text {
            font-size: 15px;
            color: #fff;
            font-weight: 600;
            line-height: 1.5;
            flex: 1;
        }
        .detail-event-star {
            color: #e63946;
            font-size: 14px;
            margin-right: 4px;
        }
        .detail-event-cls {
            font-size: 11px;
            color: #8b949e;
            padding: 0;
            border-radius: 0;
            white-space: nowrap;
            flex-shrink: 0;
            margin-top: 0;
            background: none;
        }'''

    if re.search(old_panel_css_pattern, html):
        html = re.sub(old_panel_css_pattern, new_panel_css, html)
        print(f"  [OK] Updated #day-detail-panel CSS")
    else:
        print(f"  [WARN] Could not match #day-detail-panel CSS exactly, trying line-by-line approach")
        # Fallback: do string replacement instead of regex
        old_css_start = "        #day-detail-panel {"
        old_css_end = "        .detail-event-cls {"
        
        # Find the start and end positions
        start_idx = html.find("        #day-detail-panel {\n")
        if start_idx >= 0:
            # Find the end of the .detail-event-cls block
            end_marker = "        }\n\n        /* 今日事件区块 */"
            end_idx = html.find(end_marker, start_idx)
            if end_idx >= 0:
                old_block = html[start_idx:end_idx + len("        }")]
                html = html.replace(old_block, new_panel_css)
                print(f"  [OK] Updated #day-detail-panel CSS (fallback method)")
            else:
                print(f"  [FAIL] Could not find CSS block end marker")
        else:
            print(f"  [FAIL] Could not find #day-detail-panel CSS start")

    # 2b. Replace today-events CSS block
    old_today_css_pattern = r'''        /\* 今日事件区块 \*/
        \.today-events-section \{
            margin-top: 30px;
            padding: 24px 28px;
            background: rgba\(22, 27, 34, 0\.96\);
            border: 1px solid #30363d;
            border-radius: 12px;
        \}
        \.today-events-title \{
            font-size: 20px;
            font-weight: 700;
            color: #d29922;
            margin-bottom: 16px;
            padding-bottom: 12px;
            border-bottom: 1px solid #30363d;
        \}
        \.today-event-item \{
            display: flex;
            align-items: flex-start;
            gap: 12px;
            padding: 10px 14px;
            margin-bottom: 8px;
            border-radius: 8px;
            background: rgba\(255,255,255,0\.04\);
            border: 1px solid rgba\(255,255,255,0\.06\);
        \}
        \.today-event-dot \{
            width: 12px;
            height: 12px;
            border-radius: 50%;
            flex-shrink: 0;
            margin-top: 4px;
        \}
        \.today-event-text \{
            font-size: 16px;
            color: #e6edf3;
            line-height: 1\.5;
            flex: 1;
        \}
        \.today-event-star \{
            color: #e63946;
            font-size: 16px;
            margin-right: 4px;
        \}
        \.today-event-cls \{
            font-size: 12px;
            padding: 2px 10px;
            border-radius: 4px;
            white-space: nowrap;
            flex-shrink: 0;
            margin-top: 2px;
        \}'''

    new_today_css = '''        /* 今日事件区块 */
        .today-events-section {
            margin-top: 20px;
            margin-bottom: 10px;
            padding: 0;
            background: transparent;
            border: none;
            border-radius: 0;
        }
        .today-events-title {
            font-size: 18px;
            font-weight: 700;
            color: #fff;
            margin-bottom: 12px;
        }
        .today-event-item {
            display: flex;
            align-items: center;
            gap: 10px;
            padding: 8px 14px;
            margin-bottom: 6px;
            border-radius: 8px;
            background: rgba(255,255,255,0.06);
            border-left: 3px solid #8b949e;
        }
        .today-event-dot {
            display: none;
        }
        .today-event-text {
            font-size: 15px;
            color: #fff;
            font-weight: 600;
            line-height: 1.5;
            flex: 1;
        }
        .today-event-star {
            color: #e63946;
            font-size: 14px;
            margin-right: 4px;
        }
        .today-event-cls {
            font-size: 11px;
            color: #8b949e;
            padding: 0;
            border-radius: 0;
            white-space: nowrap;
            flex-shrink: 0;
            margin-top: 0;
            background: none;
        }'''

    if re.search(old_today_css_pattern, html):
        html = re.sub(old_today_css_pattern, new_today_css, html)
        print(f"  [OK] Updated .today-events CSS")
    else:
        print(f"  [WARN] Could not match .today-events CSS regex, trying fallback")
        # Fallback: string-based replacement
        old_start = "        /* 今日事件区块 */"
        old_end_marker = "        .today-event-cls {"
        start_idx = html.find(old_start)
        if start_idx >= 0:
            # Find end of the .today-event-cls block
            end_search = html.find("        }\n\n        .today-events-empty", start_idx)
            if end_search >= 0:
                old_block = html[start_idx:end_search + len("        }")]
                html = html.replace(old_block, new_today_css)
                print(f"  [OK] Updated .today-events CSS (fallback method)")
            else:
                print(f"  [FAIL] Could not find today-events CSS end")

    # ========== STEP 3: Update JS detail panel title ==========
    old_title_pattern = r'titleEl\.textContent = CURRENT_YEAR \+ "-" \+ mm \+ "-" \+ dd \+ " · 共" \+ eventsData\.length \+ "个事件";'
    new_title = 'titleEl.textContent = dayNum + "日 事件详情";'
    
    if re.search(old_title_pattern, html):
        html = re.sub(old_title_pattern, new_title, html)
        print(f"  [OK] Updated JS detail title format")
    else:
        print(f"  [WARN] Could not match JS detail title regex, trying string replace")
        old_title_str = 'titleEl.textContent = CURRENT_YEAR + "-" + mm + "-" + dd + " · 共" + eventsData.length + "个事件";'
        if old_title_str in html:
            html = html.replace(old_title_str, new_title)
            print(f"  [OK] Updated JS detail title format (string replace)")
        else:
            print(f"  [FAIL] Could not find JS detail title to update")

    # ========== STEP 4: Update JS event rendering for detail panel ==========
    old_render_pattern = r"""            html \+= '<div class="detail-event-item">';
            html \+= '<span class="detail-event-dot" style="background:' \+ color \+ ';"></span>';
            html \+= '<span class="detail-event-text">' \+ star \+ ev\.text \+ '</span>';
            html \+= '<span class="detail-event-cls" style="background:' \+ color \+ ';color:#fff;">' \+ label \+ '</span>';"""
    
    new_render = """            html += '<div class="detail-event-item" style="border-left-color:' + color + ';">';
            html += '<span class="detail-event-text">' + star + ev.text + '</span>';
            html += '<span class="detail-event-cls">' + (isImportant ? '<span class="detail-event-star">★</span>' : '') + label + '</span>';"""
    
    if re.search(old_render_pattern, html):
        html = re.sub(old_render_pattern, new_render, html)
        print(f"  [OK] Updated JS detail event rendering")
    else:
        print(f"  [WARN] Could not match JS detail rendering regex, trying string replace")
        old_render_str = """            html += '<div class="detail-event-item">';
            html += '<span class="detail-event-dot" style="background:' + color + ';"></span>';
            html += '<span class="detail-event-text">' + star + ev.text + '</span>';
            html += '<span class="detail-event-cls" style="background:' + color + ';color:#fff;">' + label + '</span>';"""
        if old_render_str in html:
            html = html.replace(old_render_str, new_render)
            print(f"  [OK] Updated JS detail event rendering (string replace)")
        else:
            print(f"  [FAIL] Could not find JS detail rendering to update")

    # ========== STEP 5: Update today-event-item HTML ==========
    # Add border-left-color from dot's background, remove dot, update cls span
    
    def fix_today_event_item(match):
        full = match.group(0)
        dot_match = re.search(r'background:(#[0-9a-fA-F]+)', full)
        if dot_match:
            color = dot_match.group(1)
            # Add border-left-color to the item div
            full = full.replace('<div class="today-event-item">', 
                              f'<div class="today-event-item" style="border-left-color:{color};">')
            # Remove the dot span entirely
            full = re.sub(r'\s*<span class="today-event-dot"[^>]*></span>', '', full)
            # Update today-event-cls: remove inline styles, add star for important
            def fix_cls(m):
                label = m.group(1)
                important_labels = {'FOMC', '财报截止', 'A50交割', '重要政策'}
                star = '<span class="today-event-star">★</span>' if label in important_labels else ''
                return f'<span class="today-event-cls">{star}{label}</span>'
            full = re.sub(r'<span class="today-event-cls"[^>]*>([^<]+)</span>', fix_cls, full)
        return full
    
    html = re.sub(
        r'<div class="today-event-item">\s*<span class="today-event-dot"[^>]*></span>\s*<span class="today-event-text">.*?</span>\s*<span class="today-event-cls"[^>]*>.*?</span>\s*</div>',
        fix_today_event_item,
        html,
        flags=re.DOTALL
    )
    print(f"  [OK] Updated today-event-item HTML structure")

    # ========== STEP 6: Update today-events-title to remove date suffix ==========
    html = re.sub(
        r'(<div class="today-events-title">📅 今日事件\(\d+个)\s*·\s*\d{4}-\d{2}-\d{2}(</div>)',
        r'\1\2',
        html
    )
    print(f"  [OK] Updated today-events-title format")

    # ========== STEP 7: Update close button text ==========
    html = html.replace('>关闭</button>', '>✕</button>')
    print(f"  [OK] Updated close button text")

    # ========== STEP 8: Clean any remaining today-event-cls inline styles ==========
    html = re.sub(
        r'<span class="today-event-cls" style="[^"]*">',
        '<span class="today-event-cls">',
        html
    )

    # Write back
    if html != original:
        with open(fpath, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"  [OK] Saved {fname}")
    else:
        print(f"  [-] No changes for {fname}")

print("\n\n===== ALL FILES PROCESSED =====")
