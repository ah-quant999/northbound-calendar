#!/usr/bin/env python3
"""
机游共振日历 — 日期-星期对齐校验脚本

检查内容：
1. 每行7个格子对应的星期是否正确（周一到周日的位置）
2. 跨月日期（other-month）的月份和星期是否匹配真实日历
3. 每月第一天的星期位置是否正确
4. 周标签文字是否与该行实际日期范围一致

用法：
  python3 validate_date_alignment.py [HTML文件路径]
  默认: ../机游共振日历.html

返回码：0=通过，1=有错误
"""

import re
import sys
import os
import calendar
from datetime import date, timedelta


WEEKDAY_NAMES = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


def parse_args():
    if len(sys.argv) > 1:
        return sys.argv[1]
    script_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(os.path.dirname(script_dir), "机游共振日历.html")


def extract_all_weeks(html: str) -> list:
    """
    提取所有周行，返回列表，每个元素含：
      month_id, week_label, days[7], th_names
    days[col] = {'day', 'is_other_month', 'weekday_index', 'weekday_name', 'td_class'} or None
    """
    weeks = []

    month_sections = list(re.finditer(
        r'<div class="month-section[^"]*" id="month-(\d+)"[^>]*>(.*?)(?=<div class="month-section|$)',
        html, re.DOTALL,
    ))

    for month_match in month_sections:
        month_id = int(month_match.group(1))
        section_html = month_match.group(2)

        week_label_pattern = re.compile(
            r'<div style="text-align:left;[^"]*">([^<]+)</div>',
        )
        table_pattern = re.compile(
            r'<table class="week-table".*?</table>',
            re.DOTALL,
        )

        labels = [(m.start(), m.group(1).strip()) for m in week_label_pattern.finditer(section_html)]
        tables = [(m.start(), m.group(0)) for m in table_pattern.finditer(section_html)]

        for tbl_start, tbl_html in tables:
            week_label = ""
            for lbl_start, lbl_text in labels:
                if lbl_start < tbl_start:
                    week_label = lbl_text
                else:
                    break

            thead_match = re.search(r'<thead>.*?</thead>', tbl_html, re.DOTALL)
            if thead_match:
                th_names = re.findall(r'<th[^>]*>(.*?)</th>', thead_match.group(0))
                th_names = [re.sub(r'<[^>]+>', '', t).strip() for t in th_names]
            else:
                th_names = WEEKDAY_NAMES

            tbody_match = re.search(r'<tbody>.*?</tbody>', tbl_html, re.DOTALL)
            if not tbody_match:
                continue

            for tr_match in re.finditer(r'<tr>(.*?)</tr>', tbody_match.group(0), re.DOTALL):
                tr_html = tr_match.group(1)
                tds = list(re.finditer(r'<td\b([^>]*)>(.*?)</td>', tr_html, re.DOTALL))
                if len(tds) != 7:
                    continue

                days = [None] * 7
                for idx, td_m in enumerate(tds):
                    td_attrs = td_m.group(1)
                    td_content = td_m.group(2)
                    day_m = re.search(r'<span class="day-number[^"]*">(\d+)</span>', td_content)
                    if not day_m:
                        continue
                    day = int(day_m.group(1))
                    is_other = ("other-month" in td_attrs) or ('class="day-number other"' in td_content)
                    td_class = ""
                    cls_m = re.search(r'class="([^"]*)"', td_attrs)
                    if cls_m:
                        td_class = cls_m.group(1)
                    days[idx] = {
                        "day": day,
                        "is_other_month": is_other,
                        "weekday_index": idx,
                        "weekday_name": th_names[idx] if idx < len(th_names) else f"列{idx}",
                        "td_class": td_class,
                    }

                if any(d is not None for d in days):
                    weeks.append({
                        "month_id": month_id,
                        "week_label": week_label,
                        "days": days,
                        "th_names": th_names,
                    })

    return weeks


