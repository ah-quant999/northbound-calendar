#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
机游共振日历批量回刷脚本
使用东方财富官方API，按日期逐个回刷指定日期范围的数据。
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

from update_jiyou_resonance_calendar import (
    build_daily_data,
    update_html,
    format_amount,
    is_a_stock_holiday,
)


def extract_day_snippet(html: str, day: int, month: int) -> str:
    """从HTML中提取指定日期单元格的文本片段，用于对比变化"""
    # 先在本月区域找
    for m in [month, month + 1]:
        month_pattern = rf'<div class="month-section[^"]*" id="month-{m}"'
        mm = re.search(month_pattern, html)
        if not mm:
            continue
        section_start = mm.start()
        next_section = re.search(r'<div class="month-section', html[section_start + 1:])
        section_end = section_start + 1 + next_section.start() if next_section else len(html)
        section_html = html[section_start:section_end]

        cell_pattern = re.compile(
            rf'<div class="day-cell">.*?<span class="day-number">\s*{day}\s*</span>.*?</div>\s*</div>\s*</td>',
            re.DOTALL,
        )
        cm = cell_pattern.search(section_html)
        if cm:
            text = cm.group(0)
            text = re.sub(r'<[^>]+>', '', text)
            text = re.sub(r'\s+', ' ', text).strip()
            return text
    return ""


def run_validate(script_name: str, html_path: str) -> dict:
    """运行一个校验脚本，返回结果"""
    script_path = os.path.join(SCRIPT_DIR, script_name)
    if not os.path.exists(script_path):
        return {"name": script_name, "ok": False, "error": "脚本不存在"}
    try:
        result = subprocess.run(
            [sys.executable, script_path, html_path],
            capture_output=True, text=True, timeout=60,
        )
        ok = result.returncode == 0
        return {
            "name": script_name,
            "ok": ok,
            "stdout": result.stdout.strip(),
            "stderr": result.stderr.strip(),
            "returncode": result.returncode,
        }
    except Exception as e:
        return {"name": script_name, "ok": False, "error": str(e)}


def run_validate_with_range(script_name: str, html_path: str) -> dict:
    """运行 validate_data_consistency.py（带 --range 和 --mode 参数）"""
    script_path = os.path.join(SCRIPT_DIR, script_name)
    if not os.path.exists(script_path):
        return {"name": script_name, "ok": False, "error": "脚本不存在"}
    try:
        result = subprocess.run(
            [sys.executable, script_path,
             "--range", "2026-06-29", "2026-07-31",
             "--mode", "html",
             "--html-path", html_path],
            capture_output=True, text=True, timeout=60,
        )
        ok = result.returncode == 0
        return {
            "name": script_name,
            "ok": ok,
            "stdout": result.stdout.strip(),
            "stderr": result.stderr.strip(),
            "returncode": result.returncode,
        }
    except Exception as e:
        return {"name": script_name, "ok": False, "error": str(e)}


