"""core.py: 受け入れ条件 1,2,3,4,5,9(DB層),10(effective_result),11(状態判定) のテスト。"""

from __future__ import annotations

import random
import sqlite3
from datetime import date, timedelta

import pytest

import core
from conftest import jst, make_is_holiday, make_skip_dates


# ── 1. 初期値 ────────────────────────────────────────────────

def test_initial_settings_defaults(conn):
    s = core.get_settings(conn)
    assert s["enabled"] == 0
    assert s["start_date"] is None
    assert s["end_date"] is None
    assert s["submit_time"] == "07:00"
    assert s["measurement_time"] == "06:50"
    assert s["temp_min"] == 365
    assert s["temp_max"] == 368
    assert s["pool_participation"] == 1
    assert s["revision"] == 1
    # 開始日を勝手に2027年7月などへ設定していないこと
    assert s["start_date"] is None and s["end_date"] is None


def test_initial_operational_status_is_stopped(conn):
    s = core.get_settings(conn)
    today = date(2026, 9, 16)
    assert core.compute_operational_status(s, today) == "stopped"


# ── 2. 永続化 ────────────────────────────────────────────────

def test_settings_persist_across_reconnect(isolated_data_dir):
    conn1 = core.connect()
    cleaned = {
        "enabled": True, "start_date": "2026-10-01", "end_date": "2026-12-31",
        "submit_time": "08:15", "measurement_time": "06:55",
        "temp_min": 366, "temp_max": 370, "pool_participation": False,
    }
    ok, err = core.save_settings(conn1, cleaned, 1, jst(2026, 9, 16, 9, 0).isoformat())
    assert ok and err is None
    conn1.close()

    # 再接続（サービス再起動を模す）しても残ること
    conn2 = core.connect()
    s = core.get_settings(conn2)
    assert bool(s["enabled"]) is True
    assert s["start_date"] == "2026-10-01"
    assert s["end_date"] == "2026-12-31"
    assert s["submit_time"] == "08:15"
    assert s["measurement_time"] == "06:55"
    assert s["temp_min"] == 366
    assert s["temp_max"] == 370
    assert bool(s["pool_participation"]) is False
    assert s["revision"] == 2
    conn2.close()


def test_save_settings_revision_conflict_keeps_previous(conn):
    before = core.get_settings(conn)
    cleaned = {
        "enabled": True, "start_date": None, "end_date": None,
        "submit_time": "09:00", "measurement_time": "06:50",
        "temp_min": 365, "temp_max": 368, "pool_participation": True,
    }
    ok, err = core.save_settings(conn, cleaned, expected_revision=999, now_iso=jst(2026, 9, 16).isoformat())
    assert ok is False
    assert err == "revision_conflict"
    after = core.get_settings(conn)
    assert after == before


def test_save_settings_failure_keeps_previous_settings(conn):
    """保存処理の途中で例外が起きた場合、コミットされず以前の設定が残ること。"""
    before = core.get_settings(conn)

    class FailingConn:
        def __init__(self, real):
            self._real = real

        def execute(self, sql, params=()):
            if sql.strip().startswith("UPDATE settings"):
                raise sqlite3.OperationalError("simulated write failure")
            return self._real.execute(sql, params)

        def commit(self):
            return self._real.commit()

        def rollback(self):
            return self._real.rollback()

    cleaned = {
        "enabled": True, "start_date": None, "end_date": None,
        "submit_time": "09:00", "measurement_time": "06:50",
        "temp_min": 365, "temp_max": 368, "pool_participation": True,
    }
    with pytest.raises(sqlite3.OperationalError):
        core.save_settings(FailingConn(conn), cleaned, 1, jst(2026, 9, 16).isoformat())

    after = core.get_settings(conn)
    assert after == before


def test_set_enabled_only_changes_enabled(conn):
    core.save_settings(
        conn,
        {"enabled": True, "start_date": "2026-01-01", "end_date": "2026-02-01",
         "submit_time": "08:00", "measurement_time": "06:55",
         "temp_min": 360, "temp_max": 370, "pool_participation": False},
        1, jst(2026, 9, 16).isoformat(),
    )
    before = core.get_settings(conn)
    updated = core.set_enabled(conn, False, jst(2026, 9, 16, 12, 0).isoformat())
    assert updated["enabled"] == 0
    for key in ("start_date", "end_date", "submit_time", "measurement_time",
                "temp_min", "temp_max", "pool_participation"):
        assert updated[key] == before[key]
    assert updated["revision"] == before["revision"] + 1