def infer_real_dates(week: dict, year: int) -> list:
    """
    根据周行的 day 号、month_id、is_other_month 标记，推断每个单元格的真实 date。
    返回长度7的列表，元素为 date 或 None。
    策略：
      1. 非 other-month 的 day，默认月=month_id
      2. other-month 的 day：
         - 如果该行大部分日号较大且 other 的 day 小 → 是下月开头
         - 如果该行大部分日号较小且 other 的 day 大 → 是上月结尾
      3. 再用星期校验验证
    """
    days = week["days"]
    month_id = week["month_id"]
    results = [None] * 7

    # 找到所有 non-other 的日号
    non_others = [(i, d["day"]) for i, d in enumerate(days) if d is not None and not d["is_other_month"]]
    others = [(i, d["day"]) for i, d in enumerate(days) if d is not None and d["is_other_month"]]

    if not non_others:
        # 全是 other-month，看周标签或上下文
        # 简单处理：假设是 month_id-1 的月末或 month_id+1 的月初
        for i, day_num in others:
            # 如果 day > 15 则是上月末，否则是下月初
            if day_num > 15:
                m = month_id - 1
                y = year
                if m < 1:
                    m = 12
                    y -= 1
            else:
                m = month_id + 1
                y = year
                if m > 12:
                    m = 1
                    y += 1
            try:
                results[i] = date(y, m, day_num)
            except ValueError:
                pass
        return results

    # 有 non-other 日期作为锚点
    anchor_idx, anchor_day = non_others[0]
    anchor_date = date(year, month_id, anchor_day)
    # 往前推算每个格子的日期
    for i in range(anchor_idx, -1, -1):
        d = anchor_date - timedelta(days=anchor_idx - i)
        if days[i] is not None:
            results[i] = d
    for i in range(anchor_idx, 7):
        d = anchor_date + timedelta(days=i - anchor_idx)
        if days[i] is not None:
            results[i] = d

    return results


def check_weekday_alignment(weeks: list, year: int) -> list:
    """校验每行7个格子对应的星期是否正确"""
    errors = []
    for wi, week in enumerate(weeks):
        label = week["week_label"] or f"第{wi+1}行"
        real_dates = infer_real_dates(week, year)
        for i, day_info in enumerate(week["days"]):
            if day_info is None:
                continue
            real_d = real_dates[i]
            if real_d is None:
                continue
            real_weekday = real_d.weekday()  # 0=周一
            if i != real_weekday:
                expected_name = WEEKDAY_NAMES[real_weekday]
                errors.append(
                    f"【星期错位】{label} {real_d.isoformat()}({day_info['day']}日) "
                    f"应为{expected_name}(列{real_weekday})，实际在{day_info['weekday_name']}(列{i})"
                )
            # 额外验证：other-month 标记是否正确
            if day_info["is_other_month"] and real_d.month == week["month_id"]:
                errors.append(
                    f"【跨月标记错误】{label} {real_d.isoformat()} 被标记为跨月(other-month)，"
                    f"但其真实月份={real_d.month}月，与当前区{week['month_id']}月相同"
                )
            if not day_info["is_other_month"] and real_d.month != week["month_id"]:
                errors.append(
                    f"【跨月标记缺失】{label} {real_d.isoformat()} 未标记跨月，"
                    f"但其真实月份={real_d.month}月，与当前区{week['month_id']}月不同"
                )
    return errors


def check_first_day_position(weeks: list, year: int) -> list:
    """校验每月第一天的星期位置是否正确"""
    errors = []
    for month_id in range(1, 13):
        first = date(year, month_id, 1)
        expected_weekday = first.weekday()
        # 找 day=1 的非跨月单元格
        found = False
        for wi, week in enumerate(weeks):
            for day_info in week["days"]:
                if day_info is None:
                    continue
                if day_info["day"] == 1 and not day_info["is_other_month"] and week["month_id"] == month_id:
                    found = True
                    if day_info["weekday_index"] != expected_weekday:
                        errors.append(
                            f"【月初错位】{year}年{month_id}月1日 应为{WEEKDAY_NAMES[expected_weekday]}，"
                            f"实际位于{day_info['weekday_name']}列"
                        )
                    break
            if found:
                break
        if not found:
            # 可以从 other-month 里找
            pass
    return errors


