#!/bin/bash
# はいチーズ！自動送信の常駐登録。
#
# このスクリプトは本人が動作確認したあとに手動で実行する。実装時には実行しない。
# crontabの編集はしないので、旧cron行のコメントアウト解除・削除は別途手で行うこと
# （現状は送信・ヘルスチェックともコメントアウト済みで停止中）。
set -e

REPO="/Users/yasuyuki/Developer/haicheese"
LAUNCH_DIR="$HOME/Library/LaunchAgents"
mkdir -p "$LAUNCH_DIR"

if [ ! -f "$REPO/.env" ]; then
  echo "ERROR: $REPO/.env がありません。先に .env.example をコピーして設定してください。"
  exit 1
fi

if ! grep -q '^HAICHEESE_WEB_TOKEN=' "$REPO/.env" 2>/dev/null; then
  echo "ERROR: .env に HAICHEESE_WEB_TOKEN が未設定です。ランダムな文字列を設定してください。"
  exit 1
fi

for plist in com.haicheese.web com.haicheese.runner com.haicheese.monitor; do
  launchctl unload "$LAUNCH_DIR/${plist}.plist" 2>/dev/null || true
  cp "$REPO/launchd/${plist}.plist" "$LAUNCH_DIR/"
  launchctl load "$LAUNCH_DIR/${plist}.plist"
  echo "登録しました: $plist"
done

echo ""
echo "常駐登録が完了しました。"
echo "旧cron行（送信・healthcheck.sh）は現状コメントアウト済みです。"
echo "'crontab -e' で内容を確認し、不要なら削除してください（このスクリプトは触りません）。"
