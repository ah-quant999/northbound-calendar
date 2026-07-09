#!/bin/bash
# 一键部署到GitHub Pages
# 用法: bash deploy.sh ["可选的提交说明"]

cd "$(dirname "$0")"

# 检测改动
CHANGED=$(git status --short)
if [ -z "$CHANGED" ]; then
    echo "没有文件改动，无需部署"
    exit 0
fi

# 生成提交说明
if [ -n "$1" ]; then
    MSG="$1"
else
    FILES=$(git status --short | awk '{print $NF}')
    MSG=""
    for f in $FILES; do
        if [[ "$f" == *".html" ]]; then
            NAME=$(basename "$f" .html)
            MSG="$MSG更新$NAME、"
        fi
    done
    MSG=$(echo "$MSG" | sed 's/、$/页/')
    [ -z "$MSG" ] && MSG="更新部署 $(date '+%m-%d %H:%M')"
fi

# 提交并推送
git add -A
git commit -m "$MSG"
git push origin main

echo ""
echo "✅ 部署完成！commit: $(git log -1 --oneline)"
echo "⏳ GitHub Pages 约1-2分钟生效"
