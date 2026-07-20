#!/usr/bin/env python3
"""
T+1 早间兜底复核脚本

用途：
  每个交易日次日（T+1 日）早上运行，对 T 日的北向资金和机游共振数据
  做一次 force 重跑补抓，防止 T 日有晚披露或遗漏的数据。

逻辑：
  1. 计算 T 日 = 当前北京时间 - 1 天（或使用 --date 指定的 T 日）
  2. 判断 T 日是否为 A 股交易日（非周末、非节假日），非交易日直接跳过
  3. 是交易日则：
       - 先跑北向资金 T 日更新（--lookback 0，仅 T 日）
       - 再跑机游共振 T 日更新（--lookback 0，仅 T 日）
  4. 检查 git 状态，有变更输出 GHA_HAS_CHANGES=true 并列出变更文件

GHA 输出标记：
  GHA_NO_CHANGE=true   — 无变更或非交易日跳过
  GHA_HAS_CHANGES=true — 有数据变更需要提交
  GHA_TARGET_DATE=YYYY-MM-DD — T 日日期
  GHA_CHANGED_FILES=... — 变更的文件列表（空格分隔）

用法：
  python3 scripts/t1_morning_fallback.py --repo-dir .
  python3 scripts/t1_morning_fallback.py --date 2026-07-14 --repo-dir .
  python3 scripts/t1_morning_fallback.py --date 2026-07-14 --repo-dir . --dry-run
"""

import argparse
import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

# 复用机游脚本的交易日判断逻辑（is_trading_day 基于 A_STOCK_HOLIDAYS_2026 + 周末判断）
from update_jiyou_resonance_gha import is_trading_day  # noqa: E402


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
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    print(f"  ↩️  exit_code = {result.returncode}")
    return result


def beijing_today() -> str:
    """返回当前北京时间的日期字符串（YYYY-MM-DD）

    GitHub Actions 运行在 UTC 时区，需要 +8 小时得到北京时间。
    为了不依赖外部库，直接计算。
    """
    utc_now = datetime.utcnow()
    bj_now = utc_now + timedelta(hours=8)
    return bj_now.strftime("%Y-%m-%d")


# T+1 兜底脚本只关心以下数据文件的变更（workflow 本身的修改不在这里提交）
WATCH_FILES = [
    "北向资金日历.html",
    "机游共振日历.html",
    "index.html",
]


def has_git_changes(repo_dir: str) -> tuple[bool, list]:
    """检查 git 中 WATCH_FILES 是否有变更，返回 (是否有变更, 变更文件列表)

    只检查已跟踪文件的修改（工作区 + 暂存区），不包含未跟踪文件。
    这样可以避免把 workflow 脚本本身、临时文件等一起提交。
    """
    changed = set()

    # 工作区变更
    r1 = run_cmd(["git", "diff", "--name-only", "--"] + WATCH_FILES, cwd=repo_dir, timeout=30)
    if r1.stdout.strip():
        changed.update(r1.stdout.strip().split("\n"))

    # 暂存区变更
    r2 = run_cmd(["git", "diff", "--cached", "--name-only", "--"] + WATCH_FILES, cwd=repo_dir, timeout=30)
    if r2.stdout.strip():
        changed.update(r2.stdout.strip().split("\n"))

    # 过滤掉空串
    changed = sorted([f for f in changed if f])
    return (len(changed) > 0, changed)


def run_northbound_update(repo_dir: str, target_date: str) -> bool:
    """运行北向资金 T 日更新（--lookback 0，只更新 T 日）"""
    script_path = str(SCRIPT_DIR / "daily_update_northbound_gha.py")
    cmd = [
        sys.executable, script_path,
        "--html", "北向资金日历.html",
        "--date", target_date,
        "--lookback", "0",
        "--repo-dir", repo_dir,
    ]
    r = run_cmd(cmd, cwd=repo_dir, timeout=300)
    return r.returncode == 0


