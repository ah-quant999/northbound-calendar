#!/bin/bash
# 核心资产保护脚本
# 用法:
#   bash protect_critical_files.sh        # 加锁（只读）
#   bash protect_critical_files.sh unlock # 解锁（可写）

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# 核心文件列表（相对路径）
FILES=(
  # 核心页面
  "北向资金日历.html"
  "index.html"
  "jiyou-resonance.html"
  "northbound-analysis.html"
  "jiyou-signal-analysis.html"
  # 重要页面
  "重要日历.html"
  "northbound-backtest.html"
  "northbound-industry-backtest.html"
  "resonance-backtest.html"
  "portal.html"
  "signal-guide.html"
  "daily-insight.html"
  "CRITICAL_ASSETS.md"
  "pre_deploy_smoke_test.py"
  # scripts 核心脚本
  "scripts/update_northbound_calendar.py"
  "scripts/update_jiyou_resonance_calendar.py"
  "scripts/update_important_calendar.py"
  "scripts/full_deploy.sh"
  "scripts/daily_update_northbound_gha.py"
  "scripts/daily_update_jiyou_gha.py"
  "scripts/update_important_gha.py"
  "scripts/t1_morning_fallback.py"
  "scripts/northbound_analysis.py"
  "scripts/jiyou_signal_analysis.py"
  "scripts/generate_northbound_backtest_page.py"
  "scripts/generate_northbound_industry_backtest_page.py"
  "scripts/daily_insight.py"
  "scripts/daily_recheck.py"
  "scripts/calendar_git.py"
  "scripts/stock_industry.py"
  "scripts/northbound_lhb_tracker.py"
  "scripts/validate_calendar_html.py"
  "scripts/validate_data_consistency.py"
  "scripts/validate_date_alignment.py"
  "scripts/validate_format_uniformity.py"
  # GHA工作流
  ".github/workflows/daily_update.yml"
  ".github/workflows/northbound_daily_update.yml"
  ".github/workflows/important_monthly_update.yml"
  ".github/workflows/t1_morning_fallback.yml"
  ".github/workflows/weekly_industry_refresh.yml"
)

MODE="${1:-lock}"
COUNT=0
SKIPPED=0

for f in "${FILES[@]}"; do
  if [ -f "$f" ]; then
    if [ "$MODE" = "unlock" ]; then
      chmod u+w "$f"
    else
      chmod a-w "$f"
    fi
    COUNT=$((COUNT + 1))
  else
    SKIPPED=$((SKIPPED + 1))
    echo "  [跳过] $f (不存在)"
  fi
done

if [ "$MODE" = "unlock" ]; then
  echo "✅ 已解锁 $COUNT 个核心文件（可写）"
else
  echo "🔒 已保护 $COUNT 个核心文件（只读）"
fi

if [ "$SKIPPED" -gt 0 ]; then
  echo "⚠️  跳过 $SKIPPED 个不存在的文件"
fi
