"""runner.py: 受け入れ条件 6(引数の受け渡し),7,8,9,10 のテスト。

submit_fn・notify_fn・now_fn・is_holiday・load_skip_dates はすべて
ダミーに差し替え、本番サイト・Discord・osascriptへは一切アクセスしない。
"""

from __future__ import annotations

from dataclasses import dataclass

import core
import runner
from conftest import jst, make_is_holiday, make_skip_dates


@dataclass
class FakeOutcome:
    result: str
    reason: str = "テスト"


def make_fake_submit(outcome: FakeOutcome, capture: dict):
    def _submit(**kwargs):
        capture.update(kwargs)
        return outcome
    return _submit


def make_fake_notify(capture: list, status: str = "ok"):
    def _notify(mac_title, mac_msg, discord_msg, *, mac=True, discord=True, event_key=None):
        capture.append({
            "mac_title": mac_title, "mac_msg": mac_msg, "discord_msg": discord_msg,
            "mac": mac, "discord": discord,
        })
        return {"status": status}
    return _notify


def enable(conn, **overrides):
    settings = {
        "enabled": True, "start_date": None, "end_date": None,
        "submit_time": "07:00", "measurement_time": "06:50",
        "temp_min": 365, "temp_max": 368, "pool_participation": True,
    }
    settings.update(overrides)
    core.save_settings(conn, settings, 1, jst(2000, 1, 1).isoformat())


# ── 6. sender に渡る値 ──────────────────────────────────────

def test_run_once_passes_measurement_time_and_pool_to_submit_fn(conn):
    enable(conn, measurement_time="06:55", pool_participation=False, submit_time="07:00")
    captured = {}
    runner.run_once(
        conn,
        now_fn=lambda: jst(2026, 9, 16, 7, 0, 0),
        submit_fn=make_fake_submit(FakeOutcome("success"), captured),
        notify_fn=make_fake_notify([]),
        is_holiday=make_is_holiday(),
        load_skip_dates=make_skip_dates(),
        email="dummy@example.invalid", password="dummy",
    )
    assert captured["hour"] == "06"
    assert captured["minute"] == "55"
    assert captured["pool_participation"] is False


def test_run_once_passes_pool_participation_true(conn):
    enable(conn, pool_participation=True)
    captured = {}
    runner.run_once(
        conn,
        now_fn=lambda: jst(2026, 9, 16, 7, 0, 0),
        submit_fn=make_fake_submit(FakeOutcome("success"), captured),
        notify_fn=make_fake_notify([]),
        is_holiday=make_is_holiday(),
        load_skip_dates=make_skip_dates(),
        email="dummy@example.invalid", password="dummy",
    )
    assert captured["pool_participation"] is True


# ── 7. 送信時刻変更の反映・3分window ─────────────────────────

def test_run_once_does_not_send_at_old_time_after_change_to_0815(conn):
    # 07:00に有効化されていたが、その後08:15へ変更（変更操作自体は過去なのでOK）
    enable(conn, submit_time="08:15")
    # 07:00には送らない
    result = runner.run_once(
        conn,
        now_fn=lambda: jst(2026, 9, 16, 7, 0, 0),
        submit_fn=make_fake_submit(FakeOutcome("success"), {}),
        notify_fn=make_fake_notify([]),
        is_holiday=make_is_holiday(),
        load_skip_dates=make_skip_dates(),
        email="dummy@example.invalid", password="dummy",
    )
    assert result == {"attempted": False}
    assert core.get_run(conn, "2026-09-16") is None


def test_run_once_sends_at_new_time_0815(conn):
    enable(conn, submit_time="08:15")
    captured = {}
    result = runner.run_once(
        conn,
        now_fn=lambda: jst(2026, 9, 16, 8, 15, 30),
        submit_fn=make_fake_submit(FakeOutcome("success"), captured),
        notify_fn=make_fake_notify([]),
        is_holiday=make_is_holiday(),
        load_skip_dates=make_skip_dates(),
        email="dummy@example.invalid", password="dummy",
    )
    assert result["attempted"] is True
    assert result["result"] == "success"
    run = core.get_run(conn, "2026-09-16")
    assert run["scheduled_time"] == "08:15"