def run_jiyou_update(repo_dir: str, target_date: str) -> bool:
    """运行机游共振 T 日更新（--lookback 0，只更新 T 日）"""
    script_path = str(SCRIPT_DIR / "daily_update_jiyou_gha.py")
    cmd = [
        sys.executable, script_path,
        "--html", "机游共振日历.html",
        "--date", target_date,
        "--lookback", "0",
        "--repo-dir", repo_dir,
        # 兜底重跑不做完整校验（日常流水线已经做过），加快速度
        "--skip-validate",
    ]
    r = run_cmd(cmd, cwd=repo_dir, timeout=300)
    return r.returncode == 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="T+1 早间兜底复核脚本（北向 + 机游共振）"
    )
    parser.add_argument("--date", default="",
                        help="指定 T 日 (YYYY-MM-DD)，留空则自动计算为北京时间昨日")
    parser.add_argument("--repo-dir", default=".",
                        help="仓库根目录（默认当前目录）")
    parser.add_argument("--dry-run", action="store_true",
                        help="只计算 T 日和判断交易日，不实际执行更新")
    args = parser.parse_args()

    repo_dir = str(Path(args.repo_dir).resolve())

    # 计算 T 日
    if args.date:
        target_date = args.date
        date_source = "命令行指定"
    else:
        bj_today = beijing_today()
        target_date = (datetime.strptime(bj_today, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
        date_source = "自动计算（北京时间昨日）"

    print("=" * 60)
    print("🌅 T+1 早间兜底复核流水线")
    print(f"📅 T 日: {target_date}（{date_source}）")
    print(f"📁 仓库目录: {repo_dir}")
    print(f"🧪 Dry-run: {'是' if args.dry_run else '否'}")
    print("=" * 60)

    # 检查 HTML 文件是否存在
    nb_html = os.path.join(repo_dir, "北向资金日历.html")
    jy_html = os.path.join(repo_dir, "机游共振日历.html")
    if not os.path.isfile(nb_html):
        log_error(f"北向 HTML 文件不存在: {nb_html}")
        sys.exit(1)
    if not os.path.isfile(jy_html):
        log_error(f"机游 HTML 文件不存在: {jy_html}")
        sys.exit(1)

    # 判断 T 日是否为交易日
    if not is_trading_day(target_date):
        log_warn(f"{target_date} 是非交易日（周末或节假日），跳过兜底更新")
        print("GHA_NO_CHANGE=true")
        print(f"GHA_TARGET_DATE={target_date}")
        print("ℹ️  流水线完成（非交易日跳过）")
        sys.exit(0)

    print(f"✅ {target_date} 是 A 股交易日，开始兜底更新")

    if args.dry_run:
        log_info("Dry-run 模式，跳过实际更新")
        print(f"GHA_TARGET_DATE={target_date}")
        print("GHA_NO_CHANGE=true")
        print("ℹ️  Dry-run 完成")
        sys.exit(0)

    # 步骤1：北向资金 T 日更新
    log_info("步骤1/2：北向资金 T 日兜底更新")
    if not run_northbound_update(repo_dir, target_date):
        log_warn("北向资金更新失败（不阻塞机游更新，继续执行）")

    # 步骤2：机游共振 T 日更新
    log_info("步骤2/3：机游共振 T 日兜底更新")
    if not run_jiyou_update(repo_dir, target_date):
        log_error("机游共振更新失败")
        sys.exit(1)

    # 步骤3：重新生成每日市场洞察（确保两端数据都更新完后，洞察同步刷新）
    log_info("步骤3/3：重新生成每日市场洞察")
    insight_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "daily_insight.py")
    insight_output = os.path.join(repo_dir, "daily-insight.html")
    jy_analysis = os.path.join(repo_dir, "jiyou-signal-analysis.html")
    nb_analysis = os.path.join(repo_dir, "northbound-analysis.html")
    if os.path.isfile(insight_script) and os.path.isfile(jy_analysis) and os.path.isfile(nb_analysis):
        r_insight = subprocess.run(
            ["python3", insight_script,
             "--jiyou-html", jy_analysis,
             "--nb-html", nb_analysis,
             "--output", insight_output],
            capture_output=True, text=True, cwd=repo_dir
        )
        if r_insight.returncode == 0:
            log_info("✅ 每日洞察重新生成成功")
        else:
            log_warn(f"每日洞察重新生成失败（返回码={r_insight.returncode}），不阻塞整体流程")
    else:
        log_warn("洞察脚本或分析页不存在，跳过每日洞察重新生成")

    # 检查 git 变更
    log_info("检查 git 变更")
    changed, changed_files = has_git_changes(repo_dir)

    print()
    print("=" * 60)
    if changed:
        print("✅ 有数据变更，需要提交推送")
        print(f"📄 变更文件: {', '.join(changed_files)}")
        print("GHA_HAS_CHANGES=true")
        print(f"GHA_TARGET_DATE={target_date}")
        print(f"GHA_CHANGED_FILES={' '.join(changed_files)}")
    else:
        print("ℹ️  无数据变更（T 日数据与日常更新一致，无需补抓）")
        print("GHA_NO_CHANGE=true")
        print(f"GHA_TARGET_DATE={target_date}")
    print("🎉 T+1 兜底流水线完成")
    print("=" * 60)

    sys.exit(0)


if __name__ == "__main__":
    main()