def check_week_label(weeks: list, year: int) -> list:
    """校验周标签文字是否与该行实际日期范围一致"""
    errors = []
    for wi, week in enumerate(weeks):
        label = week["week_label"]
        if not label:
            continue
        m = re.match(r'第\d+周\s+(\d+)/(\d+)-(\d+)/(\d+)', label)
        if not m:
            continue
        start_m, start_d, end_m, end_d = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))

        real_dates = infer_real_dates(week, year)
        valid_dates = [d for d in real_dates if d is not None]
        if not valid_dates:
            continue

        first_real = min(valid_dates)
        last_real = max(valid_dates)

        # 构造标签起止的真实 date（年的处理：如果end_m < start_m → 跨年）
        start_year = year
        end_year = year
        if start_m == 12 and end_m == 1:
            end_year = year + 1
        elif start_m > end_m:
            # 不常规，跳过
            continue

        try:
            label_start = date(start_year, start_m, start_d)
            label_end = date(end_year, end_m, end_d)
        except ValueError:
            continue

        if first_real != label_start:
            errors.append(
                f"【周标签不匹配】「{label}」标注起始{start_m}/{start_d}({label_start})，"
                f"实际起始为{first_real.month}/{first_real.day}({first_real})"
            )
        if last_real != label_end:
            errors.append(
                f"【周标签不匹配】「{label}」标注结束{end_m}/{end_d}({label_end})，"
                f"实际结束为{last_real.month}/{last_real.day}({last_real})"
            )
    return errors


def main():
    html_path = parse_args()
    if not os.path.exists(html_path):
        print(f"❌ 文件不存在: {html_path}")
        sys.exit(2)

    # 从文件名或当前时间推断年份，默认2026
    year = 2026
    # 如果文件名含年份
    m = re.search(r'(\d{4})', os.path.basename(html_path))
    if m:
        y = int(m.group(1))
        if 2020 <= y <= 2030:
            year = y

    print(f"🔍 日期-星期对齐校验 — {os.path.basename(html_path)} (年份={year})")
    print("=" * 60)

    with open(html_path, "r", encoding="utf-8") as f:
        html = f.read()

    weeks = extract_all_weeks(html)
    print(f"📊 找到 {len(weeks)} 个周行")

    all_errors = []

    print("\n1️⃣  检查：每日星期位置 & 跨月标记")
    errs = check_weekday_alignment(weeks, year)
    if errs:
        for e in errs:
            print(f"  ❌ {e}")
        all_errors.extend(errs)
    else:
        print("  ✅ 通过")

    print("\n2️⃣  检查：每月第一天的星期位置")
    errs = check_first_day_position(weeks, year)
    if errs:
        for e in errs:
            print(f"  ❌ {e}")
        all_errors.extend(errs)
    else:
        print("  ✅ 通过")

    print("\n3️⃣  检查：周标签与实际日期范围一致")
    errs = check_week_label(weeks, year)
    if errs:
        for e in errs:
            print(f"  ❌ {e}")
        all_errors.extend(errs)
    else:
        print("  ✅ 通过")

    print("\n" + "=" * 60)
    if all_errors:
        print(f"❌ 共发现 {len(all_errors)} 个错误")
        for i, e in enumerate(all_errors, 1):
            print(f"  {i}. {e}")
        sys.exit(1)
    else:
        print("✅ 日期-星期对齐校验全部通过")
        sys.exit(0)


if __name__ == "__main__":
    main()
