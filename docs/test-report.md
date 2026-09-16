# テスト結果 (2026-09-16)

対象: `/Users/yasuyuki/Developer/haicheese`（`docs/design.md` 4章「受け入れ条件」1〜13、9章「実装時の決定」）
実行: `.venv/bin/python -m pytest -q`（Python 3.12.14 / Flask 3.1.3 / Playwright 1.63.0 / pytest 9.1.1）
結果: **110 passed, 4 failed**（テストコード自体は未修正、実装側の問題を2件検出）

本番安全の確認: テスト全体を通じて `note.hoi-sys.com`・Discord API・`osascript` へは一度もアクセスしていない（`sender.login`/`notifier.notify_mac`/`notifier.notify_discord` はすべてモンキーパッチ、または呼び出し自体をダミー関数に差し替え）。`.env` は読み込ませず（`dotenv.load_dotenv` を no-op に差し替え済み）、DBは全テストで `tmp_path` 配下の一時ディレクトリを使用し、`data/app.sqlite3` はリポジトリ内に作成されていないことを確認済み（`find`で0件）。crontab・launchd・git pushは一切実行していない。

## 成功

条件ごとの対応（テストファイル: `tests/test_core.py` `tests/test_runner.py` `tests/test_monitor.py` `tests/test_sender.py` `tests/test_notifier.py` `tests/test_app.py` `tests/test_ui_responsive.py`）。

| # | 受け入れ条件 | 結果 | 主なテスト |
|---|---|---|---|
| 1 | 初期値（停止中・日付未設定・07:00・06:50・36.5〜36.8・参加） | 合格 | `test_core.py::test_initial_settings_defaults` |
| 2 | 永続化（再接続で残る）／保存失敗時に以前の設定を維持 | 合格 | `test_settings_persist_across_reconnect` / `test_save_settings_revision_conflict_keeps_previous` / `test_save_settings_failure_keeps_previous_settings` / `test_app.py::test_settings_save_failure_keeps_previous_settings_and_reports_error` |
| 3 | 運用状態4種＋初日・最終日を含む | 合格 | `test_core.py::test_operational_status_matrix`（8パターン） |
| 4 | 次回予定と「予定なし」の理由（stopped/ended/no_target） | 合格 | `test_next_run_when_stopped` / `test_next_run_when_ended` / `test_next_run_no_target_in_period` / `test_next_run_finds_next_weekday` / `test_next_run_skips_holiday_and_skip_dates` / `test_next_run_today_past_window_moves_to_tomorrow` / `test_next_run_today_already_run_moves_to_tomorrow` |
| 5 | 体温の生成・検証（範囲内・両端含む・上下限等しい・不正値/逆転/範囲外/0.1刻み拒否） | 合格 | `test_generate_temp_within_range_inclusive` / `test_generate_temp_min_equals_max` / `test_validate_settings_rejects_bad_temperature`（5パターン）/ `test_validate_settings_accepts_equal_min_max` / `test_validate_settings_rejects_reversed_dates` |
| 6 | 検温時刻・プールがsenderへ渡る値／プール欄検出（参加/不参加・true/false・欄なし→failed） | 合格 | `test_runner.py::test_run_once_passes_measurement_time_and_pool_to_submit_fn` / `test_sender.py::test_find_pool_select_*`（4種）/ `test_submit_fails_without_pressing_when_pool_select_missing` |
| 7 | 送信時刻変更の反映（07:00に送らず08:15に送る）／過去時刻変更・予定後有効化で即時送信しない／3分window | 合格 | `test_run_once_does_not_send_at_old_time_after_change_to_0815` / `test_run_once_sends_at_new_time_0815` / `test_run_once_does_not_send_immediately_when_changed_to_past_time` / `test_run_once_does_not_send_when_enabled_after_scheduled_time` / `test_run_once_3min_window_boundary` |
| 8 | 土日祝日・skip_dates.txtは送らない | 合格 | `test_run_once_skips_weekend` / `test_run_once_skips_holiday` / `test_run_once_skips_skip_dates_txt` |
| 9 | 二重起動・再起動・時刻変更後も成功済み/失敗/結果不明は再送しない／already_sentは正常スキップ | 合格 | `test_core.py::test_claim_run_prevents_duplicate` / `test_claim_run_concurrent_only_one_wins`（実スレッド競合）/ `test_runner.py::test_run_once_concurrent_double_start_only_one_sends`（実スレッド競合）/ `test_run_once_does_not_resend_same_day_after_success` / `test_run_once_does_not_resend_after_time_change_same_day` / `test_run_once_already_sent_is_recorded_as_normal_skip` / `test_run_once_does_not_resend_after_failed_or_unknown` |
| 10 | 通信失敗はsuccessにならない／送信ボタン後の例外はunknown／通知失敗で送信結果は不変／running15分超は結果不明 | 合格 | `test_sender.py::test_submit_login_exception_before_commit_is_failed`（failed）/ `test_submit_exception_after_commit_is_unknown`（unknown）/ `test_runner.py::test_run_once_notify_failure_does_not_change_submit_result` / `test_core.py::test_effective_result_running_over_15min_becomes_unknown` / `test_effective_result_running_exactly_15min_stays_running` |
| 11 | 監視: 停止中・開始前・対象外日に警告しない／予定+20分で警告／同日同種は1回／変更後の時刻に追従／停止の再確認（cancelled・押さない） | 一部合格・1件バグ検出 | `test_monitor.py::test_monitor_does_not_warn_when_stopped` / `_before_start_date` / `_on_non_target_day` / `test_monitor_warns_after_20min_of_no_run` / `test_monitor_same_alert_fires_only_once` / `test_monitor_follows_time_change` / `test_monitor_warns_failed_result` / `test_monitor_does_not_warn_on_success` / `test_monitor_tick_stale_warns_when_enabled`／`test_sender.py::test_submit_cancelled_before_confirm` / `test_submit_cancelled_before_final_send`（下の「失敗」に1件別途あり） |
| 12 | 320/390/1024pxで横スクロールなし・保存/停止ボタンが見える・未保存表示 | **一部失敗（3件）** | `test_ui_responsive.py`（下記「失敗」参照。未保存表示自体は合格） |
| 13 | 時計・送信先・通知先の差し替えで本番送信・Discord送信なしに検証 | 合格（本レポート全体の前提として達成） | 全テストで `now_fn`/`submit_fn`/`notify_fn`/`is_holiday`/`load_skip_dates` を差し替え |