def main():
    parser = argparse.ArgumentParser(description="机游共振日历批量回刷脚本")
    parser.add_argument("--html_path", required=True, help="HTML文件路径")
    parser.add_argument("--dates", nargs="+", required=True, help="需要回刷的日期列表")
    parser.add_argument("--no-backup", action="store_true", help="不备份")
    parser.add_argument("--validate", action="store_true", default=True, help="运行校验脚本")
    parser.add_argument("--push", action="store_true", help="完成后git push")
    parser.add_argument("--repo_path", default="/tmp/cal-repo/", help="仓库路径")
    args = parser.parse_args()

    html_path = args.html_path
    dates = args.dates

    if not os.path.exists(html_path):
        print(f"❌ HTML文件不存在: {html_path}")
        sys.exit(1)

    # 1. 备份
    if not args.no_backup:
        backup_path = html_path + f".backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        shutil.copy2(html_path, backup_path)
        print(f"📦 已备份原始文件: {backup_path}")
    else:
        backup_path = None

    # 2. 保存原始HTML快照
    with open(html_path, "r", encoding="utf-8") as f:
        original_html = f.read()

    before_snippets = {}
    for date_str in dates:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        snippet = extract_day_snippet(original_html, dt.day, dt.month)
        before_snippets[date_str] = snippet

    # 3. 逐个日期更新
    results = []
    skipped_dates = []
    for date_str in dates:
        print(f"\n{'='*60}")
        print(f"📅 正在处理 {date_str}")
        print(f"{'='*60}")

        # 检查是否为周末或法定假日
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        if dt.weekday() >= 5:
            print(f"📅 {date_str} 是周末，休市，跳过")
            skipped_dates.append(date_str)
            continue
        if is_a_stock_holiday(date_str):
            print(f"🏛️ {date_str} 是A股法定假日，休市，跳过")
            skipped_dates.append(date_str)
            continue

        try:
            daily_data = build_daily_data(date_str)
            ok = update_html(html_path, daily_data)
            results.append({
                "date": date_str,
                "ok": ok,
                "inst_top5": len(daily_data.institution_top5),
                "inst_sell_top3": len(daily_data.institution_sell_top3),
                "youzi_buy_top5": len(daily_data.youzi_buy_top5),
                "youzi_sell_top3": len(daily_data.youzi_sell_top3),
                "resonance": len(daily_data.resonance),
            })
            print(f"✅ {date_str} 处理完成: 机构TOP5={len(daily_data.institution_top5)}, "
                  f"游资买TOP5={len(daily_data.youzi_buy_top5)}, 共振={len(daily_data.resonance)}")
        except Exception as e:
            import traceback
            traceback.print_exc()
            results.append({
                "date": date_str,
                "ok": False,
                "error": str(e),
            })
            print(f"❌ {date_str} 处理失败: {e}")

    # 4. 对比变化
    print(f"\n{'='*60}")
    print("🔍 数据变化对比")
    print(f"{'='*60}")

    with open(html_path, "r", encoding="utf-8") as f:
        updated_html = f.read()

    changed_dates = []
    unchanged_dates = []
    failed_dates = []

    for r in results:
        date_str = r["date"]
        if not r["ok"]:
            failed_dates.append(r)
            print(f"❌ {date_str}: 更新失败 - {r.get('error', '未知错误')}")
            continue

        dt = datetime.strptime(date_str, "%Y-%m-%d")
        after_snippet = extract_day_snippet(updated_html, dt.day, dt.month)
        before_snippet = before_snippets.get(date_str, "")

        if before_snippet != after_snippet:
            changed_dates.append(r)
            print(f"🔄 {date_str}: 数据已变更")
            print(f"   机构TOP5:{r['inst_top5']}只 卖出TOP3:{r['inst_sell_top3']}只 "
                  f"游资买TOP5:{r['youzi_buy_top5']}只 卖TOP3:{r['youzi_sell_top3']}只 "
                  f"共振:{r['resonance']}个")
        else:
            unchanged_dates.append(r)
            print(f"➡️  {date_str}: 数据无变化")

    if skipped_dates:
        print(f"\n⏭️  跳过的日期（周末/假日）: {', '.join(skipped_dates)}")

    print(f"\n📊 总计: {len(dates)}天, 成功处理: {len(results)}天, "
          f"变更: {len(changed_dates)}天, 无变化: {len(unchanged_dates)}天, "
          f"失败: {len(failed_dates)}天, 跳过: {len(skipped_dates)}天")

    if failed_dates:
        print("⚠️  有日期更新失败，不进行后续校验和推送")
        sys.exit(1)

    # 5. 运行4个校验脚本
    if args.validate:
        print(f"\n{'='*60}")
        print("✅ 运行全量校验脚本")
        print(f"{'='*60}")

        validate_scripts = [
            "validate_calendar_html.py",
            "validate_data_consistency.py",
            "validate_date_alignment.py",
            "validate_format_uniformity.py",
        ]

        all_ok = True
        for vs in validate_scripts:
            print(f"\n--- {vs} ---")
            if vs == "validate_data_consistency.py":
                vr = run_validate_with_range(vs, html_path)
            else:
                vr = run_validate(vs, html_path)
            if vr["ok"]:
                print(f"✅ 通过")
                if vr.get("stdout"):
                    lines = vr["stdout"].strip().split("\n")
                    for line in lines[-5:]:
                        print(f"   {line}")
            else:
                print(f"❌ 失败 (exit code: {vr.get('returncode', '?')})")
                print(f"   stdout: {vr.get('stdout', '')[:800]}")
                print(f"   stderr: {vr.get('stderr', '')[:800]}")
                all_ok = False

        if not all_ok:
            print("\n❌ 校验未全部通过，不执行推送")
            sys.exit(1)
        else:
            print("\n🎉 全部4个校验脚本通过！")

    # 6. Git提交推送
    if args.push:
        print(f"\n{'='*60}")
        print("📤 提交并推送到GitHub")
        print(f"{'='*60}")

        repo_path = args.repo_path
        file_name = os.path.basename(html_path)

        from calendar_git import calendar_git_setup, calendar_git_push

        if not calendar_git_setup(repo_path):
            print("❌ Git初始化失败")
            sys.exit(1)

        dst = os.path.join(repo_path, file_name)
        src_abs = os.path.abspath(html_path)
        dst_abs = os.path.abspath(dst)
        if src_abs != dst_abs:
            shutil.copy2(html_path, dst)
        jiyou_dst = os.path.join(repo_path, "jiyou-resonance.html")
        if src_abs != os.path.abspath(jiyou_dst):
            shutil.copy2(html_path, jiyou_dst)
        print(f"📄 已复制到仓库: {dst}, jiyou-resonance.html")

        script_files = [
            "update_jiyou_resonance_calendar.py",
            "backfill_jiyou_resonance.py",
            "validate_calendar_html.py",
            "validate_data_consistency.py",
            "validate_date_alignment.py",
            "validate_format_uniformity.py",
        ]
        for sf in script_files:
            src = os.path.join(SCRIPT_DIR, sf)
            dst = os.path.join(repo_path, "scripts", sf)
            if os.path.exists(src) and os.path.abspath(src) != os.path.abspath(dst):
                shutil.copy2(src, dst)

        date_range_str = f"{dates[0]}~{dates[-1]}"
        commit_msg = (f"auto: 机游共振日历批量回刷 {date_range_str} (东财官方API)\n\n"
                      f"回刷日期: {', '.join(dates)}\n"
                      f"变更天数: {len(changed_dates)}\n"
                      f"无变化: {len(unchanged_dates)}\n"
                      f"跳过(周末/假日): {len(skipped_dates)}")

        files_to_push = [file_name, "jiyou-resonance.html"] + \
                        [f"scripts/{s}" for s in script_files
                         if os.path.exists(os.path.join(SCRIPT_DIR, s))]

        push_ok = calendar_git_push(repo_path, files_to_push, commit_msg)

        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_path, capture_output=True, text=True, timeout=10,
        )
        commit_hash = result.stdout.strip()[:7] if result.returncode == 0 else "unknown"

        if push_ok:
            print(f"✅ 推送成功！commit: {commit_hash}")
        else:
            print(f"❌ 推送失败！commit: {commit_hash}")
            sys.exit(1)

    # 7. 输出汇总
    print(f"\n{'='*60}")
    print("📋 回刷完成汇总")
    print(f"{'='*60}")
    print(f"回刷日期: {', '.join(dates)}")
    print(f"总天数: {len(dates)}")
    print(f"成功处理: {len(results)}天")
    print(f"变更天数: {len(changed_dates)}")
    print(f"无变化天数: {len(unchanged_dates)}")
    print(f"失败天数: {len(failed_dates)}")
    print(f"跳过(周末/假日): {len(skipped_dates)}")
    if changed_dates:
        print(f"\n变更日期清单:")
        for r in changed_dates:
            print(f"  - {r['date']}")
    if skipped_dates:
        print(f"\n跳过日期清单: {', '.join(skipped_dates)}")
    if backup_path:
        print(f"\n备份文件: {backup_path}")
    print()


if __name__ == "__main__":
    main()
