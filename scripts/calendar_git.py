#!/usr/bin/env python3
"""
日历精选 Git 分支强制管控模块
所有日历相关脚本必须通过本模块操作 git，确保只推 calendar-pages 分支。

用法:
    from calendar_git import calendar_git_setup, calendar_git_push, calendar_git_pull

    # 在脚本开头调用一次
    calendar_git_setup(repo_path)

    # 拉取
    calendar_git_pull(repo_path)

    # 推送
    calendar_git_push(repo_path, ["index.html"], "auto: 更新xxx")
"""

import os
import subprocess
from datetime import datetime

# ========== 硬编码配置（任何脚本不得绕过） ==========
FORCED_BRANCH = "calendar-pages"  # 唯一允许的推送分支
GIT_EMAIL = "afoxli@coze.email"
GIT_NAME = "afoxli"
TOKEN = os.environ.get("GITHUB_TOKEN", "")
REPO = "ah-quant999/northbound-calendar"


def _run_git(args: list, cwd: str, timeout: int = 30) -> subprocess.CompletedProcess:
    """执行 git 命令"""
    return subprocess.run(
        ["git"] + args,
        cwd=cwd, capture_output=True, text=True, timeout=timeout,
    )


def _ensure_branch(repo_path: str) -> bool:
    """强制切换到 calendar-pages 分支，失败则报错退出"""
    # 先 fetch
    _run_git(["fetch", "origin", FORCED_BRANCH], repo_path, timeout=30)

    # 检查当前分支
    result = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], repo_path)
    current = result.stdout.strip() if result.returncode == 0 else ""

    if current == FORCED_BRANCH:
        return True

    # 切换分支
    print(f"🔒 强制切换分支: {current} → {FORCED_BRANCH}")
    result = _run_git(["checkout", FORCED_BRANCH], repo_path)
    if result.returncode != 0:
        # 分支可能本地不存在，从远程创建
        result = _run_git(["checkout", "-b", FORCED_BRANCH, f"origin/{FORCED_BRANCH}"], repo_path)
        if result.returncode != 0:
            print(f"❌ 无法切换到 {FORCED_BRANCH}: {result.stderr.strip()}")
            return False

    # 二次确认
    result = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], repo_path)
    actual = result.stdout.strip()
    if actual != FORCED_BRANCH:
        print(f"❌ 分支校验失败! 当前={actual}, 期望={FORCED_BRANCH}")
        return False

    print(f"✅ 已确认在 {FORCED_BRANCH} 分支")
    return True


def calendar_git_setup(repo_path: str) -> bool:
    """
    初始化 git 配置并强制切换到 calendar-pages 分支。
    所有日历脚本在执行 git 操作前必须调用此函数。
    """
    if not os.path.isdir(repo_path):
        print(f"❌ 仓库路径不存在: {repo_path}")
        return False

    # 配置 git 身份
    _run_git(["config", "user.email", GIT_EMAIL], repo_path, timeout=10)
    _run_git(["config", "user.name", GIT_NAME], repo_path, timeout=10)

    # 设置 remote URL（含 token）
    remote_url = f"https://{TOKEN}@github.com/{REPO}.git"
    _run_git(["remote", "set-url", "origin", remote_url], repo_path, timeout=10)

    # 强制切换到正确分支
    return _ensure_branch(repo_path)


def calendar_git_pull(repo_path: str) -> bool:
    """从 calendar-pages 分支拉取最新代码"""
    if not _ensure_branch(repo_path):
        return False

    result = _run_git(["pull", "origin", FORCED_BRANCH], repo_path)
    print(f"📥 Git pull: {result.stdout.strip()}")
    if result.returncode != 0:
        print(f"⚠️ Git pull stderr: {result.stderr.strip()}")
    return result.returncode == 0 or "Already up to date" in result.stdout


def calendar_git_push(repo_path: str, files: list, commit_msg: str) -> bool:
    """
    将指定文件推送到 calendar-pages 分支。
    任何对其他分支的推送都会被拒绝。

    Args:
        repo_path: 仓库本地路径
        files: 要提交的文件列表（相对仓库根目录）
        commit_msg: commit 消息
    """
    if not _ensure_branch(repo_path):
        print("❌ 分支校验失败，拒绝推送")
        return False

    # 再次确认当前分支（双重保险）
    result = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], repo_path)
    current = result.stdout.strip()
    if current != FORCED_BRANCH:
        print(f"❌ 安全检查失败! 当前分支={current}, 只允许推送到 {FORCED_BRANCH}")
        return False

    # git add
    for f in files:
        _run_git(["add", f], repo_path, timeout=10)

    # git commit
    result = _run_git(
        ["commit", "-m", f"{commit_msg} [{datetime.now().strftime('%H:%M')}]"],
        repo_path, timeout=10,
    )
    print(f"📝 Commit: {result.stdout.strip()}")
    if "nothing to commit" in result.stdout:
        print("✅ 无变更需要提交")
        return True

    # git push（硬编码分支，不接受参数）
    result = _run_git(["push", "origin", FORCED_BRANCH], repo_path, timeout=30)
    print(f"📤 Push to {FORCED_BRANCH}: {result.stdout.strip()}")
    if result.returncode != 0:
        stderr = result.stderr.strip()
        if "Everything up-to-date" in stderr or "Everything up-to-date" in result.stdout:
            return True
        print(f"❌ Push 失败: {stderr}")
        return False

    return True
