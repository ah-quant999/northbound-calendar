#!/bin/bash
# ============================================================
# 机游共振日历 — 一键部署流水线
# 流程：抓取更新 → 格式统一 → 多层校验 → git 部署
#
# 用法:
#   ./full_deploy.sh [--date YYYY-MM-DD] [--force] [--dry-run] [--html-path PATH]
#
# 环境变量:
#   GITHUB_TOKEN   GitHub 访问令牌（默认从 calendar_git.py 读取）
# ============================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
HTML_FILE="机游共振日历.html"
HTML_PATH="$REPO_DIR/$HTML_FILE"
PYTHON="${PYTHON:-python3}"

TARGET_DATE=""
FORCE_UPDATE=""
DRY_RUN=""
COMMIT_DATE=""

# 解析参数
while [[ $# -gt 0 ]]; do
    case "$1" in
        --date)
            TARGET_DATE="$2"
            shift 2
            ;;
        --force)
            FORCE_UPDATE="--force"
            shift
            ;;
        --dry-run)
            DRY_RUN="1"
            shift
            ;;
        --html-path)
            HTML_PATH="$2"
            shift 2
            ;;
        --html-file)
            HTML_FILE="$2"
            HTML_PATH="$REPO_DIR/$HTML_FILE"
            shift 2
            ;;
        *)
            echo "❌ 未知参数: $1"
            echo "用法: $0 [--date YYYY-MM-DD] [--force] [--dry-run] [--html-path PATH]"
            exit 2
            ;;
    esac
done

COMMIT_DATE="${TARGET_DATE:-$(date +%Y-%m-%d)}"

echo "=============================================="
echo "🚀 机游共振日历 — 一键部署流水线"
echo "📅 目标日期: $COMMIT_DATE"
echo "📄 HTML路径: $HTML_PATH"
echo "🏭 仓库目录: $REPO_DIR"
[[ -n "$FORCE_UPDATE" ]] && echo "🔧 强制更新: 是"
[[ -n "$DRY_RUN" ]] && echo "🧪 Dry-run: 是（不推送）"
echo "=============================================="

# 保存 git 状态，失败时回退
git_reset() {
    echo ""
    echo "🔄 回退 git 变更..."
    cd "$REPO_DIR"
    git checkout -- . 2>/dev/null || true
    git clean -fd 2>/dev/null || true
    echo "✅ 已回退到干净状态"
}

# 错误处理
on_error() {
    local exit_code=$?
    local step="$1"
    echo ""
    echo "❌❌❌ 部署失败 ❌❌❌"
    echo "❌ 失败步骤: $step"
    echo "❌ 退出码: $exit_code"
    git_reset
    exit $exit_code
}

trap 'on_error "未知步骤"' ERR

# ====== 步骤0：确保在正确分支 ======
echo ""
echo "📌 [步骤0/6] 检查 git 分支..."
cd "$REPO_DIR"

# 确保在 calendar-pages 分支
CURRENT_BRANCH="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo '')"
if [[ "$CURRENT_BRANCH" != "calendar-pages" ]]; then
    echo "⚠️  当前分支: $CURRENT_BRANCH，切换到 calendar-pages..."
    git fetch origin calendar-pages --depth 5 2>/dev/null || true
    if git show-ref --verify --quiet "refs/heads/calendar-pages"; then
        git checkout calendar-pages
    else
        git checkout -b calendar-pages origin/calendar-pages
    fi
fi
echo "✅ 当前分支: $(git rev-parse --abbrev-ref HEAD)"

# ====== 步骤1：抓取更新 ======
echo ""
echo "🔍 [步骤1/6] 抓取更新机游共振日历数据..."

UPDATE_ARGS=(
    --html_path "$HTML_PATH"
    --repo_path "$REPO_DIR"
    --no-push
    --skip-self-check
    --result_mode display_only
)
[[ -n "$TARGET_DATE" ]] && UPDATE_ARGS+=(--date "$TARGET_DATE")
[[ -n "$FORCE_UPDATE" ]] && UPDATE_ARGS+=("$FORCE_UPDATE")
[[ -n "$DRY_RUN" ]] && UPDATE_ARGS+=(--dry-run)

set +e
$PYTHON "$SCRIPT_DIR/update_jiyou_resonance_calendar.py" "${UPDATE_ARGS[@]}"
UPDATE_EXIT=$?
set -e

if [[ $UPDATE_EXIT -ne 0 ]]; then
    # 如果只是 dry-run 退出不算失败
    if [[ -n "$DRY_RUN" ]]; then
        echo "🧪 Dry-run 模式，抓取完成，退出"
        exit 0
    fi
    on_error "步骤1-抓取更新"
fi
echo "✅ 抓取更新完成"

# ====== 步骤2：格式统一 ======
echo ""
echo "📐 [步骤2/6] 格式统一化处理..."
$PYTHON "$SCRIPT_DIR/normalize_calendar_format.py" "$HTML_PATH" --in-place
echo "✅ 格式统一完成"

# ====== 步骤3：HTML结构校验 ======
echo ""
echo "🏗️  [步骤3/6] HTML 结构校验..."
$PYTHON "$SCRIPT_DIR/validate_calendar_html.py" "$HTML_PATH"
echo "✅ HTML 结构校验通过"

# ====== 步骤4：日期-星期对齐校验 ======
echo ""
echo "📅 [步骤4/6] 日期-星期对齐校验..."
$PYTHON "$SCRIPT_DIR/validate_date_alignment.py" "$HTML_PATH"
echo "✅ 日期-星期对齐校验通过"

# ====== 步骤5：格式一致性校验 ======
echo ""
echo "🎨 [步骤5/6] 格式统一性校验..."
$PYTHON "$SCRIPT_DIR/validate_format_uniformity.py" "$HTML_PATH"
echo "✅ 格式统一性校验通过"

# ====== 步骤6：数据一致性校验 ======
echo ""
echo "🔎 [步骤6/6] 数据一致性校验..."
$PYTHON "$SCRIPT_DIR/validate_data_consistency.py" "$HTML_PATH"
echo "✅ 数据一致性校验通过"

# ====== Git 部署 ======
echo ""
echo "=============================================="

if [[ -n "$DRY_RUN" ]]; then
    echo "🧪 Dry-run 模式，全部校验通过，跳过 git 部署"
    echo "✅ 所有校验通过（dry-run）"
    exit 0
fi

echo "📤 开始 Git 部署..."
cd "$REPO_DIR"

# 检查是否有变更
if git diff --quiet -- "$HTML_FILE" 2>/dev/null && git diff --cached --quiet -- "$HTML_FILE" 2>/dev/null; then
    # 也检查 index.html
    if git diff --quiet -- index.html 2>/dev/null; then
        echo "ℹ️  无文件变更，跳过提交"
        echo "✅ 部署完成（无变更）"
        exit 0
    fi
fi

# 同时复制为 index.html
cp "$HTML_FILE" index.html
echo "📄 已复制为 index.html"

git add "$HTML_FILE" index.html scripts/

COMMIT_MSG="${COMMIT_DATE} 机游共振日历更新"
echo "📝 Commit message: $COMMIT_MSG"

git commit -m "$COMMIT_MSG" || {
    echo "ℹ️  无变更需要提交"
    echo "✅ 部署完成（无变更）"
    exit 0
}

git push origin calendar-pages

echo ""
echo "🎉🎉🎉 部署成功 🎉🎉🎉"
echo "📅 日期: $COMMIT_DATE"
echo "📄 文件: $HTML_FILE + index.html"
echo "🌿 分支: calendar-pages"
echo "=============================================="

exit 0
