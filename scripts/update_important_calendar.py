#!/usr/bin/env python3
"""
每月1号自动运行：重新生成重要日历HTML文件（19个月），
复制到GitHub仓库 northbound-calendar 并通过 calendar_git 安全模块推送到 calendar-pages 分支。

用法:
    python update_important_calendar.py [result_mode]

参数:
    result_mode: display_only（默认，每月例行展示）
"""

import asyncio
import os
import shutil
import subprocess
import sys
from codeact_sdk import CodeActSDK
from calendar_git import calendar_git_setup, calendar_git_push, calendar_git_pull

# ==================== 常量配置 ====================

# 日历生成脚本路径
GENERATE_SCRIPT = "/app/data/所有对话/主对话/codeact/scripts/generate_calendars.py"
# 日历输出目录
CALENDAR_OUTPUT_DIR = "/app/data/所有对话/主对话/重要日历"
# Git仓库本地目录
GIT_REPO_DIR = "/tmp/nb-calendar/"
# 需要复制的文件列表（19个月，2026-06 ~ 2027-12）
CALENDAR_FILES = [
    f"重要日历_{y}{m:02d}.html"
    for y in range(2026, 2028)
    for m in range(1, 13)
    if (y == 2026 and m >= 6) or (y == 2027 and m <= 12)
]


def run_cmd(cmd: list[str], cwd: str | None = None, timeout: int = 120) -> str:
    """执行系统命令（仅用于 python3 等非 git 命令），返回stdout，失败时抛异常"""
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=cwd,
    )
    if result.returncode != 0:
        err = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"命令执行失败 (exit={result.returncode}): {err[:500]}")
    return result.stdout.strip()


async def main():
    result_mode = sys.argv[1] if len(sys.argv) > 1 else "display_only"
    actual_mode = result_mode if result_mode != "auto" else "display_only"

    print(f"[参数] result_mode={result_mode}, actual_mode={actual_mode}")
    print(f"[配置] 日历文件数: {len(CALENDAR_FILES)}")
    print(f"[配置] Git仓库: {GIT_REPO_DIR}")

    sdk = CodeActSDK()

    try:
        # ========== Step 0: Git 初始化（自动clone + 分支校验） ==========
        print("\n[Step 0] calendar_git 初始化（自动clone + 分支强制校验）...")
        if not calendar_git_setup(GIT_REPO_DIR):
            raise RuntimeError("calendar_git_setup 失败：无法初始化仓库或切换到 calendar-pages 分支")
        print("✅ Git 初始化完成，已确认在 calendar-pages 分支")

        # ========== Step 1: 生成最新日历HTML ==========
        print("\n[Step 1] 运行 generate_calendars.py 生成最新日历文件...")
        stdout = run_cmd(
            ["python3", GENERATE_SCRIPT,
             "--start-year", "2026", "--start-month", "6",
             "--end-year", "2027", "--end-month", "12",
             "--output-dir", CALENDAR_OUTPUT_DIR],
            timeout=300,
        )
        print(stdout)

        # 验证文件是否生成
        missing = []
        for fname in CALENDAR_FILES:
            fpath = os.path.join(CALENDAR_OUTPUT_DIR, fname)
            if not os.path.isfile(fpath):
                missing.append(fname)
        if missing:
            raise RuntimeError(f"缺失日历文件 ({len(missing)}个): {missing[:5]}...")
        print(f"✅ 所有 {len(CALENDAR_FILES)} 个日历文件已生成")

        # ========== Step 2: Git Pull ==========
        print("\n[Step 2] 通过 calendar_git_pull 拉取最新...")
        if not calendar_git_pull(GIT_REPO_DIR):
            print("⚠️ Git pull 失败，继续尝试推送")

        # ========== Step 3: 复制文件到仓库 ==========
        print("\n[Step 3] 复制日历文件到Git仓库...")
        copied = 0
        for fname in CALENDAR_FILES:
            src = os.path.join(CALENDAR_OUTPUT_DIR, fname)
            dst = os.path.join(GIT_REPO_DIR, fname)
            shutil.copy2(src, dst)
            copied += 1
        print(f"✅ 已复制 {copied} 个文件到 {GIT_REPO_DIR}")

        # ========== Step 4: Git Add + Commit + Push (通过 calendar_git 安全模块) ==========
        from datetime import datetime
        today = datetime.now()
        commit_msg = f"chore: 更新重要日历 ({today.strftime('%Y年%m月')})"
        print(f"\n[Step 4] 通过 calendar_git_push 推送（分支强制校验）...")
        print(f"  commit_msg: {commit_msg}")
        print(f"  files: {len(CALENDAR_FILES)} 个")

        if not calendar_git_push(GIT_REPO_DIR, CALENDAR_FILES, commit_msg):
            raise RuntimeError("calendar_git_push 失败：推送被拒绝")

        # ========== 提交结果 ==========
        summary = (
            f"重要日历更新完成\n"
            f"- 生成并推送 {copied} 个日历文件（2026年6月~2027年12月）\n"
            f"- 提交信息: {commit_msg}\n"
            f"- 通过calendar_git安全模块推送到calendar-pages分支\n"
            f"- GitHub仓库: https://github.com/ah-quant999/northbound-calendar"
        )
        print(f"\n{summary}")

        await sdk.submit_result(
            result_mode=actual_mode,
            status="success",
            message=summary,
            data={
                "action": "updated_and_pushed",
                "file_count": copied,
                "commit_message": commit_msg,
                "repo": "ah-quant999/northbound-calendar",
            },
        )

    except Exception as e:
        error_msg = f"重要日历更新失败: {e}"
        print(f"\n❌ {error_msg}")
        await sdk.submit_result(
            result_mode="notify",
            status="error",
            message=error_msg,
            data={"error_type": type(e).__name__},
        )

asyncio.run(main())
