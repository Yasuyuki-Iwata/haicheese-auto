# レビュー結果 (2026-09-16)

対象: `git diff 639a6d0..HEAD`（Web設定画面 app.py・core.py・sender.py・runner.py・monitor.py・notifier.py 一式の追加）。
`docs/design.md` 4章・9章、`docs/test-report.md`（再テストで114件成功）を前提に、コードは変更せずレビューした。
`.venv/bin/python -m pytest -q` を実行し114件成功を再確認済み。本番サイト・Discord API・osascript・`.env`・crontab/launchd・pushはいずれも実行していない。

## 重大な指摘

指摘なし。

二重送信防止（`fcntl`ロック→`runs` UNIQUEの順、`claim_run`の`BEGIN IMMEDIATE`、`should_attempt_submit`の3分窓＋`updated_at`比較）、停止の再確認（`check_stop`を「確認する」前・最後の「送信」前の2箇所で読み直し）、成功判定（`sender.submit`が送信後に再読込して「送信済み」表示を確認してから`success`にする、コミット後の例外は`unknown`）、認証・CSRF（`secrets.compare_digest`による定数時間比較、HttpOnly＋SameSite=Strict Cookie、Origin検証、JSON限定、werkzeugログの`?token=`伏せ字）、監視の重複通知抑止（`alerts`テーブルのUNIQUE）、`.gitignore`（`.env*`・`data/`・`logs/`・`.venv/`除外）、`git log -p`での秘密情報混入の有無は、いずれも実装どおりで実害のある欠陥は見つからなかった。

## 軽微な指摘

- [`app.py:146-157` (`run_for_display`) / `app.py:172-173` (`build_status`)] `runs.result` が `running` のまま15分を超えた場合、`core.effective_result()` で内部的には「結果不明」扱いにする設計（design.md 9章）だが、`run_for_display`と`running_now`はDBの生の`result`をそのまま使っており、`effective_result`を通していない（`compute_health_status`だけが`effective_result`を使っている）。このため、runner.pyが送信確定後にクラッシュ・強制終了した場合、動作確認バッジは15分後に「要確認」になるのに、直近の結果一覧と「処理中です。送信確定済みなら停止後も結果が届くことがあります」の表示は15分を過ぎても恒久的に「処理中」のまま変わらない。ユーザーが「まだ処理中で結果待ち」と誤解し続ける可能性がある。修正案: `run_for_display`（および`running_now`の判定）でも`core.effective_result(run, now)`を通した値を使う。

- [`core.py:244`, `core.py:258` (`validate_settings`)] `enabled = bool(payload.get("enabled", False))` と `pool_participation = bool(payload.get(...))` は、JSONの真偽値ではなく文字列が渡った場合に`bool("false")`が`True`になる（Pythonの仕様上、空文字列以外はすべて truthy）。現在の`templates/index.html`は常にJSのbooleanを送るため実害はないが、`/api/settings`・`/api/preview`はJSON APIとして公開されており、将来ブラウザ以外（自作スクリプト・ショートカット等）から`"enabled": "false"`のような文字列で叩いた場合、意図せず自動送信が有効化される。修正案: 文字列`"false"`/`"true"`も含めて明示的に真偽判定するか、bool型以外を拒否する。

- [`notifier.py:14-16` (`notify_mac`)] `f'display notification "{message}" with title "{title}"'` はエスケープなしでAppleScript文字列に埋め込んでいる（`submit.py`からの移設のみで今回の変更による新規混入ではない）。`message`/`title`にはPlaywrightの例外メッセージ（`outcome.reason`）が入ることがあり、対象サイトの表示内容次第では二重引用符やバックスラッシュを含む文字列でAppleScript構文が壊れる、またはコマンド注入に繋がる可能性が理論上ある。今回の差分での新規導入ではないため重大には分類しないが、次に触る機会があればエスケープ処理（`"`と`\`の置換、または`osascript`に引数で渡す方式への変更）を推奨する。

## 指摘なし（上記以外）

- XSS: `templates/index.html`のJSは`textContent`／`createElement`のみを使い、`innerHTML`にサーバー値を入れている箇所はない。`className`に使う`run.result`・`operational_status`・`health_status`はいずれもサーバー側の固定列挙値。
- 認証・CSRF・Cookie属性・Origin検証・トークン比較・ログ伏せ字は設計どおり実装されている。
- 初期DBは`enabled=0`・日付未設定で、launchd plistを読み込んでも（`should_attempt_submit`が`enabled`を見るため）即送信にはならない。`submit.py`直接実行は終了コード1で送信しない。
- Gitコミット履歴（`git log -p 639a6d0..HEAD`）に実際の資格情報・個人データは含まれておらず、テスト用の値はすべて`dummy`/`test-dummy-*`/`example.invalid`。`data/`・`logs/`・`.venv/`・`.env`はコミットされていない。
