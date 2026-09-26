"""monitor.py: 受け入れ条件 11 のテスト。

notify_fn は必ずダミーに差し替え、Discord・Macへは一切通知しない。
"""

from __future__ import annotations

import core
import monitor
from conftest import jst, make_is_holiday, make_skip_dates


def enable(conn, **overrides):
    settings = {
        "enabled": True, "start_date": None, "end_date": None,
        "submit_time": "07:00", "measurement_time": "06:50",
        "temp_min": 365, "temp_max": 368, "pool_participation": True,
    }
    settings.update(overrides)
    core.save_settings(conn, settings, 1, jst(2000, 1, 1).isoformat())


def make_capturing_notify(store: list):
    def _notify(mac_title, mac_msg, discord_msg, *, mac=True, discord=True, event_key=None):
        store.append((mac_title, mac_msg, discord_msg))
        return {"status": "ok"}
    return _notify


def test_monitor_does_not_warn_when_stopped(conn):
    # enabled=False（初期値のまま）
    core.record_tick(conn, jst(2026, 9, 16, 6, 0))  # 古いtickでも
    notified = []
    result = monitor.check_once(
        conn,
        now_fn=lambda: jst(2026, 9, 16, 8, 0),
        notify_fn=make_capturing_notify(notified),
        is_holiday=make_is_holiday(),
        load_skip_dates=make_skip_dates(),
    )
    assert result["fired"] == []
    assert notified == []


def test_monitor_does_not_warn_before_start_date(conn):
    enable(conn, start_date="2026-10-01")
    core.record_tick(conn, jst(2026, 9, 16, 7, 55))
    notified = []
    result = monitor.check_once(
        conn,
        now_fn=lambda: jst(2026, 9, 16, 8, 0),
        notify_fn=make_capturing_notify(notified),
        is_holiday=make_is_holiday(),
        load_skip_dates=make_skip_dates(),
    )
    assert result["fired"] == []


def test_monitor_does_not_warn_on_non_target_day(conn):
    enable(conn)
    core.record_tick(conn, jst(2026, 9, 19, 7, 55))
    notified = []
    result = monitor.check_once(
        conn,
        now_fn=lambda: jst(2026, 9, 19, 8, 0),  # 土曜
        notify_fn=make_capturing_notify(notified),
        is_holiday=make_is_holiday(),
        load_skip_dates=make_skip_dates(),
    )
    assert result["fired"] == []


def test_monitor_warns_after_20min_of_no_run(conn):
    enable(conn, submit_time="07:00")
    core.record_tick(conn, jst(2026, 9, 16, 7, 25))
    notified = []
    result = monitor.check_once(
        conn,
        now_fn=lambda: jst(2026, 9, 16, 7, 20, 1),  # 予定+20分1秒
        notify_fn=make_capturing_notify(notified),
        is_holiday=make_is_holiday(),
        load_skip_dates=make_skip_dates(),
    )
    kinds = [f["kind"] for f in result["fired"]]
    assert "missing" in kinds
    assert len(notified) >= 1


def test_monitor_same_alert_fires_only_once(conn):
    enable(conn, submit_time="07:00")
    core.record_tick(conn, jst(2026, 9, 16, 7, 25))
    now_fn = lambda: jst(2026, 9, 16, 7, 21)
    notified = []
    first = monitor.check_once(
        conn, now_fn=now_fn, notify_fn=make_capturing_notify(notified),
        is_holiday=make_is_holiday(), load_skip_dates=make_skip_dates(),
    )
    second = monitor.check_once(
        conn, now_fn=lambda: jst(2026, 9, 16, 7, 26), notify_fn=make_capturing_notify(notified),
        is_holiday=make_is_holiday(), load_skip_dates=make_skip_dates(),
    )
    assert len(first["fired"]) == 1
    assert second["fired"] == []  # 同じ日・種類は2回目は発報しない
    assert len(notified) == 1


def test_monitor_follows_time_change(conn):
    """変更後の時刻に追従する: 07:00基準なら+20分超だが、
    08:15へ変更していれば、まだ+20分に達していないので警告しない。"""
    enable(conn, submit_time="08:15")
    core.record_tick(conn, jst(2026, 9, 16, 8, 20))
    notified = []
    result = monitor.check_once(
        conn,
        now_fn=lambda: jst(2026, 9, 16, 8, 25),  # 08:15+20分=08:35にまだ達していない
        notify_fn=make_capturing_notify(notified),
        is_holiday=make_is_holiday(),
        load_skip_dates=make_skip_dates(),
    )
    assert result["fired"] == []


def test_monitor_warns_failed_result(conn):
    enable(conn, submit_time="07:00")
    core.claim_run(conn, "2026-09-16", "07:00", "{}", jst(2026, 9, 16, 7, 0).isoformat())
    core.finish_run(conn, "2026-09-16", result="failed", reason="ログイン失敗",
                     temp=None, finished_at_iso=jst(2026, 9, 16, 7, 1).isoformat())
    core.record_tick(conn, jst(2026, 9, 16, 7, 25))
    notified = []
    result = monitor.check_once(
        conn,
        now_fn=lambda: jst(2026, 9, 16, 7, 21),
        notify_fn=make_capturing_notify(notified),
        is_holiday=make_is_holiday(),
        load_skip_dates=make_skip_dates(),
    )
    kinds = [f["kind"] for f in result["fired"]]
    assert "failed" in kinds


def test_monitor_does_not_warn_on_success(conn):
    enable(conn, submit_time="07:00")
    core.claim_run(conn, "2026-09-16", "07:00", "{}", jst(2026, 9, 16, 7, 0).isoformat())
    core.finish_run(conn, "2026-09-16", result="success", reason="送信完了",
                     temp=365, finished_at_iso=jst(2026, 9, 16, 7, 1).isoformat())
    core.record_tick(conn, jst(2026, 9, 16, 7, 25))
    notified = []
    result = monitor.check_once(
        conn,
        now_fn=lambda: jst(2026, 9, 16, 7, 30),
        notify_fn=make_capturing_notify(notified),
        is_holiday=make_is_holiday(),
        load_skip_dates=make_skip_dates(),
    )
    assert result["fired"] == []


def test_monitor_tick_stale_warns_when_enabled(conn):
    enable(conn)
    core.record_tick(conn, jst(2026, 9, 16, 7, 0))
    notified = []
    result = monitor.check_once(
        conn,
        now_fn=lambda: jst(2026, 9, 16, 7, 11),  # 11分停滞
        notify_fn=make_capturing_notify(notified),
        is_holiday=make_is_holiday(),
        load_skip_dates=make_skip_dates(),
    )
    kinds = [f["kind"] for f in result["fired"]]
    assert "tick_stale" in kinds