Web（認証・CSRF等、9章の実装詳細）も合わせて確認: `tests/test_app.py`
- 認証なし401 / トークン→Cookie（HttpOnly・SameSite=Strict）/ 誤トークン401
- CSRFヘッダーなし403 / Origin不一致403 / 非JSON415
- `revision` 不一致409、成功時の設定反映
- `POST /api/pause` が `enabled` 以外を変更しないこと
- `/api/status` のレスポンスにメール・パスワード・Discordトークン・Webトークンが含まれないこと
- DB読取・書込失敗時にエラーを返し、書込失敗時は設定が変わらないこと
- 画面HTMLに `skip_dates` を編集するUIが無いこと（8章の「編集機能を追加しない」の確認）

その他、要求外だが安全確認のため追加: `tests/test_notifier.py`（`notify()`の集計ロジックのみ。実際の`osascript`/Discord APIは呼び出していない）。

## 失敗

### 1. `.footer-bar` の負のマージンにより320/390/1024pxすべてで横スクロールが発生する（受け入れ条件12違反）

- ファイル: `templates/index.html:100-104`（`.footer-bar { ... margin:0 -20px -80px; }`）と `templates/index.html:123-129`（`@media(max-width:600px){ .footer-bar { margin:0 -14px -90px; } }`）
- テスト: `tests/test_ui_responsive.py::test_no_horizontal_scroll_and_buttons_visible[320|390|1024]`
- 実測: `document.documentElement.scrollWidth` が `window.innerWidth` を常に超える
  - 320px → scrollWidth **334**（+14）
  - 390px → scrollWidth **404**（+14）
  - 1024px → scrollWidth **1044**（+20）