# ── 3. 運用状態 ───────────────────────────────────────────────

@pytest.mark.parametrize("settings,today,expected", [
    ({"enabled": 0, "start_date": None, "end_date": None}, date(2026, 9, 16), "stopped"),
    ({"enabled": 1, "start_date": "2026-10-01", "end_date": None}, date(2026, 9, 16), "waiting"),
    ({"enabled": 1, "start_date": "2026-09-01", "end_date": "2026-09-30"}, date(2026, 9, 16), "active"),
    ({"enabled": 1, "start_date": None, "end_date": "2026-09-01"}, date(2026, 9, 16), "ended"),
    # 初日を含む
    ({"enabled": 1, "start_date": "2026-09-16", "end_date": None}, date(2026, 9, 16), "active"),
    # 最終日を含む
    ({"enabled": 1, "start_date": None, "end_date": "2026-09-16"}, date(2026, 9, 16), "active"),
    # 最終日の翌日は終了
    ({"enabled": 1, "start_date": None, "end_date": "2026-09-15"}, date(2026, 9, 16), "ended"),
    # 開始日前日はまだ開始待ち
    ({"enabled": 1, "start_date": "2026-09-17", "end_date": None}, date(2026, 9, 16), "waiting"),
])
def test_operational_status_matrix(settings, today, expected):
    assert core.compute_operational_status(settings, today) == expected


# ── 4. 次回予定 ───────────────────────────────────────────────

def _settings(**overrides):
    base = {
        "enabled": 1, "start_date": None, "end_date": None,
        "submit_time": "07:00", "measurement_time": "06:50",
        "temp_min": 365, "temp_max": 368, "pool_participation": 1,
        "updated_at": jst(2000, 1, 1).isoformat(),
    }
    base.update(overrides)
    return base


def test_next_run_when_stopped():
    settings = _settings(enabled=0)
    result = core.compute_next_run(
        settings, jst(2026, 9, 16, 6, 0), make_is_holiday(), make_skip_dates()(), lambda d: False,
    )
    assert result == {"date": None, "reason": "stopped"}


def test_next_run_when_ended():
    settings = _settings(end_date="2026-09-01")
    result = core.compute_next_run(
        settings, jst(2026, 9, 16, 6, 0), make_is_holiday(), make_skip_dates()(), lambda d: False,
    )
    assert result == {"date": None, "reason": "ended"}


def test_next_run_no_target_in_period():
    # 期間が土曜日1日だけなので、対象日が存在しない
    settings = _settings(start_date="2026-09-19", end_date="2026-09-19")
    result = core.compute_next_run(
        settings, jst(2026, 9, 16, 6, 0), make_is_holiday(), make_skip_dates()(), lambda d: False,
    )
    assert result == {"date": None, "reason": "no_target"}


def test_next_run_finds_next_weekday():
    settings = _settings()
    result = core.compute_next_run(
        settings, jst(2026, 9, 16, 6, 0), make_is_holiday(), make_skip_dates()(), lambda d: False,
    )
    assert result == {"date": "2026-09-16", "reason": None}


def test_next_run_skips_holiday_and_skip_dates():
    settings = _settings()
    is_holiday = make_is_holiday({"2026-09-16"})
    skip = make_skip_dates({"2026-09-17"})()
    result = core.compute_next_run(
        settings, jst(2026, 9, 16, 6, 0), is_holiday, skip, lambda d: False,
    )
    assert result == {"date": "2026-09-18", "reason": None}


def test_next_run_today_past_window_moves_to_tomorrow():
    settings = _settings(submit_time="07:00")
    # 今日の送信可能時間帯（07:00〜07:03未満）はもう過ぎている
    result = core.compute_next_run(
        settings, jst(2026, 9, 16, 8, 0), make_is_holiday(), make_skip_dates()(),
        lambda d: False,
    )
    assert result["date"] == "2026-09-17"


def test_next_run_today_already_run_moves_to_tomorrow():
    settings = _settings()
    result = core.compute_next_run(
        settings, jst(2026, 9, 16, 6, 0), make_is_holiday(), make_skip_dates()(),
        lambda d: d == "2026-09-16",
    )
    assert result["date"] == "2026-09-17"


# ── 5. 体温 ──────────────────────────────────────────────────

def test_generate_temp_within_range_inclusive():
    seen = set()
    rng = random.Random(12345)
    for _ in range(2000):
        v = core.generate_temp(365, 368, rng)
        assert 365 <= v <= 368
        seen.add(v)
    assert seen == {365, 366, 367, 368}  # 両端含む


