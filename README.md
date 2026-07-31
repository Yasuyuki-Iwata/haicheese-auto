# はいチーズ！ノート 自動送信ツール

保育園の連絡帳アプリ「はいチーズ！ノート」に毎朝自動で連絡帳を送信するスクリプト。

## 動作概要

- 毎朝 **7:00** に cron で自動起動（平日のみ・土日祝日はスキップ）
- Playwright でブラウザを裏側で操作し、以下を自動入力して送信
  - 体温: 36.0〜36.9℃ のランダム値
  - 検温時間: 6:50 固定
  - プール参加: 参加 固定
- 送信完了後、Mac 通知 ＋ Discord DM で結果を通知

## セットアップ

### 1. 依存ライブラリのインストール

```bash
pip3 install playwright python-dotenv jpholiday
playwright install chromium
```

### 2. 認証情報の設定

```bash
cp .env.example .env
# .env を編集してメールアドレスとパスワードを入力
```

### 3. cron 登録

```bash
crontab -e
```

以下を追加：

```
# [はいチーズ！ノート] プール参加・毎朝自動入力
0 7 * * 1-5 /path/to/python3 /Users/yourname/scripts/haicheese/submit.py >> /Users/yourname/scripts/haicheese/logs/cron.log 2>&1
```

## ファイル構成

```
haicheese/
├── submit.py        # メインスクリプト
├── .env             # 認証情報（Git管理外）
├── .env.example     # 設定例
├── .gitignore
└── logs/            # 実行ログ（Git管理外）
```

## ログ確認

```bash
# 最新の実行ログ
tail -20 ~/scripts/haicheese/logs/cron.log
```