- 原因: `.footer-bar` は `<main>` の兄弟要素として `<body>` 直下に置かれているが（`<main>...</main>` の後に `<div class="footer-bar">` が続く構造）、CSSは「`<main>` の左右パディングを打ち消して画面端まで広げる」ための負のマージン（デスクトップ`-20px`・モバイル`-14px`）を単純に `.footer-bar` 自身へ適用している。`.footer-bar` の親は `<body>`（パディングなし）なので、この負のマージンは `<main>` のパディングを打ち消すのではなく、ビューポート自体の外側へ `.footer-bar` をはみ出させてしまう。実測でも `.footer-bar` の `getBoundingClientRect()` は `left:-14, right:334`（320px viewport時）で、右側に14px分ビューポート外へはみ出していることを確認した。
- 再現手順:
  1. `templates/index.html` を Flask経由で表示し、ビューポート幅320pxにする
  2. `document.querySelector('.footer-bar').getBoundingClientRect()` を評価する
  3. `right` がビューポート幅を超えていることを確認する（例: 320px幅で `right:334`）
  4. `document.documentElement.scrollWidth`（334）が `window.innerWidth`（320）を超え、横スクロールが発生する
- 期待値: 受け入れ条件12「320px・390px・1024px幅で横スクロールがなく」を満たすこと（`scrollWidth <= innerWidth`）
- 実装かデザインどちらの問題か: **実装のCSSバグ**。`.footer-bar` を `<main>` の内側（またはパディングが0のフルブリード用ラッパー）に移すか、負のマージンではなく `width:100vw` ＋ `position:fixed`／`left:0;right:0` 方式にするなど、`<main>`のパディングとは独立して画面幅ぴったりに広げる実装に直す必要がある。design.md自体は色・余白の流用元を指定しているのみで、この負マージン手法自体は実装時の判断（9章にも明記なし）。テストの書き方の問題ではなく、実際にPlaywrightで測定した横スクロールを検出したもの。

### 2. `compute_health_status` が「停止中」でも直近対象日の未実行を理由に「要確認」を返す（受け入れ条件11・design.md 9章の記述と食い違う可能性）

- ファイル: `core.py:460-485`（`compute_health_status`）
- テスト: `tests/test_core.py::test_health_status_stopped_does_not_warn_on_stale_tick`
- 再現手順:
  1. 初期状態（`enabled=0`、初期値のまま）でDBを作成
  2. `core.record_tick(conn, 2026-09-16 06:00 JST)` を記録
  3. `core.compute_health_status(conn, now=2026-09-16 08:00 JST, is_holiday=ダミー(常にFalse), skip_dates=空)` を呼ぶ
  4. 結果が `"warning"`（実測）になる
- 期待値（design.mdの読み方）: design.md 9章「状態表示」に「動作確認は 未確認（tick記録なし）／正常／要確認（有効中にtickが10分以上止まっている、直近の対象日が失敗・結果不明・未実行）」「停止中はtickの停止を要確認にしない」とある。停止中はtickの停止それ自体は要確認にしないと明記されているが、`compute_health_status`の実装では「直近の対象日の未実行・失敗・結果不明」チェック（`most_recent_target_date`以降のロジック）が`enabled`の状態に関わらず常に実行される。そのため、自動送信を一度も有効化していない・意図的に停止しているだけのアカウントでも、現在時刻が（デフォルトの）送信予定時刻+20分を過ぎていれば「要確認」バッジが出てしまう。design.mdの受け入れ条件1「初回導入時は停止中で起動し...を表示する」や条件11「停止中・開始前・対象外の日には未送信警告を出さない」の趣旨（停止中は静かにしている）から見ると、この「要確認」表示は意図と食い違う可能性が高い。一方でmonitor.py側の`check_once`は「未送信・失敗・結果不明」チェックを`settings.get("enabled")`で正しくガードしており（`monitor.py:66`）、UI側の`compute_health_status`だけこのガードが抜けているように見える。
- 実装かデザインどちらの問題か: design.mdの記述は「有効中に」が両方の条件（tick停滞／直近対象日の未実行等）にかかるのか、後半だけにかかるのか厳密には曖昧。ただし、monitor.pyの実装や受け入れ条件11の趣旨と比較すると、`compute_health_status`側だけ`enabled`チェックが漏れている実装ミスの可能性が高いと判断した。断定はできないため、設計者に確認したうえで、バグならガード追加、意図通りならdesign.mdの当該箇所を明確化することを推奨する。

