#!/bin/bash
# ============================================================
# 机游共振日历 — 每日自动更新流水线（Shell 入口）
# 内部调用 daily_update_jiyou.py 完成实际流水线，
# 适配中文文件名「机游共振日历.html」，可作为每日定时任务独立运行
#
# 用法:
#   ./daily_update_jiyou.sh <result_mode> [--date YYYY-MM-DD] [--no-push]
#
# 环境变量:
#   GITHUB_TOKEN   GitHub 访问令牌（优先环境变量，其次从 SECRET.md 读取）
# ============================================================

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON="${PYTHON:-python3}"

exec "$PYTHON" "$SCRIPT_DIR/daily_update_jiyou.py" "$@"
