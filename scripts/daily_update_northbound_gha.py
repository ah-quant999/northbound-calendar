#!/usr/bin/env python3
"""
北向资金日历 — 每日更新流水线（GitHub Actions 纯 Python 版）

完全不依赖 CodeActSDK / pydantic，只用标准库 + requests。
运行在 GitHub Actions 的 ubuntu-latest 环境中，当前目录就是仓库根目录。

流程：
  1. 当日数据更新
  2. 回刷前 2 个交易日（防漏更）
  3. 同步 index.html（复制主HTML → index.html）
  4. 有变更则输出标志（供 Actions commit/push）

用法：
  python3 daily_update_northbound_gha.py --html 北向资金日历.html
  python3 daily_update_northbound_gha.py --html 北向资金日历.html --date 2026-07-14
  python3 daily_update_northbound_gha.py --html 北向资金日历.html --lookback 3
"""

import argparse
import os
import shutil
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from update_northbound_gha import is_northbound_open  # noqa: E402
from update_northbound_gha import (
    update_weekly_summary as northbound_update_weekly,
    update_monthly_summary as northbound_update_monthly,
)  # noqa: E402


def log_info(msg: str) -> None:
    print()
    print(f"🟢 {msg}")


def log_warn(msg: str) -> None:
    print(f"🟡 {msg}")


def log_error(msg: str) -> None:
    print(f"🔴 {msg}", file=sys.stderr)


def run_cmd(cmd: list, cwd: str | None = None, timeout: int = 300) -> subprocess.CompletedProcess:
    """运行命令，返回 CompletedProcess（不抛异常）"""
    print(f"  $ {' '.join(cmd)}")
    result = subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.stdout:
        lines = result.stdout.strip().splitlines()
        for line in lines[-10:]:
            print(f"  | {line}")
        if len(lines) > 10:
            print(f"  ... (共 {len(lines)} 行 stdout)")
    if result.returncode != 0 and result.stderr:
        lines = result.stderr.strip().splitlines()
        for line in lines[-10:]:
            print(f"  ERR | {line}")
    return result


def get_prev_trading_days(date_str: str, n: int) -> list:
    """获取 date_str 之前的 n 个北向交易日（不含当天）"""
    dates = []
    cur = datetime.strptime(date_str, "%Y-%m-%d") - timedelta(days=1)
    while len(dates) < n:
        ds = cur.strftime("%Y-%m-%d")
        if is_northbound_open(ds):
            dates.append(ds)
        cur -= timedelta(days=1)
        if cur < datetime(2020, 1, 1):
            break
    return dates


def run_update(html_path: str, date_str: str) -> bool:
    """运行单日更新，成功返回True"""
    update_script = str(SCRIPT_DIR / "update_northbound_gha.py")
    r = run_cmd([
        sys.executable, update_script,
        "--date", date_str,
        "--html", html_path,
    ], timeout=120)
    if r.returncode != 0:
        log_error(f"更新 {date_str} 失败（返回码={r.returncode}）")
        return False
    return True


def has_git_changes(repo_dir: str, files: list) -> bool:
    """检查git是否有变更"""
    r1 = run_cmd(["git", "diff", "--quiet", "--"] + files, cwd=repo_dir, timeout=30)
    if r1.returncode != 0:
        return True
    r2 = run_cmd(["git", "diff", "--cached", "--quiet", "--"] + files, cwd=repo_dir, timeout=30)
    if r2.returncode != 0:
        return True
    # 也检查是否有未跟踪文件
    r3 = run_cmd(["git", "status", "--porcelain", "--"] + files, cwd=repo_dir, timeout=30)
    if r3.stdout.strip():
        return True
    return False


