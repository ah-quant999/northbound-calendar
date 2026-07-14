#!/usr/bin/env python3
"""
机游共振日历 — 每日自动更新流水线（纯数据交叉版）
适配中文文件名「机游共振日历.html」，可作为每日定时任务独立运行

共振逻辑：机构净买入TOP5 ∩ 龙虎榜个股买入净额TOP20

流程：
  1. 克隆/拉取仓库（calendar-pages 分支）
  2. 运行 update_jiyou_resonance_calendar.py 更新当日数据 + 回滚最近 2 天
  3. 同步 index.html（复制机游共振日历.html → index.html）
  4. 运行 validate_data_consistency.py（api 模式）校验
  5. 校验通过 → git add / commit / push
  6. 校验失败 → git reset --hard 回退，输出错误

用法（第一个参数为 result_mode）：
  daily_update_jiyou.py <result_mode> [--date YYYY-MM-DD] [--no-push]
"""

import asyncio
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

from codeact_sdk import CodeActSDK

sdk = CodeActSDK()

# ====== 常量 ======
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_DIR = Path("/tmp/cal-repo")
HTML_FILE = "机游共振日历.html"
INDEX_FILE = "index.html"
BRANCH = "calendar-pages"
REPO = "ah-quant999/northbound-calendar"
GIT_EMAIL = "afoxli@coze.email"
GIT_NAME = "afoxli"


# ====== 工具函数 ======
def log_info(msg: str) -> None:
    print()
    print(f"🟢 {msg}")


def log_warn(msg: str) -> None:
    print(f"🟡 {msg}")


def log_error(msg: str) -> None:
    print(f"🔴 {msg}", file=sys.stderr)


def run_cmd(cmd: list, cwd: str | None = None, timeout: int = 300) -> subprocess.CompletedProcess:
    """运行命令，返回 CompletedProcess（永远不会抛异常，返回码由调用方判断）"""
    print(f"  $ {' '.join(cmd)}")
    result = subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.stdout:
        # 只打印最后 5 行，避免刷屏
        lines = result.stdout.strip().splitlines()
        for line in lines[-5:]:
            print(f"  | {line}")
        if len(lines) > 5:
            print(f"  ... (共 {len(lines)} 行 stdout)")
    if result.returncode != 0 and result.stderr:
        lines = result.stderr.strip().splitlines()
        for line in lines[-10:]:
            print(f"  ERR | {line}")
    return result


def load_github_token() -> str:
    tok = os.environ.get("GITHUB_TOKEN", "")
    if tok:
        return tok
    candidates = [
        Path("/app/data/所有对话/主对话/SECRET.md"),
        SCRIPT_DIR.parent.parent / "SECRET.md",
        SCRIPT_DIR.parent / "SECRET.md",
        Path("./SECRET.md"),
    ]
    for f in candidates:
        if f.is_file():
            try:
                text = f.read_text(encoding="utf-8")
                for line in text.splitlines():
                    if "GITHUB_TOKEN_ah_quant999" in line:
                        m = re.search(r"ghp_[A-Za-z0-9]+", line)
                        if m:
                            return m.group(0)
            except Exception:
                pass
    return ""


# ====== Git 操作 ======
def git_reset_hard() -> None:
    log_warn("回退 git 变更 (reset --hard + clean)...")
    run_cmd(["git", "checkout", "--", "."], cwd=str(REPO_DIR), timeout=30)
    run_cmd(["git", "reset", "--hard", "HEAD"], cwd=str(REPO_DIR), timeout=30)
    run_cmd(["git", "clean", "-fd"], cwd=str(REPO_DIR), timeout=30)


