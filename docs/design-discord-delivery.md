# Discord通知の共通配信部への移行（T10 連絡帳）

作成日: 2026-09-27。全体の仕様はvaultの `ops/automation/discord-delivery/docs/design.md`、段階の一覧は同じ場所の `implementation-guide.md`。この文書は haicheese 側の変更だけを書く。先に移行した近鉄（kintetsu-bot）・Kindle（kindle-pdf）の `docs/design-discord-delivery.md` と同じ作りにする。

## 目的

Bot トークンで DM チャンネルへ直接 POST するのをやめ、共通配信部 `discord_delivery`（`~/.local/lib/discord_delivery.py`）の送信箱へ預ける。送れなかった通知は15分巡回（kb-reconcile）の drain が送り直す。あわせて、監視（monitor.py）の「警告済み」の記録を、通知を預けたあとに書くよう並べ替える。今は記録してから送るので、Discord への送信が失敗するとその警告は二度と出ない。

連絡帳の送信・監視は2026-09-12から停止中（settings.enabled=0）。この段階では停止を解除しない。送信（sender.py）・予定判定（core.py の判定関数）・runs の日付単位の重複防止（claim_run）は変えない。

## 送信先

Bot `{kind: bot, channel_id: <DISCORD_DM_CHANNEL>, token_file: <repo>/.env, token_key: DISCORD_BOT_TOKEN}`。今と同じ DM チャンネルで、トークンは送る直前に共通配信部が `.env` から読む。アダプター・runner・monitor はトークンを読まない・渡さない。

## notifier.py

| 関数 | 変えたあと |
|---|---|
| `notify_mac(title, message)` | 変えない |
| `notify_discord_result(dm_channel, message, *, event_key=None, ttl_seconds=86400)` → dict | 新設。共通配信部の `submit` の結果（status・event_id・reason など）を返す。`dm_channel` か `message` が空なら送信箱へ登録せず `{"status": "not_configured"}`。共通部の読み込み失敗・`submit` の例外は `{"status": "error", "reason": ...}`（呼び出し元へ例外を投げない） |
| `notify_discord(bot_token, dm_channel, message)` → bool | 互換のため残す。`bot_token` は受け取るだけで使わない。預けられた（sent・queued・suppressed）なら True、それ以外は False |
| `notify(mac_title, mac_message, discord_message, *, bot_token="", dm_channel, mac=True, discord=True, event_key=None)` → dict | 下記 |

`notify` の戻り値:
- `discord_status` を必ず入れる。Discord を求めていない（`discord=False`）ときは `"skipped"`、それ以外は `notify_discord_result` の status（sent・queued・suppressed・dead・error・not_configured）。
- `status` は runs.notify_status に保存する要約。Mac（求めたとき）が成功し、Discord が sent・suppressed・skipped なら `"ok"`。Mac が成功し、Discord が queued なら `"queued"`（送信済みとは言わない）。求めたもののうち一部だけ失敗なら `"partial"`、全部失敗なら `"failed"`。Discord の失敗は dead・error・not_configured。
- `"partial"` のときは今どおり `mac_ok`・`discord_ok` も入れる。

重複の止め方と TTL:

| 通知 | 呼び出し元 | event_key | TTL |
|---|---|---|---|
| 送信結果（成功・失敗・結果不明） | runner.run_once | 使わない（runs の claim_run で日付ごとに1回しか呼ばれない。event_key は一度 dead になると同じキーを二度と受け付けない） | 24時間 |
| 監視の警告（tick_stale・missing・failed・unknown） | monitor.check_once | `haicheese:alert:<target_date>:<kind>`（下記の再試行で二重に登録しないため） | 24時間 |

## monitor.py の並べ替え

今: `record_alert_once`（INSERT）→ 成功したら通知。
変えたあと:
1. `core.has_alert(conn, target_date, kind)`（新設。alerts に同じ日・種類があるか読むだけ）が True なら何もしない。
2. 通知する。`notify_fn(..., event_key="haicheese:alert:<target_date>:<kind>")`。
3. 戻り値の `discord_status` が `"error"` 以外（sent・queued・suppressed・dead・not_configured・skipped、または `discord_status` が無い）なら `record_alert_once` で記録し、`fired` に入れる。dead・not_configured は再試行しても送れないので記録し、`log.error` で理由を残す。
4. `"error"`（共通部を読み込めない・登録の例外）なら記録しない。`log.error` を出し、戻り値の `retry` に入れる。次の5分後の監視でもう一度通知する（event_key があるので、登録済みなら Discord は suppressed になり二重に届かない）。この間は Mac 通知が5分ごとに出る。共通部が壊れているときだけ起きる。

`notify_fn` に `event_key` キーワードを足すので、tests のダミー通知関数も `**kwargs` を受けるように直す。

## runner.py

- `real_notify` は `event_key` を受け取れるようにし（使わないが署名をそろえる）、`bot_token` を渡さない。`os.getenv("DISCORD_BOT_TOKEN")` の読み込みを消す。`DISCORD_DM_CHANNEL` は今どおり読む。
- `set_notify_status` に保存する値は `notify` の `status`（ok・queued・partial・failed）。送信結果（runs.result）は変えない。
- runner のログに `discord_status` を1行出す（queued は「配信待ち」）。

monitor.main も同じく `DISCORD_BOT_TOKEN` を読まない。

## 読み込み

`_delivery_module()` は、環境変数 `HAICHEESE_DELIVERY_LIB` があればそこ、無ければ `~/.local/lib` から `discord_delivery` を import する（近鉄・Kindle と同じ作り）。失敗したら直送へは戻さない。

## healthcheck.sh は接続しない

実装手順書は「healthcheck の独自 curl も接続」としているが、手順書の作成（9月22日）より前の9月16日に monitor.py へ置き換わり、healthcheck.sh は launchd・crontab のどちらからも呼ばれていない（ファイル先頭に明記、2026-09-27 に launchd の一覧と crontab で確認）。動かないファイルを接続しても検証できないので、変えない。

## テストの安全

`tests/conftest.py` に autouse の fixture を足し、全テストで `DISCORD_DELIVERY_HOME`・`DISCORD_DELIVERY_USER_HOME` を一時ディレクトリへ、`NO_DISCORD=1` を設定する。送信を試す試験だけ `NO_DISCORD` を外し、共通配信部の HTTP（`_http_request` など）を偽物に差し替え、`token_file` は一時ディレクトリの偽 `.env` にする。本番の送信箱（`~/Library/Application Support/discord-delivery`）・本番の `data/app.sqlite3`・`.env` には書かない・読まない。実行は `.venv/bin/python3 -m pytest -q tests`。

## やらないこと

- 連絡帳の送信・監視の再開（settings.enabled の変更、launchd の変更）
- sender.py・Web画面（app.py・templates）の変更
- 直送へのフォールバック
- healthcheck.sh の変更