def test_generate_temp_min_equals_max():
    rng = random.Random(1)
    for _ in range(20):
        assert core.generate_temp(365, 365, rng) == 365


@pytest.mark.parametrize("payload,expected_error_substr", [
    ({"temp_min": "36.55", "temp_max": "36.8"}, "0.1℃刻み"),
    ({"temp_min": "34.9", "temp_max": "36.8"}, "35.0〜42.0"),
    ({"temp_min": "36.5", "temp_max": "42.1"}, "35.0〜42.0"),
    ({"temp_min": "36.8", "temp_max": "36.5"}, "下限は上限以下"),
    ({"temp_min": "abc", "temp_max": "36.8"}, "temp_minが不正"),
])
def test_validate_settings_rejects_bad_temperature(payload, expected_error_substr):
    base_payload = {
        "enabled": False, "start_date": None, "end_date": None,
        "submit_time": "07:00", "measurement_time": "06:50",
        "pool_participation": True,
    }
    base_payload.update(payload)
    cleaned, errors = core.validate_settings(base_payload)
    assert cleaned is None
    assert any(expected_error_substr in e for e in errors)


def test_validate_settings_accepts_equal_min_max():
    payload = {
        "enabled": False, "start_date": None, "end_date": None,
        "submit_time": "07:00", "measurement_time": "06:50",
        "temp_min": "36.5", "temp_max": "36.5", "pool_participation": True,
    }
    cleaned, errors = core.validate_settings(payload)
    assert errors == []
    assert cleaned["temp_min"] == cleaned["temp_max"] == 365


def test_validate_settings_rejects_reversed_dates():
    payload = {
        "enabled": True, "start_date": "2026-10-01", "end_date": "2026-09-01",
        "submit_time": "07:00", "measurement_time": "06:50",
        "temp_min": "36.5", "temp_max": "36.8", "pool_participation": True,
    }
    cleaned, errors = core.validate_settings(payload)
    assert cleaned is None
    assert any("開始日は終了日より前" in e for e in errors)


def test_validate_settings_rejects_measurement_time_not_5min_step():
    payload = {
        "enabled": True, "start_date": None, "end_date": None,
        "submit_time": "07:00", "measurement_time": "06:52",
        "temp_min": "36.5", "temp_max": "36.8", "pool_participation": True,
    }
    cleaned, errors = core.validate_settings(payload)
    assert cleaned is None
    assert any("5分刻み" in e for e in errors)


def test_validate_settings_submit_time_allows_1min_step():
    payload = {
        "enabled": True, "start_date": None, "end_date": None,
        "submit_time": "08:17", "measurement_time": "06:50",
        "temp_min": "36.5", "temp_max": "36.8", "pool_participation": True,
    }
    cleaned, errors = core.validate_settings(payload)
    assert errors == []
    assert cleaned["submit_time"] == "08:17"


# ── 9. 送信判定・重複防止（DB層） ─────────────────────────────

def test_should_attempt_submit_within_window_then_blocked_after(conn):
    settings = _settings(submit_time="07:00")
    settings["updated_at"] = jst(2000, 1, 1).isoformat()
    has_run = lambda d: core.get_run(conn, d) is not None

    assert core.should_attempt_submit(
        settings, jst(2026, 9, 16, 7, 0, 0), make_is_holiday(), make_skip_dates()(), has_run,
    ) is True
    assert core.should_attempt_submit(
        settings, jst(2026, 9, 16, 7, 2, 59), make_is_holiday(), make_skip_dates()(), has_run,
    ) is True
    # 3分window: window_endちょうどはfalse
    assert core.should_attempt_submit(
        settings, jst(2026, 9, 16, 7, 3, 0), make_is_holiday(), make_skip_dates()(), has_run,
    ) is False
    # 予定時刻より前もfalse
    assert core.should_attempt_submit(
        settings, jst(2026, 9, 16, 6, 59, 59), make_is_holiday(), make_skip_dates()(), has_run,
    ) is False


def test_should_attempt_submit_blocks_when_updated_after_scheduled():
    """過去時刻への変更・予定時刻後の有効化では即時送信しない。"""
    settings = _settings(submit_time="07:00")
    settings["updated_at"] = jst(2026, 9, 16, 10, 0).isoformat()  # 予定時刻(07:00)より後に変更
    result = core.should_attempt_submit(
        settings, jst(2026, 9, 16, 10, 1), make_is_holiday(), make_skip_dates()(), lambda d: False,
    )
    assert result is False