## カバーできなかった受け入れ条件・検証範囲の限界

- **本番フォームの実際のDOM構造**: `sender.py`の`submit()`はローカルfixture HTML（`tests/fixtures/hoicheese_form.py`で生成、`sender.login`/`sender.open_contact_form`をモンキーパッチして読み込ませる）でのみ検証した。本物の `note.hoi-sys.com` のフォームが今後変更された場合の追従は未検証（設計方針どおり、本番アクセスをしないテストでは原理的に検証できない領域）。
- **`is_already_sent`のPlaywright locator挙動**: fixture作成時に気づいた点として、`page.locator("button", has_text=...)` の `.count()` は非表示（`display:none`）要素も含めて数える。本物のサイトのDOM構造がテストfixtureと異なり「非表示だが存在する」ボタンを使っている場合、`is_already_sent()`が誤検知する可能性は理論上あるが、これは本番DOMを見ないと確認できない。
- **本物のcron/launchd導入後の動作**: `launchd/`配下のplistは今回のテストで一切読み込んでいない（design.md 9章の指示どおり）。実際にlaunchdへ登録した後の挙動（起動間隔のずれ・ポート競合時のフォールバック等）は未検証。
- **`monitor.py`の`tick_stale`と`missing`が同時に発火するケースの相互作用**: 両方の条件が同時に真になる場合の通知順序・内容は個別には確認したが、同一tickで両方発火するケースの通知本文の組み合わせまでは検証していない。
- **画面のポーリング失敗時の「情報取得失敗」表示（`stale-notice`）**: `loadStatus()`のfetch失敗時にクラス`show`が付くことはコード上確認したが、Playwrightでネットワーク遮断を模した自動テストまでは書いていない（実装コードは変更していないため目視でのロジック確認に留めた）。
- **Webの待受ポート・バインド失敗時の127.0.0.1フォールバック**（`app.py:main`）: `app.run()`を直接使う経路のため、実際にポートを占有させて`OSError`を発生させるテストは、本レポートのテストでは行っていない（`main()`関数自体は呼んでおらず、Flaskのtest_client / `make_server`経由でのみ検証）。
- **`healthcheck.sh`が「旧方式・未使用」である旨の記載**: ファイル冒頭コメントの目視確認のみで、自動テストは書いていない（design.md 9章の記載事項であり、4章の受け入れ条件そのものではないため）。

## 実行方法

```
cd /Users/yasuyuki/Developer/haicheese
.venv/bin/python -m pytest -q
```

新規追加ファイル: `tests/conftest.py`, `tests/test_core.py`, `tests/test_notifier.py`, `tests/test_runner.py`, `tests/test_monitor.py`, `tests/test_sender.py`, `tests/test_app.py`, `tests/test_ui_responsive.py`, `tests/fixtures/__init__.py`, `tests/fixtures/hoicheese_form.py`。実装コード（`core.py`・`sender.py`・`notifier.py`・`runner.py`・`monitor.py`・`app.py`・`templates/index.html`）は変更していない。

## 再テスト（2026-09-16）

失敗2件（footer-barの横スクロール、停止中の要確認判定）を `227b128` で修正後に全件を再実行し、114件すべて成功した。