def main():
    parser = argparse.ArgumentParser(
        description="北向资金日历每日更新流水线 (GHA纯Python版)"
    )
    parser.add_argument("--html", default="北向资金日历.html",
                        help="主HTML文件路径（默认：北向资金日历.html）")
    parser.add_argument("--date", default="",
                        help="目标日期（默认今天）")
    parser.add_argument("--lookback", type=int, default=2,
                        help="回刷前N个北向交易日（默认2）")
    parser.add_argument("--repo-dir", default=".",
                        help="仓库根目录（默认当前目录）")
    args = parser.parse_args()

    repo_dir = str(Path(args.repo_dir).resolve())
    html_file = args.html
    html_path = os.path.join(repo_dir, html_file) if not os.path.isabs(html_file) else html_file
    index_path = os.path.join(os.path.dirname(html_path), "index.html")

    target_date = args.date or datetime.now().strftime("%Y-%m-%d")

    print("=" * 60)
    print("🚀 北向资金日历 — 每日更新流水线 (GHA版)")
    print(f"📅 目标日期: {target_date}")
    print(f"📁 仓库目录: {repo_dir}")
    print(f"📄 HTML 文件: {html_path}")
    print(f"🔁 回刷天数: 前 {args.lookback} 个北向交易日")
    print("=" * 60)

    # 检查 HTML 文件是否存在
    if not os.path.isfile(html_path):
        log_error(f"HTML 文件不存在: {html_path}")
        sys.exit(1)

    # 记录变更前文件大小
    size_before = os.path.getsize(html_path)
    print(f"📏 更新前文件大小: {size_before} 字节")

    # 计算待更新日期列表
    update_dates = []
    if is_northbound_open(target_date):
        update_dates.append(target_date)
    else:
        log_warn(f"{target_date} 北向通道未开放（非交易日或港股休市），跳过当日更新")

    prev_days = get_prev_trading_days(target_date, args.lookback)
    update_dates.extend(prev_days)

    if not update_dates:
        log_warn("没有需要更新的日期")
        print("GHA_NO_CHANGE=true")
        sys.exit(0)

    print(f"📋 待更新日期: {', '.join(update_dates)}")

    # 步骤1：逐个更新
    log_info("步骤1/4：更新北向资金每日数据")
    all_ok = True
    for ds in update_dates:
        print(f"\n--- 更新 {ds} ---")
        if not run_update(html_path, ds):
            all_ok = False

    if not all_ok:
        log_error("数据更新失败")
        sys.exit(1)

    # 步骤2：更新周汇总
    log_info("步骤2/4：更新当周汇总TOP5")
    try:
        northbound_update_weekly(html_path, target_date)
    except Exception as e:
        log_error(f"周汇总更新异常: {e}")
        import traceback
        traceback.print_exc()

    # 步骤3：更新月度汇总
    log_info("步骤3/4：更新月度汇总TOP10")
    try:
        northbound_update_monthly(html_path, target_date)
    except Exception as e:
        log_error(f"月度汇总更新异常: {e}")
        import traceback
        traceback.print_exc()

    # 步骤4：同步 index.html
    log_info("步骤4/5：同步 index.html")
    shutil.copy2(html_path, index_path)
    print(f"✅ index.html 已同步: {index_path}")

    # 步骤5：更新北向分析页
    log_info("步骤5/5：更新北向分析页面")
    analysis_html = os.path.join(os.path.dirname(html_path), "northbound-analysis.html")
    analysis_script = str(SCRIPT_DIR / "northbound_analysis.py")
    for ds in update_dates:
        if not is_northbound_open(ds):
            continue
        print(f"\n--- 北向分析 {ds} ---")
        r_analysis = run_cmd([
            sys.executable, analysis_script,
            "--date", ds,
            "--html", analysis_html,
            "--repo-dir", os.path.dirname(html_path),
        ], timeout=180)
        if r_analysis.returncode != 0:
            log_warn(f"北向分析页更新 {ds} 失败（返回码={r_analysis.returncode}），继续后续流程")

    # 检查是否有变更
    size_after = os.path.getsize(html_path)
    print(f"\n📏 更新后文件大小: {size_after} 字节")

    # 检查 git 状态
    changed = has_git_changes(repo_dir, [html_file, "index.html", "northbound-analysis.html"])

    print()
    print("=" * 60)
    if changed:
        print("✅ 有数据变更，需要提交推送")
        print("GHA_HAS_CHANGES=true")
        print(f"GHA_TARGET_DATE={target_date}")
    else:
        print("ℹ️  无数据变更")
        print("GHA_NO_CHANGE=true")
    print("🎉 流水线完成")
    print("=" * 60)

    sys.exit(0)


if __name__ == "__main__":
    main()
