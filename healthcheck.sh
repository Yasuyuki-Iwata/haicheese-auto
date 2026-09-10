#!/bin/bash
# はいチーズ！自動送信のヘルスチェック。
#
# 2026-09-08: crontabが /Users/yasuyuki/scripts/haicheese を指したままディレクトリ
#   ごと消えており、リダイレクト先も存在しないためシェルレベルでコマンドが起動
#   できず、submit.py内のDiscord失敗通知すら一度も実行されないまま5日間無音で
#   気づけなかった。submit.py自身の通知はプロセスが起動して初めて意味を持つため、
#   cronジョブとは独立に「今日ログに新しい行が増えたか」だけを見て、無ければ
#   ここから直接Discordに警告する。
#
# crontab: 20 7 * * 1-5 (submit.py の 0 7 の20分後、平日のみ)
set -uo pipefail

REPO="/Users/yasuyuki/Developer/haicheese"
LOG_FILE="$REPO/logs/$(date +%Y-%m).log"
ENV_FILE="$REPO/.env"
TODAY=$(date +%Y-%m-%d)

# 土日はcron自体が動かないので確認不要
DOW=$(date +%u)  # 1=月 ... 7=日
if [ "$DOW" -ge 6 ]; then
  exit 0
fi

if [ -f "$LOG_FILE" ] && grep -q "^$TODAY " "$LOG_FILE"; then
  exit 0
fi

# ここに来た = 今日分のログ行が見当たらない(cronが起動していない/パスが壊れている等)
MSG="⚠️ はいチーズ！連絡帳の自動送信ログが今日(${TODAY})分見当たりません。crontab・パスの断線やcron自体の停止を確認してください。"

if [ -f "$ENV_FILE" ]; then
  DISCORD_BOT_TOKEN=$(grep '^DISCORD_BOT_TOKEN=' "$ENV_FILE" | cut -d= -f2-)
  DISCORD_DM_CHANNEL=$(grep '^DISCORD_DM_CHANNEL=' "$ENV_FILE" | cut -d= -f2-)
fi

if [ -n "${DISCORD_BOT_TOKEN:-}" ] && [ -n "${DISCORD_DM_CHANNEL:-}" ]; then
  JSON_PAYLOAD=$(python3 - "$MSG" <<'EOF'
import json, sys
print(json.dumps({"content": sys.argv[1]}))
EOF
)
  curl -sS -m 10 -X POST "https://discord.com/api/v10/channels/${DISCORD_DM_CHANNEL}/messages" \
    -H "Authorization: Bot ${DISCORD_BOT_TOKEN}" \
    -H "Content-Type: application/json" \
    -d "$JSON_PAYLOAD" \
    >/dev/null 2>&1 || true
fi

osascript -e "display notification \"${MSG}\" with title \"はいチーズ！ヘルスチェック\"" 2>/dev/null || true

echo "$(date '+%Y-%m-%d %H:%M:%S')  今日分のログなし → Discord通知済み" >> "$REPO/logs/healthcheck.log"
