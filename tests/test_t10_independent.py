"""T10（Discord通知の共通配信部移行）の独立検証テスト。

実装者（tests/test_discord_delivery_adapter.py・test_monitor.py・test_runner.py）とは
別の視点で、docs/design-discord-delivery.md の受け入れ条件を確認する。ここでは
ダミーのnotify_fnではなく、本物の共通配信部 discord_delivery（HTTPだけ偽物に差し替え）を
notifier.notify経由で実際に動かし、monitor.check_once・runner.run_onceから使う。

守ること:
- 実装コード（notifier.py・monitor.py・runner.py・core.py・discord_delivery.py）は変更しない。
- 本物のDiscord API・本番の送信箱・本番DB・osascriptには一度もアクセスしない
  （tests/conftest.py の autouse fixture が送信箱・DBを一時ディレクトリへ隔離する。
  notify_mac は本ファイルのfixtureで偽物に差し替える）。
- 偽トークンは一目で偽物と分かる値にし、送信箱・戻り値・ログ・例外文に出ないことを確認する。
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time as _time
import uuid
from dataclasses import dataclass
from pathlib import Path

import pytest

import core
import monitor
import notifier
import runner
from conftest import REPO_ROOT, jst, make_is_holiday, make_skip_dates

DM_CHANNEL = "999888777000"
FAKE_TOKEN = "T10-INDEP-FAKE-TOKEN-e3b0c44298fc-not-a-real-secret"


# ── 共通ヘルパー ──────────────────────────────────────────────

def enable(conn, **overrides):
    settings = {
        "enabled": True, "start_date": None, "end_date": None,
        "submit_time": "07:00", "measurement_time": "06:50",
        "temp_min": 365, "temp_max": 368, "pool_participation": True,
    }
    settings.update(overrides)
    core.save_settings(conn, settings, 1, jst(2000, 1, 1).isoformat())


@dataclass
class FakeOutcome:
    result: str
    reason: str = "テスト"


def make_fake_submit(outcome: FakeOutcome, capture: dict):
    def _submit(**kwargs):
        capture.update(kwargs)
        return outcome
    return _submit


def make_real_notify(dm_channel: str = DM_CHANNEL):
    """monitor.check_once・runner.run_once のどちらからも呼べる、本物のnotifier.notifyへの橋渡し。"""
    def _notify(mac_title, mac_msg, discord_msg, *, mac=True, discord=True, event_key=None):
        return notifier.notify(
            mac_title, mac_msg, discord_msg,
            dm_channel=dm_channel, mac=mac, discord=discord, event_key=event_key,
        )
    return _notify


def _write_fake_env(path: Path, token: str = FAKE_TOKEN) -> None:
    path.write_text(f"DISCORD_BOT_TOKEN={token}\n", encoding="utf-8")


@pytest.fixture
def real_delivery(monkeypatch, tmp_path):
    """本物の共通配信部を使い、送信は各テストでHTTPだけ偽物に差し替える。

    - NO_DISCORD を外し、実際の送信フローを通す（HTTPは必ず偽物に差し替えること）。
    - notifier.TOKEN_FILE を偽の.envへ差し替える（本物の.envは読まない）。
    - notifier.notify_mac を偽物にし、実際にosascriptを起動しない。
    """
    monkeypatch.delenv("NO_DISCORD", raising=False)
    fake_env = tmp_path / "fake.env"
    _write_fake_env(fake_env)
    monkeypatch.setattr(notifier, "TOKEN_FILE", fake_env)
    monkeypatch.setattr(notifier, "notify_mac", lambda *a, **k: None)
    delivery = notifier._delivery_module()
    return delivery


def make_fake_http_success(calls):
    def _f(url, data, headers):
        calls.append({"url": url, "data": data, "headers": dict(headers)})
        body = json.dumps({"id": str(len(calls)), "channel_id": "chan"}).encode("utf-8")
        return 200, {}, body
    return _f


def make_fake_http_status(status_code, calls):
    def _f(url, data, headers):
        calls.append({"url": url, "data": data, "headers": dict(headers)})
        body = json.dumps({"message": "blocked", "code": 0}).encode("utf-8")
        return status_code, {}, body
    return _f


def make_fake_http_transient_failure(delivery, calls):
    def _f(url, data, headers):
        calls.append({"url": url, "data": data, "headers": dict(headers)})
        raise delivery._TransientTransportError()
    return _f


def _events_by_source(delivery, source="haicheese"):
    db_path = delivery._outbox_dir() / "outbox.sqlite3"
    if not db_path.exists():
        return []
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT id, status, event_key, reason, destination, payload FROM events "
            "WHERE source=? ORDER BY id",
            (source,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _dump_outbox_text(delivery):
    db_path = delivery._outbox_dir() / "outbox.sqlite3"
    if not db_path.exists():
        return ""
    conn = sqlite3.connect(str(db_path))
    try:
        parts = []
        for table in ("events", "parts", "attachments"):
            rows = conn.execute(f"SELECT * FROM {table}").fetchall()
            parts.append(repr(rows))
        return "\n".join(parts)
    finally:
        conn.close()


# ── 1. 通信断でqueued→alertsに記録→drainでsent。本文一致 ─────────────

def test_network_outage_queues_then_drain_delivers_matching_text(conn, real_delivery, monkeypatch):
    enable(conn, submit_time="07:00")
    core.record_tick(conn, jst(2026, 9, 16, 7, 25))
    calls = []
    monkeypatch.setattr(real_delivery, "_http_request", make_fake_http_transient_failure(real_delivery, calls))

    notify_fn = make_real_notify()
    result = monitor.check_once(
        conn,
        now_fn=lambda: jst(2026, 9, 16, 7, 21),
        notify_fn=notify_fn,
        is_holiday=make_is_holiday(),
        load_skip_dates=make_skip_dates(),
    )

    assert [f["kind"] for f in result["fired"]] == ["missing"]
    assert core.has_alert(conn, "2026-09-16", "missing") is True
    assert len(calls) == 1

    rows = _events_by_source(real_delivery)
    assert len(rows) == 1
    assert rows[0]["status"] == "queued"
    assert FAKE_TOKEN not in json.dumps(rows[0])

    # 通信復旧を模してdrain。再試行の待ち時間（60秒）が経つよう_nowを進める。
    real_now = _time.time()
    monkeypatch.setattr(real_delivery, "_now", lambda: real_now + 61)
    sent_calls = []
    monkeypatch.setattr(real_delivery, "_http_request", make_fake_http_success(sent_calls))
    drained = real_delivery.drain(limit=10)

    assert any(r.get("status") == "sent" for r in drained)
    sent_payload = json.loads(sent_calls[-1]["data"].decode("utf-8"))
    expected_msg = "⚠️ はいチーズ！ 2026-09-16の連絡帳: 予定時刻を過ぎても実行された形跡がありません"
    assert sent_payload["content"] == expected_msg


# ── 2. 共通部の読み込み失敗→retry。直してから記録・1回だけ届く ──────────

def test_delivery_module_load_failure_retries_then_delivers_once(conn, real_delivery, monkeypatch, tmp_path):
    enable(conn, submit_time="07:00")
    core.record_tick(conn, jst(2026, 9, 16, 7, 25))
    calls = []
    monkeypatch.setattr(real_delivery, "_http_request", make_fake_http_success(calls))

    broken_dir = tmp_path / "broken-lib"
    broken_dir.mkdir()
    monkeypatch.setenv("HAICHEESE_DELIVERY_LIB", str(broken_dir))

    notify_fn = make_real_notify()
    first = monitor.check_once(
        conn, now_fn=lambda: jst(2026, 9, 16, 7, 21), notify_fn=notify_fn,
        is_holiday=make_is_holiday(), load_skip_dates=make_skip_dates(),
    )
    assert first["fired"] == []
    assert first["retry"] == [{"target_date": "2026-09-16", "kind": "missing"}]
    assert core.has_alert(conn, "2026-09-16", "missing") is False
    assert calls == []  # 共通部を読み込めていないのでHTTPには到達しない

    # 共通部を直す（環境変数を戻す＝標準の~/.local/libに戻る）
    monkeypatch.delenv("HAICHEESE_DELIVERY_LIB", raising=False)

    second = monitor.check_once(
        conn, now_fn=lambda: jst(2026, 9, 16, 7, 26), notify_fn=notify_fn,
        is_holiday=make_is_holiday(), load_skip_dates=make_skip_dates(),
    )
    assert [f["kind"] for f in second["fired"]] == ["missing"]
    assert core.has_alert(conn, "2026-09-16", "missing") is True

    rows = _events_by_source(real_delivery)
    assert len(rows) == 1  # Discordへは1回だけ届いた
    assert len(calls) == 1


def test_registered_but_record_failure_does_not_double_deliver(conn, real_delivery, monkeypatch):
    """1回目にDiscordへの登録は成功したが、record_alert_once自体が例外で失敗した状況。
    2回目はevent_keyによりsuppressedとなり、Discordへは二重に届かない。"""
    enable(conn, submit_time="07:00")
    core.record_tick(conn, jst(2026, 9, 16, 7, 25))
    calls = []
    monkeypatch.setattr(real_delivery, "_http_request", make_fake_http_success(calls))

    original_record_alert_once = core.record_alert_once
    state = {"n": 0}

    def flaky_record_alert_once(conn_, target, kind, created_at_iso):
        state["n"] += 1
        if state["n"] == 1:
            raise RuntimeError("simulated disk full")
        return original_record_alert_once(conn_, target, kind, created_at_iso)

    monkeypatch.setattr(core, "record_alert_once", flaky_record_alert_once)

    notify_fn = make_real_notify()
    with pytest.raises(RuntimeError):
        monitor.check_once(
            conn, now_fn=lambda: jst(2026, 9, 16, 7, 21), notify_fn=notify_fn,
            is_holiday=make_is_holiday(), load_skip_dates=make_skip_dates(),
        )
    assert core.has_alert(conn, "2026-09-16", "missing") is False
    assert len(calls) == 1  # 1回目でDiscordには既に届いている

    second = monitor.check_once(
        conn, now_fn=lambda: jst(2026, 9, 16, 7, 26), notify_fn=notify_fn,
        is_holiday=make_is_holiday(), load_skip_dates=make_skip_dates(),
    )
    assert [f["kind"] for f in second["fired"]] == ["missing"]
    assert core.has_alert(conn, "2026-09-16", "missing") is True
    assert len(calls) == 1  # 2回目はsuppressedになりHTTPは呼ばれない（二重に届かない）

    rows = _events_by_source(real_delivery)
    assert len(rows) == 1


# ── 3. 400/403でdead→記録・log.error・以後通知しない ────────────────

@pytest.mark.parametrize("status_code", [400, 403])
def test_discord_4xx_marks_dead_logs_error_and_stops_retrying(conn, real_delivery, monkeypatch, caplog, status_code):
    enable(conn, submit_time="07:00")
    core.record_tick(conn, jst(2026, 9, 16, 7, 25))
    calls = []
    monkeypatch.setattr(real_delivery, "_http_request", make_fake_http_status(status_code, calls))

    notify_fn = make_real_notify()
    caplog.set_level(logging.ERROR, logger="haicheese.monitor")
    result = monitor.check_once(
        conn, now_fn=lambda: jst(2026, 9, 16, 7, 21), notify_fn=notify_fn,
        is_holiday=make_is_holiday(), load_skip_dates=make_skip_dates(),
    )
    assert [f["kind"] for f in result["fired"]] == ["missing"]
    assert core.has_alert(conn, "2026-09-16", "missing") is True
    assert any("dead" in rec.message for rec in caplog.records)

    rows = _events_by_source(real_delivery)
    assert len(rows) == 1
    assert rows[0]["status"] == "dead"
    assert rows[0]["reason"] == f"http_{status_code}"
    assert len(calls) == 1

    # 以後通知しない: has_alertで弾かれ、notify_fnすら呼ばれずHTTP呼び出し数も増えない
    second = monitor.check_once(
        conn, now_fn=lambda: jst(2026, 9, 16, 7, 30), notify_fn=notify_fn,
        is_holiday=make_is_holiday(), load_skip_dates=make_skip_dates(),
    )
    assert second["fired"] == []
    assert len(calls) == 1


# ── 4. 同日の複数種類は別event_key、翌日の同種は再送 ────────────────

def test_tick_stale_and_missing_use_distinct_event_keys_and_repeat_next_day(conn, real_delivery, monkeypatch):
    enable(conn, submit_time="07:00")
    core.record_tick(conn, jst(2026, 9, 16, 6, 30))  # tick_stale同時に発生させる
    calls = []
    monkeypatch.setattr(real_delivery, "_http_request", make_fake_http_success(calls))

    notify_fn = make_real_notify()
    day1 = monitor.check_once(
        conn, now_fn=lambda: jst(2026, 9, 16, 7, 21), notify_fn=notify_fn,
        is_holiday=make_is_holiday(), load_skip_dates=make_skip_dates(),
    )
    assert sorted(f["kind"] for f in day1["fired"]) == ["missing", "tick_stale"]

    rows = _events_by_source(real_delivery)
    assert len(rows) == 2
    assert sorted(r["event_key"] for r in rows) == [
        "haicheese:alert:2026-09-16:missing",
        "haicheese:alert:2026-09-16:tick_stale",
    ]

    # 翌日: tickは更新されないまま、当日分も未送信のまま→両方とも再び届く
    day2 = monitor.check_once(
        conn, now_fn=lambda: jst(2026, 9, 17, 7, 21), notify_fn=notify_fn,
        is_holiday=make_is_holiday(), load_skip_dates=make_skip_dates(),
    )
    assert sorted(f["kind"] for f in day2["fired"]) == ["missing", "tick_stale"]

    rows2 = _events_by_source(real_delivery)
    assert len(rows2) == 4
    assert sorted(r["event_key"] for r in rows2) == [
        "haicheese:alert:2026-09-16:missing",
        "haicheese:alert:2026-09-16:tick_stale",
        "haicheese:alert:2026-09-17:missing",
        "haicheese:alert:2026-09-17:tick_stale",
    ]
    assert len(calls) == 4


# ── 5. runner.run_once: notify_status のマッピング・runs.resultは不変 ──

def test_run_once_success_and_discord_sent_sets_notify_status_ok(conn, real_delivery, monkeypatch):
    enable(conn, submit_time="07:00")
    calls = []
    monkeypatch.setattr(real_delivery, "_http_request", make_fake_http_success(calls))

    result = runner.run_once(
        conn, now_fn=lambda: jst(2026, 9, 16, 7, 0, 0),
        submit_fn=make_fake_submit(FakeOutcome("success"), {}),
        notify_fn=make_real_notify(),
        is_holiday=make_is_holiday(), load_skip_dates=make_skip_dates(),
        email="dummy@example.invalid", password="dummy",
    )
    assert result["result"] == "success"
    run = core.get_run(conn, "2026-09-16")
    assert run["result"] == "success"
    assert run["notify_status"] == "ok"
    assert len(_events_by_source(real_delivery)) == 1


def test_run_once_failed_and_discord_queued_sets_notify_status_queued(conn, real_delivery, monkeypatch):
    enable(conn, submit_time="07:00")
    calls = []
    monkeypatch.setattr(real_delivery, "_http_request", make_fake_http_transient_failure(real_delivery, calls))

    result = runner.run_once(
        conn, now_fn=lambda: jst(2026, 9, 17, 7, 0, 0),
        submit_fn=make_fake_submit(FakeOutcome("failed", "ネットワークエラー"), {}),
        notify_fn=make_real_notify(),
        is_holiday=make_is_holiday(), load_skip_dates=make_skip_dates(),
        email="dummy@example.invalid", password="dummy",
    )
    assert result["result"] == "failed"
    run = core.get_run(conn, "2026-09-17")
    assert run["result"] == "failed"  # 送信結果は通知結果で変わらない
    assert run["notify_status"] == "queued"
    rows = _events_by_source(real_delivery)
    assert len(rows) == 1
    assert rows[0]["status"] == "queued"


def test_run_once_unknown_and_discord_dead_sets_notify_status_partial(conn, real_delivery, monkeypatch):
    enable(conn, submit_time="07:00")
    calls = []
    monkeypatch.setattr(real_delivery, "_http_request", make_fake_http_status(403, calls))

    result = runner.run_once(
        conn, now_fn=lambda: jst(2026, 9, 18, 7, 0, 0),
        submit_fn=make_fake_submit(FakeOutcome("unknown", "タイムアウト"), {}),
        notify_fn=make_real_notify(),
        is_holiday=make_is_holiday(), load_skip_dates=make_skip_dates(),
        email="dummy@example.invalid", password="dummy",
    )
    assert result["result"] == "unknown"
    run = core.get_run(conn, "2026-09-18")
    assert run["result"] == "unknown"
    assert run["notify_status"] == "partial"


def test_run_once_mac_and_discord_both_fail_sets_notify_status_failed(conn, real_delivery, monkeypatch):
    enable(conn, submit_time="07:00")
    monkeypatch.setattr(notifier, "notify_mac", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("osascript失敗")))
    calls = []
    monkeypatch.setattr(real_delivery, "_http_request", make_fake_http_status(403, calls))

    result = runner.run_once(
        conn, now_fn=lambda: jst(2026, 9, 21, 7, 0, 0),
        submit_fn=make_fake_submit(FakeOutcome("failed", "ログイン失敗"), {}),
        notify_fn=make_real_notify(),
        is_holiday=make_is_holiday(), load_skip_dates=make_skip_dates(),
        email="dummy@example.invalid", password="dummy",
    )
    assert result["result"] == "failed"
    run = core.get_run(conn, "2026-09-21")
    assert run["result"] == "failed"
    assert run["notify_status"] == "failed"


def test_run_once_already_sent_does_not_register_discord(conn, real_delivery, monkeypatch):
    enable(conn, submit_time="07:00")
    calls = []
    monkeypatch.setattr(real_delivery, "_http_request", make_fake_http_success(calls))

    before = len(_events_by_source(real_delivery))
    result = runner.run_once(
        conn, now_fn=lambda: jst(2026, 9, 22, 7, 0, 0),
        submit_fn=make_fake_submit(FakeOutcome("already_sent", "開いた時点で送信済み"), {}),
        notify_fn=make_real_notify(),
        is_holiday=make_is_holiday(), load_skip_dates=make_skip_dates(),
        email="dummy@example.invalid", password="dummy",
    )
    assert result["result"] == "already_sent"
    assert len(_events_by_source(real_delivery)) == before  # 送信箱に行が増えない
    assert calls == []


def test_run_once_cancelled_does_not_register_discord(conn, real_delivery, monkeypatch):
    enable(conn, submit_time="07:00")
    calls = []
    monkeypatch.setattr(real_delivery, "_http_request", make_fake_http_success(calls))

    before = len(_events_by_source(real_delivery))
    result = runner.run_once(
        conn, now_fn=lambda: jst(2026, 9, 23, 7, 0, 0),
        submit_fn=make_fake_submit(FakeOutcome("cancelled", "手動停止"), {}),
        notify_fn=make_real_notify(),
        is_holiday=make_is_holiday(), load_skip_dates=make_skip_dates(),
        email="dummy@example.invalid", password="dummy",
    )
    assert result["result"] == "cancelled"
    assert len(_events_by_source(real_delivery)) == before  # 送信箱に行が増えない
    assert calls == []


# ── 6. トークンの非露出・DISCORD_BOT_TOKENを読まない ────────────────

def test_fake_token_never_appears_in_outbox_return_values_or_logs(conn, monkeypatch, tmp_path, caplog):
    unique_token = f"T10-INDEP-{uuid.uuid4()}-NOT-REAL"
    fake_env = tmp_path / "unique.env"
    _write_fake_env(fake_env, unique_token)

    monkeypatch.delenv("NO_DISCORD", raising=False)
    monkeypatch.setattr(notifier, "TOKEN_FILE", fake_env)
    monkeypatch.setattr(notifier, "notify_mac", lambda *a, **k: None)
    delivery = notifier._delivery_module()

    calls = []

    def fake_http_capture_success(url, data, headers):
        calls.append({"url": url, "data": data, "headers": dict(headers)})
        return 200, {}, json.dumps({"id": "1", "channel_id": "chan"}).encode("utf-8")

    monkeypatch.setattr(delivery, "_http_request", fake_http_capture_success)

    enable(conn, submit_time="07:00")
    core.record_tick(conn, jst(2026, 9, 16, 7, 25))
    caplog.set_level(logging.INFO)

    monitor_result = monitor.check_once(
        conn, now_fn=lambda: jst(2026, 9, 16, 7, 21), notify_fn=make_real_notify(),
        is_holiday=make_is_holiday(), load_skip_dates=make_skip_dates(),
    )

    # トークンはAuthorizationヘッダには入る（送信直前に読むのは仕様どおり）。
    assert any(unique_token in h["headers"].get("Authorization", "") for h in calls)
    # それ以外のどこにも出ない。
    assert unique_token not in json.dumps(monitor_result)
    assert unique_token not in _dump_outbox_text(delivery)
    assert unique_token not in caplog.text

    attachments_dir = delivery._outbox_dir() / "attachments"
    for f in attachments_dir.glob("*"):
        assert unique_token not in f.read_bytes().decode("utf-8", errors="ignore")

    runner_result = runner.run_once(
        conn, now_fn=lambda: jst(2026, 9, 17, 7, 0, 0),
        submit_fn=make_fake_submit(FakeOutcome("success"), {}),
        notify_fn=make_real_notify(),
        is_holiday=make_is_holiday(), load_skip_dates=make_skip_dates(),
        email="dummy@example.invalid", password="dummy",
    )
    assert unique_token not in json.dumps(runner_result)
    run_row = core.get_run(conn, "2026-09-17")
    assert unique_token not in json.dumps(dict(run_row), default=str)
    assert unique_token not in _dump_outbox_text(delivery)
    assert unique_token not in caplog.text


def test_runner_and_monitor_main_do_not_read_discord_bot_token_env():
    runner_src = (REPO_ROOT / "runner.py").read_text(encoding="utf-8")
    monitor_src = (REPO_ROOT / "monitor.py").read_text(encoding="utf-8")
    assert "DISCORD_BOT_TOKEN" not in runner_src
    assert "DISCORD_BOT_TOKEN" not in monitor_src
    assert "getenv(\"DISCORD_BOT_TOKEN\")" not in runner_src
    assert "getenv(\"DISCORD_BOT_TOKEN\")" not in monitor_src
    # DISCORD_DM_CHANNELは今どおり読む(トークンだけ読まない)ことも合わせて確認する。
    assert "DISCORD_DM_CHANNEL" in runner_src
    assert "DISCORD_DM_CHANNEL" in monitor_src


# ── 7. 停止中(enabled=0)ではrunner/monitorとも送信箱に何も登録しない ──

def test_disabled_settings_register_nothing_in_outbox_for_runner_and_monitor(conn, real_delivery, monkeypatch):
    # settings.enabled はinit_dbの初期値のままFalse(明示的に有効化しない)
    core.record_tick(conn, jst(2026, 9, 16, 6, 0))  # 古いtickでもenabled=0なら要確認にしない
    calls = []
    monkeypatch.setattr(real_delivery, "_http_request", make_fake_http_success(calls))

    notify_calls = []
    base_notify = make_real_notify()

    def counting_notify(*args, **kwargs):
        notify_calls.append((args, kwargs))
        return base_notify(*args, **kwargs)

    monitor_result = monitor.check_once(
        conn, now_fn=lambda: jst(2026, 9, 16, 8, 0), notify_fn=counting_notify,
        is_holiday=make_is_holiday(), load_skip_dates=make_skip_dates(),
    )
    assert monitor_result["fired"] == []
    assert notify_calls == []
    assert _events_by_source(real_delivery) == []

    runner_result = runner.run_once(
        conn, now_fn=lambda: jst(2026, 9, 16, 7, 0, 0),
        submit_fn=make_fake_submit(FakeOutcome("success"), {}),
        notify_fn=counting_notify,
        is_holiday=make_is_holiday(), load_skip_dates=make_skip_dates(),
        email="dummy@example.invalid", password="dummy",
    )
    assert runner_result == {"attempted": False}
    assert notify_calls == []
    assert _events_by_source(real_delivery) == []
    assert calls == []