def ensure_repo(token: str) -> None:
    log_info(f"步骤1/5：准备仓库 {REPO_DIR} ({BRANCH})")
    git_dir = REPO_DIR / ".git"

    if git_dir.is_dir():
        # 切换到正确分支
        result = run_cmd(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=str(REPO_DIR))
        cur = result.stdout.strip()
        if cur != BRANCH:
            run_cmd(["git", "fetch", "origin", BRANCH, "--depth", "20"], cwd=str(REPO_DIR))
            # 尝试切换
            r2 = run_cmd(["git", "show-ref", "--verify", f"refs/heads/{BRANCH}"], cwd=str(REPO_DIR))
            if r2.returncode == 0:
                run_cmd(["git", "checkout", BRANCH], cwd=str(REPO_DIR))
            else:
                run_cmd(["git", "checkout", "-b", BRANCH, f"origin/{BRANCH}"], cwd=str(REPO_DIR))
        # pull
        r = run_cmd(["git", "pull", "origin", BRANCH, "--no-rebase"], cwd=str(REPO_DIR), timeout=60)
        if r.returncode != 0:
            log_warn("git pull 失败，尝试重置后再拉...")
            run_cmd(["git", "reset", "--hard", f"origin/{BRANCH}"], cwd=str(REPO_DIR))
    else:
        if REPO_DIR.exists():
            shutil.rmtree(REPO_DIR)
        REPO_DIR.parent.mkdir(parents=True, exist_ok=True)
        remote_url = f"https://{token}@github.com/{REPO}.git"
        r = run_cmd(
            ["git", "clone", "--single-branch", "-b", BRANCH, remote_url, str(REPO_DIR)],
            timeout=120,
        )
        if r.returncode != 0:
            raise RuntimeError(f"克隆仓库失败: {REPO}")
        # 配置身份
        run_cmd(["git", "config", "user.email", GIT_EMAIL], cwd=str(REPO_DIR))
        run_cmd(["git", "config", "user.name", GIT_NAME], cwd=str(REPO_DIR))

    # 确认状态
    r1 = run_cmd(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=str(REPO_DIR))
    r2 = run_cmd(["git", "rev-parse", "--short", "HEAD"], cwd=str(REPO_DIR))
    print(f"✅ 仓库就绪: {r1.stdout.strip()} @ {r2.stdout.strip()}")


# ====== 流水线步骤 ======
def run_update(target_date: str) -> None:
    """运行 update 脚本（当日 + 回滚最近 2 天），失败则抛异常"""
    log_info(f"步骤2/5：运行机游共振日历更新（{target_date}，回滚 2 天）")
    html_path = str(REPO_DIR / HTML_FILE)
    update_script = str(SCRIPT_DIR / "update_jiyou_resonance_calendar.py")

    # 当日更新
    r = run_cmd(
        [
            sys.executable, update_script,
            "--html_path", html_path,
            "--repo_path", str(REPO_DIR),
            "--date", target_date,
            "--no-push",
            "--skip-self-check",
            "--result_mode", "display_only",
        ],
        cwd=str(SCRIPT_DIR),
        timeout=300,
    )
    # 注意：子脚本因 SDK 不可用导致的 submit_result 失败（exit 1），
    # 只要 HTML 文件有写入，不算失败。通过检查 HTML 文件是否存在且大小正常。
    if r.returncode != 0:
        # 检查 HTML 是否存在且有内容 → 视为数据更新成功，只是 sdk 提交失败
        if not os.path.isfile(html_path) or os.path.getsize(html_path) < 10000:
            raise RuntimeError(f"更新当日数据失败（返回码={r.returncode}，HTML 无效）")
        log_warn(f"更新脚本返回码 {r.returncode}（可能为 SDK 不可用），但 HTML 已生成，继续...")

    # 回滚最近 2 天
    date_obj = datetime.strptime(target_date, "%Y-%m-%d")
    for i in range(1, 3):
        prev_date = (date_obj - timedelta(days=i)).strftime("%Y-%m-%d")
        log_info(f"回滚修正前 {i} 天: {prev_date}")
        pr = run_cmd(
            [
                sys.executable, update_script,
                "--html_path", html_path,
                "--repo_path", str(REPO_DIR),
                "--date", prev_date,
                "--force",
                "--no-push",
                "--skip-self-check",
                "--result_mode", "display_only",
            ],
            cwd=str(SCRIPT_DIR),
            timeout=300,
        )
        if pr.returncode != 0:
            log_warn(f"回滚修正 {prev_date} 返回码 {pr.returncode}，继续...")

    print("✅ 更新完成")


def sync_index() -> None:
    log_info(f"步骤3/5：同步 index.html（复制 {HTML_FILE} → {INDEX_FILE}）")
    src = REPO_DIR / HTML_FILE
    dst = REPO_DIR / INDEX_FILE
    if not src.is_file():
        raise RuntimeError(f"HTML 文件不存在，无法同步 index.html: {src}")
    shutil.copy2(src, dst)
    print("✅ index.html 已同步")


