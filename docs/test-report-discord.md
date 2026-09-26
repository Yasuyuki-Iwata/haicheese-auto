# テスト結果 (2026-09-27) — Discord通知の共通配信部移行

対象: `docs/design-discord-delivery.md`。実装者のテスト
（`tests/test_discord_delivery_adapter.py`・`tests/test_monitor.py`・`tests/test_runner.py`・
`tests/test_notifier.py`）とは別に、`tests/test_t10_independent.py`
（pytest、15件）を独立した視点で書いた。ダミーのnotify_fnではなく、本物の共通配信部
`discord_delivery`（HTTPだけ偽物に差し替え）を`notifier.notify`経由で実際に動かし、
`monitor.check_once`・`runner.run_once`から呼び出す構成にした。

## 実行結果

- 新規: `tests/test_t10_independent.py` 15件、全て成功
- リポジトリ全体: `.venv/bin/python3 -m pytest -q tests` → **147件成功**（実装者分132件＋新規15件）
- 本番の送信箱（`~/Library/Application Support/discord-delivery`）・本番DB
  （`/Users/yasuyuki/Developer/haicheese/data/app.sqlite3`）は`conftest.py`の
  `fail_if_production_stores_touched`により前後で変化なしを確認済み（テスト実行後も同fixtureが失敗しなかった）
- 妥当性の確認: `test_network_outage_queues_then_drain_delivers_matching_text`の期待値を
  意図的に壊して実行し、実際に失敗することを確認した上で元に戻した（テストが名目だけで
  常に通る作りになっていないことの検証）

## 成功（受け入れ条件ごと）

1. **通信断→queued→drainでsent、本文一致**
   `test_network_outage_queues_then_drain_delivers_matching_text`
   本物の共通配信部にHTTP失敗を注入 → `alerts`に記録され`queued`。
   `_now`を進めてdrain → `sent`、送信された本文が監視の警告文と完全一致することを確認。

2. **共通部の読み込み失敗→retry、直してから記録・1回だけ届く**
   `test_delivery_module_load_failure_retries_then_delivers_once`
   `HAICHEESE_DELIVERY_LIB`を空ディレクトリに向けて1回目は`error`→未記録・`retry`に入る
   → 直してから2回目は記録され、Discordへの登録は1回だけ（HTTP呼び出し1回・outbox行1件）。

   `test_registered_but_record_failure_does_not_double_deliver`
   `core.record_alert_once`を1回だけ例外にして「登録は成功したが記録だけ失敗」を再現。
   1回目は`check_once`自体が例外で終わる（`alerts`未記録・Discordには既に1通届く）→
   2回目は同じevent_keyのため`suppressed`となり、HTTPは追加で呼ばれず二重配信しないことを確認。

3. **400/403でdead→記録・log.error・以後通知しない**
   `test_discord_4xx_marks_dead_logs_error_and_stops_retrying`（400・403をパラメタライズ）
   `dead`・理由（`http_400`/`http_403`）が記録され、`log.error`に理由が出て、
   2回目の`check_once`では`has_alert`で弾かれてHTTP呼び出しが増えないことを確認。

4. **同日の複数種類は別event_key、翌日の同種は再送**
   `test_tick_stale_and_missing_use_distinct_event_keys_and_repeat_next_day`
   同日に`tick_stale`と`missing`が別々の`event_key`で計2件届き、翌日も同じ種類が
   別の`event_key`（日付違い）で計4件になることを確認。

5. **runner.run_once: notify_statusのマッピング・runs.resultは通知結果で変わらない**
   - `test_run_once_success_and_discord_sent_sets_notify_status_ok` → `ok`
   - `test_run_once_failed_and_discord_queued_sets_notify_status_queued` → `queued`（runs.resultは`failed`のまま）
   - `test_run_once_unknown_and_discord_dead_sets_notify_status_partial` → `partial`
   - `test_run_once_mac_and_discord_both_fail_sets_notify_status_failed` → `failed`
   - `test_run_once_already_sent_does_not_register_discord` / `test_run_once_cancelled_does_not_register_discord`
     → `already_sent`・`cancelled`ではDiscordへ登録されず送信箱の行数が増えないことを確認。

6. **トークンの非露出・DISCORD_BOT_TOKENを読まない**
   `test_fake_token_never_appears_in_outbox_return_values_or_logs`
   一意な偽トークンを使い、Authorizationヘッダには入る（仕様どおり）が、
   送信箱の全テーブル（events・parts・attachments）・添付ディレクトリ・
   `notify`/`run_once`の戻り値・ログ本文のどこにも出ないことを確認。

   `test_runner_and_monitor_main_do_not_read_discord_bot_token_env`
   `runner.py`・`monitor.py`のソースを静的に読み、`DISCORD_BOT_TOKEN`という文字列が
   存在しないこと、`DISCORD_DM_CHANNEL`は今どおり読んでいることを確認（ソース確認のみ）。

7. **停止中（enabled=0）ではrunner・monitorとも送信箱に何も登録しない**
   `test_disabled_settings_register_nothing_in_outbox_for_runner_and_monitor`
   `enabled=0`かつtickが古い・当日が対象日でも、`notify_fn`自体が呼ばれず、
   送信箱の行数が変化しないことを確認。

## 失敗

なし。設計（`docs/design-discord-delivery.md`）どおりの挙動を確認でき、実装コードの
不具合は見つからなかった。

## 見つけた不具合（重要度: 低・設計との整合性メモ）

コードのバグではないが、レビューで気づいた点を記録する。

- `monitor.check_once`内の`handle()`は`core.record_alert_once`の呼び出しを
  try/exceptで囲んでいない。そのため「Discordへの登録は成功したが記録だけ失敗する」状況
  （例: DBがロック中・ディスク満杯）では、その回の`check_once`自体が例外を送出して
  終了する（`monitor.main`は呼び出し元でこの例外を捕捉していないため、launchdの
  その回の起動はエラー終了する）。`docs/design-discord-delivery.md`の項目4は
  「"error"（共通部を読み込めない・登録の例外）なら記録しない」と書いており、
  "登録の例外"という表現から、この経路も想定内として扱われているように読める。
  `test_registered_but_record_failure_does_not_double_deliver`で確認した通り、
  次回の`check_once`ではevent_keyによる`suppressed`のおかげで二重配信は起きないため、
  実害は「その回のmonitor起動がエラー終了する」点だけで、5分後の次回起動で自然に
  回復する。ただし、この経路（`check_once`が例外を送出しうること）は設計文書に
  明記されておらず、`log.error`も出ないままクラッシュする。意図した仕様なのか、
  `record_alert_once`もtry/exceptで囲んで`retry`に入れるつもりだったのか、
  設計文書と実装のどちらが正か判断がつかないため、ここに記録するだけに留める。

## カバーできなかった受け入れ条件

- 「例外文」にトークンが出ないことは、実際に例外が送出される具体的なシナリオを
  作れなかったため、ソースコードを読んで確認した（`_resolve_credentials`・
  `_attempt`内のreason文字列はいずれも固定の英語ラベルで、トークンの値を
  含む経路が無いことを確認）。実行時アサーションでは検証できていない。
- Discordのグローバルレート制限（429・`rate_limited_global`）まわりの挙動は
  今回のタスク範囲外のため検証していない（design-discord-delivery.mdにも
  記載がない）。
- `healthcheck.sh`は設計文書の方針どおり「接続しない」ため対象外とした
  （変更なしなので検証不要と判断）。