def test_run_once_does_not_send_immediately_when_changed_to_past_time(conn):
    """過去時刻への変更で即時送信しない: 10:00に、既に過ぎた09:59へ変更しても
    その変更直後（window内）には送らない。"""
    conn.execute("BEGIN IMMEDIATE")
    conn.execute(
        "UPDATE settings SET enabled=1, submit_time='09:59', updated_at=? WHERE id=1",
        (jst(2026, 9, 16, 10, 0).isoformat(),),
    )
    conn.commit()
    # 09:59〜10:02未満のwindow内だが、updated_at(10:00)がscheduled_dt(09:59)以降なのでブロックされる
    result = runner.run_once(
        conn,
        now_fn=lambda: jst(2026, 9, 16, 10, 0, 30),
        submit_fn=make_fake_submit(FakeOutcome("success"), {}),
        notify_fn=make_fake_notify([]),
        is_holiday=make_is_holiday(),
        load_skip_dates=make_skip_dates(),
        email="dummy@example.invalid", password="dummy",
    )
    assert result == {"attempted": False}


def test_run_once_does_not_send_when_enabled_after_scheduled_time(conn):
    """予定時刻後の有効化で即時送信しない。"""
    # 10:00に有効化（submit_timeは07:00のまま）
    conn.execute("BEGIN IMMEDIATE")
    conn.execute(
        "UPDATE settings SET enabled=1, submit_time='07:00', updated_at=? WHERE id=1",
        (jst(2026, 9, 16, 10, 0).isoformat(),),
    )
    conn.commit()
    result = runner.run_once(
        conn,
        now_fn=lambda: jst(2026, 9, 16, 10, 1, 0),
        submit_fn=make_fake_submit(FakeOutcome("success"), {}),
        notify_fn=make_fake_notify([]),
        is_holiday=make_is_holiday(),
        load_skip_dates=make_skip_dates(),
        email="dummy@example.invalid", password="dummy",
    )
    assert result == {"attempted": False}


def test_run_once_3min_window_boundary(conn):
    enable(conn, submit_time="07:00")
    # ちょうど3分後は対象外（未満のみ許可）
    result = runner.run_once(
        conn,
        now_fn=lambda: jst(2026, 9, 16, 7, 3, 0),
        submit_fn=make_fake_submit(FakeOutcome("success"), {}),
        notify_fn=make_fake_notify([]),
        is_holiday=make_is_holiday(),
        load_skip_dates=make_skip_dates(),
        email="dummy@example.invalid", password="dummy",
    )
    assert result == {"attempted": False}


# ── 8. 土日祝日・skip_dates ────────────────────────────────

def test_run_once_skips_weekend(conn):
    enable(conn)
    result = runner.run_once(
        conn,
        now_fn=lambda: jst(2026, 9, 19, 7, 0, 0),  # 土曜
        submit_fn=make_fake_submit(FakeOutcome("success"), {}),
        notify_fn=make_fake_notify([]),
        is_holiday=make_is_holiday(),
        load_skip_dates=make_skip_dates(),
        email="dummy@example.invalid", password="dummy",
    )
    assert result == {"attempted": False}


def test_run_once_skips_holiday(conn):
    enable(conn)
    result = runner.run_once(
        conn,
        now_fn=lambda: jst(2026, 9, 16, 7, 0, 0),
        submit_fn=make_fake_submit(FakeOutcome("success"), {}),
        notify_fn=make_fake_notify([]),
        is_holiday=make_is_holiday({"2026-09-16"}),
        load_skip_dates=make_skip_dates(),
        email="dummy@example.invalid", password="dummy",
    )
    assert result == {"attempted": False}


def test_run_once_skips_skip_dates_txt(conn):
    enable(conn)
    result = runner.run_once(
        conn,
        now_fn=lambda: jst(2026, 9, 16, 7, 0, 0),
        submit_fn=make_fake_submit(FakeOutcome("success"), {}),
        notify_fn=make_fake_notify([]),
        is_holiday=make_is_holiday(),
        load_skip_dates=make_skip_dates({"2026-09-16"}),
        email="dummy@example.invalid", password="dummy",
    )
    assert result == {"attempted": False}


