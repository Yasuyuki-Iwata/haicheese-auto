# はいチーズ！ノート 自動送信ツール

保育園の連絡帳アプリ「はいチーズ！ノート」に連絡帳を自動送信するツール。ブラウザの設定画面から、有効・無効、送信期間、体温の範囲、検温時刻、プールの参加を確認・変更できる。

## 構成

```
haicheese/
├── core.py        # 設定の検証・状態判定・次回予定。UIと実行で共通
├── sender.py       # Playwrightでの実際の送信操作
├── notifier.py     # Mac通知・Discord通知
├── runner.py       # 毎分の送信判定（launchdから起動）
├── monitor.py      # 未送信・異常の監視（launchdから起動）
├── app.py          # Flaskの設定画面
├── templates/index.html
├── submit.py       # 旧スクリプト。直接実行では送信しない
├── healthcheck.sh  # 旧ヘルスチェック。monitor.pyに置き換え済み、未使用
├── launchd/        # 常駐用plistと導入スクリプト
├── skip_dates.txt  # 手動で送信をスキップする日付（1行1日付）
├── data/           # SQLite（Git管理外）
└── logs/           # 実行ログ（Git管理外）
```

設定と実行記録は `data/app.sqlite3` に保存する。土日祝日は自動でスキップし、`skip_dates.txt` に書いた日付もスキップする（この2つは今回も画面からは編集できない）。

## セットアップ

### 1. 仮想環境と依存パッケージ

```bash
cd /Users/yasuyuki/Developer/haicheese
/opt/homebrew/bin/python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Playwrightのブラウザは `~/Library/Caches/ms-playwright` の既存キャッシュを使う。入っていなければ `python -m playwright install chromium` を実行する。

### 2. 認証情報

```bash
cp .env.example .env
```

`.env` に以下を設定する。

| 変数 | 内容 |
|---|---|
| `HAICHEESE_EMAIL` / `HAICHEESE_PASSWORD` | はいチーズ！ノートのログイン情報 |
| `DISCORD_BOT_TOKEN` / `DISCORD_DM_CHANNEL` | 送信結果の通知先。`DISCORD_BOT_TOKEN` はこのツール自身は読まず、共通配信部 `discord_delivery`（`~/.local/lib`）が送信直前に`.env`から読む |
| `HAICHEESE_WEB_HOST` / `HAICHEESE_WEB_PORT` | 設定画面の待ち受け（既定 `127.0.0.1:5002`） |
| `HAICHEESE_WEB_TOKEN` | 設定画面のアクセストークン。未設定だと起動しない |

### 3. 動作確認（本番送信なし）

設定画面だけを手元で確認する場合は、一時的なDBを指すよう `HAICHEESE_DATA_DIR` を指定して起動する。

```bash
HAICHEESE_DATA_DIR=/tmp/haicheese-check HAICHEESE_WEB_TOKEN=devtoken \
  .venv/bin/python3 app.py
```

`http://127.0.0.1:5002/?token=devtoken` を開くと、初期状態（停止中・体温36.5〜36.8℃・検温06:50・プール参加）が確認できる。確認が終わったら `Ctrl-C` で止める。

### 4. 常駐登録

launchdへの登録は本人が確認してから手動で行う。

```bash
bash launchd/install.sh
```

`com.haicheese.web`（設定画面・KeepAlive）、`com.haicheese.runner`（毎分の送信判定）、`com.haicheese.monitor`（5分おきの監視）を登録する。停止する場合は次のコマンドを使う。

```bash
launchctl unload ~/Library/LaunchAgents/com.haicheese.web.plist
launchctl unload ~/Library/LaunchAgents/com.haicheese.runner.plist
launchctl unload ~/Library/LaunchAgents/com.haicheese.monitor.plist
```

旧cronの送信・ヘルスチェックの行は現状コメントアウト済みで、`launchd/install.sh` からは触らない。不要なら `crontab -e` で手動で消す。

## 使い方

設定画面で以下を確認・変更できる。

- 自動送信の有効・無効、開始日・終了日（未設定なら制限なし）
- 体温の範囲（35.0〜42.0℃、0.1℃刻み。範囲内でランダムな値を毎回生成）
- 検温時刻（分は5分刻み）、送信時刻（1分単位）
- プールの参加・不参加

保存前に画面上部へ「保存すると稼働中になり、次回は◯月◯日に送信予定です」のような要約が出る。保存すると、その内容がサーバー側の確定設定として画面に反映される。「自動送信を停止」は他の未保存の変更を巻き込まず即座に効く。

送信は次の条件がすべて揃ったときだけ行う。

- 有効かつ、送信期間内・平日・祝日でない・`skip_dates.txt` にない日
- 現在時刻が送信予定時刻から3分未満
- 設定の更新日時が送信予定時刻より前（過去の時刻に変更しても即時送信しない）
- その日にまだ実行記録がない（同じ日に何度起動しても再送信しない）

送信結果は「送信済み」「失敗」「結果不明」「スキップ（送信先で確認済み）」「停止による中止」のいずれかで記録し、通信の失敗を成功として扱わない。予定時刻から20分過ぎても結果が確認できない場合は、Mac通知とDiscordで警告する。

## 開発メモ

`core.py` の状態判定・次回予定・送信判定は、時計・祝日判定・skip日付の読み込みをすべて引数で差し替えられる純粋関数にしてある。本番サイトへ接続せず、送信・通知をダミーに差し替えてテストできる。