def test_should_attempt_submit_blocks_when_run_exists():
    settings = _settings(submit_time="07:00")
    settings["updated_at"] = jst(2000, 1, 1).isoformat()
    result = core.should_attempt_submit(
        settings, jst(2026, 9, 16, 7, 0), make_is_holiday(), make_skip_dates()(), lambda d: True,
    )
    assert result is False


def test_claim_run_prevents_duplicate(conn):
    ok1 = core.claim_run(conn, "2026-09-16", "07:00", "{}", jst(2026, 9, 16, 7, 0).isoformat())
    ok2 = core.claim_run(conn, "2026-09-16", "07:00", "{}", jst(2026, 9, 16, 7, 1).isoformat())
    assert ok1 is True
    assert ok2 is False  # UNIQUE制約で二重確保できない


def test_claim_run_concurrent_only_one_wins(isolated_data_dir):
    """同時起動を模して2スレッドから同時にclaim_runしても片方しか成功しない。"""
    import threading

    results = []

    def worker():
        c = core.connect()
        try:
            results.append(core.claim_run(c, "2026-09-16", "07:00", "{}", jst(2026, 9, 16, 7, 0).isoformat()))
        finally:
            c.close()

    threads = [threading.Thread(target=worker) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sum(1 for r in results if r) == 1
    assert sum(1 for r in results if not r) == 4


# ── 10. 結果不明・15分超 ─────────────────────────────────────

def test_effective_result_running_over_15min_becomes_unknown():
    run = {"result": "running", "started_at": jst(2026, 9, 16, 7, 0).isoformat()}
    now = jst(2026, 9, 16, 7, 16)  # 16分後
    assert core.effective_result(run, now) == "unknown"


def test_effective_result_running_exactly_15min_stays_running():
    run = {"result": "running", "started_at": jst(2026, 9, 16, 7, 0).isoformat()}
    now = jst(2026, 9, 16, 7, 15)  # ちょうど15分（>ではないのでrunningのまま）
    assert core.effective_result(run, now) == "running"


def test_effective_result_running_under_15min_stays_running():
    run = {"result": "running", "started_at": jst(2026, 9, 16, 7, 0).isoformat()}
    now = jst(2026, 9, 16, 7, 10)
    assert core.effective_result(run, now) == "running"


def test_effective_result_passthrough_for_finished_results():
    for result in ("success", "already_sent", "failed", "unknown", "cancelled"):
        run = {"result": result, "started_at": jst(2026, 9, 16, 7, 0).isoformat()}
        assert core.effective_result(run, jst(2026, 9, 16, 8, 0)) == result


def test_effective_result_none_run():
    assert core.effective_result(None, jst(2026, 9, 16, 8, 0)) is None


# ── 11. 動作確認状態 ─────────────────────────────────────────

def test_health_status_unknown_without_tick(conn):
    health = core.compute_health_status(conn, jst(2026, 9, 16, 7, 30), make_is_holiday(), make_skip_dates()())
    assert health == "unknown"


def test_health_status_stopped_does_not_warn_on_stale_tick(conn):
    # 無効なのでtickが古くても要確認にしない
    old_tick = jst(2026, 9, 16, 6, 0)
    core.record_tick(conn, old_tick)
    health = core.compute_health_status(conn, jst(2026, 9, 16, 8, 0), make_is_holiday(), make_skip_dates()())
    assert health != "warning"


def test_health_status_warning_when_enabled_tick_stale(conn):
    core.save_settings(
        conn,
        {"enabled": True, "start_date": None, "end_date": None,
         "submit_time": "07:00", "measurement_time": "06:50",
         "temp_min": 365, "temp_max": 368, "pool_participation": True},
        1, jst(2000, 1, 1).isoformat(),
    )
    core.record_tick(conn, jst(2026, 9, 16, 7, 0))
    health = core.compute_health_status(conn, jst(2026, 9, 16, 7, 11), make_is_holiday(), make_skip_dates()())
    assert health == "warning"


def test_health_status_ok_when_nothing_to_worry_about(conn):
    core.save_settings(
        conn,
        {"enabled": True, "start_date": None, "end_date": None,
         "submit_time": "07:00", "measurement_time": "06:50",
         "temp_min": 365, "temp_max": 368, "pool_participation": True},
        1, jst(2000, 1, 1).isoformat(),
    )
    core.record_tick(conn, jst(2026, 9, 16, 7, 0))
    # まだ送信予定時刻+20分を過ぎていない
    health = core.compute_health_status(conn, jst(2026, 9, 16, 7, 5), make_is_holiday(), make_skip_dates()())
    assert health == "ok"