# ── 9. 二重起動・再起動・再送しない ────────────────────────

def test_run_once_does_not_resend_same_day_after_success(conn):
    enable(conn)
    now_fn = lambda: jst(2026, 9, 16, 7, 0, 0)
    first = runner.run_once(
        conn, now_fn=now_fn,
        submit_fn=make_fake_submit(FakeOutcome("success"), {}),
        notify_fn=make_fake_notify([]),
        is_holiday=make_is_holiday(), load_skip_dates=make_skip_dates(),
        email="dummy@example.invalid", password="dummy",
    )
    assert first["result"] == "success"

    # 「再起動」を模して同じ日・同じ時刻でもう一度呼ぶ
    second_capture = {}
    second = runner.run_once(
        conn, now_fn=now_fn,
        submit_fn=make_fake_submit(FakeOutcome("success"), second_capture),
        notify_fn=make_fake_notify([]),
        is_holiday=make_is_holiday(), load_skip_dates=make_skip_dates(),
        email="dummy@example.invalid", password="dummy",
    )
    # should_attempt_submit自体がhas_runを見て弾くため、2回目はwindow判定より前で終わる
    # （"already_claimed"はshould_attempt_submit通過後にclaim_runが競合したときだけ付く理由）
    assert second == {"attempted": False}
    assert second_capture == {}  # submit_fnは呼ばれていない


def test_run_once_does_not_resend_after_time_change_same_day(conn):
    """同日に成功済みなら、送信時刻を後ろへ変更しても再送しない。"""
    enable(conn, submit_time="07:00")
    runner.run_once(
        conn, now_fn=lambda: jst(2026, 9, 16, 7, 0, 0),
        submit_fn=make_fake_submit(FakeOutcome("success"), {}),
        notify_fn=make_fake_notify([]),
        is_holiday=make_is_holiday(), load_skip_dates=make_skip_dates(),
        email="dummy@example.invalid", password="dummy",
    )
    # 同日中に08:15へ変更
    conn.execute("BEGIN IMMEDIATE")
    conn.execute(
        "UPDATE settings SET submit_time='08:15', updated_at=? WHERE id=1",
        (jst(2026, 9, 16, 7, 30).isoformat(),),
    )
    conn.commit()

    result = runner.run_once(
        conn, now_fn=lambda: jst(2026, 9, 16, 8, 15, 30),
        submit_fn=make_fake_submit(FakeOutcome("success"), {}),
        notify_fn=make_fake_notify([]),
        is_holiday=make_is_holiday(), load_skip_dates=make_skip_dates(),
        email="dummy@example.invalid", password="dummy",
    )
    assert result == {"attempted": False}


def test_run_once_already_sent_is_recorded_as_normal_skip(conn):
    enable(conn)
    result = runner.run_once(
        conn, now_fn=lambda: jst(2026, 9, 16, 7, 0, 0),
        submit_fn=make_fake_submit(FakeOutcome("already_sent", "開いた時点で送信済み"), {}),
        notify_fn=make_fake_notify([]),
        is_holiday=make_is_holiday(), load_skip_dates=make_skip_dates(),
        email="dummy@example.invalid", password="dummy",
    )
    assert result["result"] == "already_sent"
    run = core.get_run(conn, "2026-09-16")
    assert run["result"] == "already_sent"

    # 翌日以降にもう一度呼んでも同日は再送しない（同じ日を指す2回目呼び出し）
    second = runner.run_once(
        conn, now_fn=lambda: jst(2026, 9, 16, 7, 1, 0),
        submit_fn=make_fake_submit(FakeOutcome("success"), {}),
        notify_fn=make_fake_notify([]),
        is_holiday=make_is_holiday(), load_skip_dates=make_skip_dates(),
        email="dummy@example.invalid", password="dummy",
    )
    assert second == {"attempted": False}