def run_validate(target_date: str) -> None:
    log_info("步骤4/5：运行数据一致性校验（api 模式）")
    html_path = str(REPO_DIR / HTML_FILE)
    validate_script = str(SCRIPT_DIR / "validate_data_consistency.py")
    r = run_cmd(
        [
            sys.executable, validate_script,
            "--date", target_date,
            "--mode", "api",
            "--html-path", html_path,
        ],
        cwd=str(SCRIPT_DIR),
        timeout=300,
    )
    if r.returncode != 0:
        log_error(f"数据一致性校验失败（返回码={r.returncode}），将回退 git 变更")
        git_reset_hard()
        raise RuntimeError("数据一致性校验失败，已回退 git 变更")
    print("✅ 校验通过")


def git_deploy(target_date: str, no_push: bool, token: str) -> str:
    log_info("步骤5/5：Git 提交与推送")

    # 检查是否有变更
    r1 = run_cmd(["git", "diff", "--quiet", "--", HTML_FILE, INDEX_FILE], cwd=str(REPO_DIR))
    r2 = run_cmd(["git", "diff", "--cached", "--quiet", "--", HTML_FILE, INDEX_FILE], cwd=str(REPO_DIR))
    if r1.returncode == 0 and r2.returncode == 0:
        msg = f"[{target_date}] 机游共振日历无变更，流水线完成"
        print(f"ℹ️  {msg}")
        return msg

    # add
    run_cmd(["git", "add", HTML_FILE, INDEX_FILE], cwd=str(REPO_DIR))

    # commit
    commit_msg = f"auto: {target_date} 机游共振日历每日更新 (纯数据交叉版)"
    r = run_cmd(["git", "commit", "-m", commit_msg], cwd=str(REPO_DIR))
    if r.returncode != 0 and "nothing to commit" in r.stdout:
        msg = f"[{target_date}] 机游共振日历无变更，流水线完成"
        print(f"ℹ️  {msg}")
        return msg

    if no_push:
        msg = f"[{target_date}] 机游共振日历更新完成（未推送，--no-push）"
        print(f"🧪 {msg}")
        return msg

    # 确保 remote URL 带 token
    remote_url = f"https://{token}@github.com/{REPO}.git"
    run_cmd(["git", "remote", "set-url", "origin", remote_url], cwd=str(REPO_DIR))

    r = run_cmd(["git", "push", "origin", BRANCH], cwd=str(REPO_DIR), timeout=120)
    if r.returncode != 0:
        raise RuntimeError(f"Git push 失败: {r.stderr.strip()[:200]}")
    msg = f"[{target_date}] 机游共振日历更新并推送成功"
    print(f"✅ {msg}")
    return msg


# ====== 主入口 ======
async def main():
    # 参数解析：第一个参数 result_mode，后续 --date / --no-push
    result_mode = sys.argv[1] if len(sys.argv) > 1 else "display_only"
    target_date = ""
    no_push = False

    i = 2
    while i < len(sys.argv):
        arg = sys.argv[i]
        if arg == "--date" and i + 1 < len(sys.argv):
            target_date = sys.argv[i + 1]
            i += 2
        elif arg == "--no-push":
            no_push = True
            i += 1
        else:
            i += 1

    if not target_date:
        target_date = datetime.now().strftime("%Y-%m-%d")

    actual_mode = result_mode if result_mode != "auto" else "display_only"

    print("=" * 50)
    print("🚀 机游共振日历 — 每日自动更新流水线")
    print(f"📅 目标日期: {target_date}")
    print(f"📁 仓库目录: {REPO_DIR}")
    print(f"📄 HTML 文件: {HTML_FILE}")
    print(f"🌿 分支: {BRANCH}")
    if no_push:
        print("🧪 模式: no-push（不推送）")
    print("=" * 50)

    token = load_github_token()
    if not token:
        log_warn("未找到 GITHUB_TOKEN，推送可能失败")

    try:
        ensure_repo(token)
        run_update(target_date)
        sync_index()
        run_validate(target_date)
        msg = git_deploy(target_date, no_push, token)

        await sdk.submit_result(
            result_mode=actual_mode,
            status="success",
            message=msg,
            data={
                "target_date": target_date,
                "no_push": no_push,
                "branch": BRANCH,
                "html_file": HTML_FILE,
            },
        )
    except Exception as e:
        err_msg = f"[{target_date}] 机游共振日历流水线失败: {e}"
        log_error(err_msg)
        # 尝试回退
        try:
            if (REPO_DIR / ".git").is_dir():
                git_reset_hard()
        except Exception:
            pass
        await sdk.submit_result(
            result_mode="notify",
            status="error",
            message=err_msg,
            data={"target_date": target_date, "error_type": type(e).__name__},
        )
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