def test_run_once_concurrent_double_start_only_one_sends(isolated_data_dir):
    """同時起動（2プロセス相当）を模す: 2つの接続がほぼ同時にrun_onceを呼んでも、
    片方だけが送信し、もう片方は already_claimed としてスキップする。"""
    import threading

    conn_setup = core.connect()
    enable(conn_setup)
    conn_setup.close()

    results = []
    barrier = threading.Barrier(2)

    def worker():
        c = core.connect()
        try:
            barrier.wait()
            r = runner.run_once(
                c, now_fn=lambda: jst(2026, 9, 16, 7, 0, 0),
                submit_fn=make_fake_submit(FakeOutcome("success"), {}),
                notify_fn=make_fake_notify([]),
                is_holiday=make_is_holiday(), load_skip_dates=make_skip_dates(),
                email="dummy@example.invalid", password="dummy",
            )
            results.append(r)
        finally:
            c.close()

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    attempted = [r for r in results if r.get("attempted")]
    skipped = [r for r in results if not r.get("attempted")]
    assert len(attempted) == 1
    assert len(skipped) == 1
    # 早い者勝ちで負けた方は理由が分かる形で記録される
    assert skipped[0].get("reason") in ("already_claimed", None)

    conn_check = core.connect()
    run = core.get_run(conn_check, "2026-09-16")
    assert run["result"] == "success"
    conn_check.close()


def test_run_once_does_not_resend_after_failed_or_unknown(conn):
    enable(conn)
    runner.run_once(
        conn, now_fn=lambda: jst(2026, 9, 16, 7, 0, 0),
        submit_fn=make_fake_submit(FakeOutcome("failed", "ネットワークエラー"), {}),
        notify_fn=make_fake_notify([]),
        is_holiday=make_is_holiday(), load_skip_dates=make_skip_dates(),
        email="dummy@example.invalid", password="dummy",
    )
    second = runner.run_once(
        conn, now_fn=lambda: jst(2026, 9, 16, 7, 1, 0),
        submit_fn=make_fake_submit(FakeOutcome("success"), {}),
        notify_fn=make_fake_notify([]),
        is_holiday=make_is_holiday(), load_skip_dates=make_skip_dates(),
        email="dummy@example.invalid", password="dummy",
    )
    assert second == {"attempted": False}


# ── 10. 通信失敗・通知失敗の分離 ──────────────────────────

def test_run_once_failed_result_is_not_success(conn):
    enable(conn)
    result = runner.run_once(
        conn, now_fn=lambda: jst(2026, 9, 16, 7, 0, 0),
        submit_fn=make_fake_submit(FakeOutcome("failed", "通信失敗"), {}),
        notify_fn=make_fake_notify([]),
        is_holiday=make_is_holiday(), load_skip_dates=make_skip_dates(),
        email="dummy@example.invalid", password="dummy",
    )
    assert result["result"] == "failed"
    run = core.get_run(conn, "2026-09-16")
    assert run["result"] == "failed"
    assert run["result"] != "success"


def test_run_once_notify_failure_does_not_change_submit_result(conn):
    enable(conn)
    notify_calls = []
    result = runner.run_once(
        conn, now_fn=lambda: jst(2026, 9, 16, 7, 0, 0),
        submit_fn=make_fake_submit(FakeOutcome("success"), {}),
        notify_fn=make_fake_notify(notify_calls, status="failed"),
        is_holiday=make_is_holiday(), load_skip_dates=make_skip_dates(),
        email="dummy@example.invalid", password="dummy",
    )
    assert result["result"] == "success"
    run = core.get_run(conn, "2026-09-16")
    assert run["result"] == "success"
    assert run["notify_status"] == "failed"
    assert len(notify_calls) == 1


def test_run_once_generated_temp_within_configured_range(conn):
    enable(conn, temp_min=370, temp_max=372)
    captured = {}
    runner.run_once(
        conn, now_fn=lambda: jst(2026, 9, 16, 7, 0, 0),
        submit_fn=make_fake_submit(FakeOutcome("success"), captured),
        notify_fn=make_fake_notify([]),
        is_holiday=make_is_holiday(), load_skip_dates=make_skip_dates(),
        email="dummy@example.invalid", password="dummy",
    )
    assert captured["temp_str"] in ("37.0", "37.1", "37.2")
    run = core.get_run(conn, "2026-09-16")
    assert 370 <= run["temp"] <= 372
