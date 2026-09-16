#!/usr/bin/env python3
"""毎分の送信判定。launchdが毎分1回起動する想定。

判定・排他制御・送信結果の記録は run_once() に閉じ込め、時計・送信・通知・
祝日判定・skip日付の読み込みをすべて引数で差し替えられるようにしてある。
本番へ実際に送信するのは __main__ から real_submit を渡したときだけ。
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import logging
import os
import sys
from datetime import date
from pathlib import Path
from typing import Callable, Optional

import core

SCRIPT_DIR = Path(__file__).resolve().parent


def _setup_logger(log_dir: Path) -> logging.Logger:
    log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("haicheese.runner")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        h = logging.FileHandler(log_dir / f"{core.default_now().strftime('%Y-%m')}.log", encoding="utf-8")
        h.setFormatter(logging.Formatter("%(asctime)s  %(message)s"))
        logger.addHandler(h)
    return logger


@contextlib.contextmanager
def _try_lock(path: Path):
    """他のrunnerが実行中なら None を返し、そうでなければファイルロックを取る。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    f = open(path, "w")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        yield None
        return
    try:
        yield f
    finally:
        fcntl.flock(f, fcntl.LOCK_UN)
        f.close()


def run_once(
    conn,
    *,
    now_fn: Callable = core.default_now,
    submit_fn: Callable = None,
    notify_fn: Callable = None,
    is_holiday: Callable = core.default_is_holiday,
    load_skip_dates: Callable = core.default_load_skip_dates,
    screenshot_dir: Path = None,
    email: str = None,
    password: str = None,
    logger: Optional[logging.Logger] = None,
) -> dict:
    """1回分の判定・送信を行う。戻り値は要約辞書（テスト・手動確認用）。"""
    log = logger or logging.getLogger("haicheese.runner")
    now = now_fn()
    core.record_tick(conn, now)

    settings = core.get_settings(conn)
    skip_dates = load_skip_dates()

    def has_run(d: str) -> bool:
        return core.get_run(conn, d) is not None

    if not core.should_attempt_submit(settings, now, is_holiday, skip_dates, has_run):
        return {"attempted": False}

    target_date = now.date().isoformat()
    snapshot = json.dumps(settings, ensure_ascii=False, default=str)

    claimed = core.claim_run(conn, target_date, settings["submit_time"], snapshot, now.isoformat())
    if not claimed:
        log.info(f"{target_date}: 既にrunsがあるため送信しません（重複防止）")
        return {"attempted": False, "reason": "already_claimed"}

    log.info(f"{target_date}: 送信を開始します")

    def check_stop() -> bool:
        current = core.get_settings(conn)
        return not current.get("enabled")

    temp_tenths = core.generate_temp(settings["temp_min"], settings["temp_max"])
    temp_str = core.temp_tenths_to_str(temp_tenths)
    hour, minute = settings["measurement_time"].split(":")

    outcome = submit_fn(
        email=email,
        password=password,
        temp_str=temp_str,
        hour=hour,
        minute=minute,
        pool_participation=bool(settings["pool_participation"]),
        screenshot_dir=screenshot_dir or (SCRIPT_DIR / "logs"),
        check_stop=check_stop,
    )

    finished_at = now_fn().isoformat()
    core.finish_run(
        conn, target_date, result=outcome.result, reason=outcome.reason,
        temp=temp_tenths, finished_at_iso=finished_at,
    )
    log.info(f"{target_date}: 結果={outcome.result} 理由={outcome.reason}")

    pool_label = "参加" if settings["pool_participation"] else "不参加"
    summary = f"体温 {temp_str}℃ / {hour}:{minute} / プール{pool_label}"

    if notify_fn is not None:
        want_mac = outcome.result in ("success", "failed", "unknown", "already_sent", "cancelled")
        want_discord = outcome.result in ("success", "failed", "unknown")
        if outcome.result == "success":
            mac_title, mac_msg = "✓ はいチーズ！送信完了", summary
            discord_msg = f"✅ はいチーズ！連絡帳 送信完了\n{summary}"
        elif outcome.result == "already_sent":
            mac_title, mac_msg = "はいチーズ！送信済み", "開いた時点で送信済みでした"
            discord_msg = ""
        elif outcome.result == "cancelled":
            mac_title, mac_msg = "はいチーズ！停止", outcome.reason
            discord_msg = ""
        else:
            mac_title, mac_msg = "はいチーズ！エラー", outcome.reason[:80]
            discord_msg = f"⚠️ はいチーズ！連絡帳 送信{('失敗' if outcome.result == 'failed' else '結果不明')}\n{outcome.reason[:200]}"

        notify_result = notify_fn(
            mac_title, mac_msg, discord_msg,
            mac=want_mac, discord=want_discord,
        )
        core.set_notify_status(conn, target_date, notify_result.get("status", "unknown"))

    return {"attempted": True, "result": outcome.result, "reason": outcome.reason}


def main() -> int:
    from dotenv import load_dotenv
    load_dotenv(SCRIPT_DIR / ".env")

    import sender
    import notifier

    log = _setup_logger(SCRIPT_DIR / "logs")

    with _try_lock(core.lock_path()) as lock_file:
        if lock_file is None:
            log.info("他のrunnerが実行中のためスキップします")
            return 0

        conn = core.connect()
        try:
            email = os.getenv("HAICHEESE_EMAIL")
            password = os.getenv("HAICHEESE_PASSWORD")
            bot_token = os.getenv("DISCORD_BOT_TOKEN", "")
            dm_channel = os.getenv("DISCORD_DM_CHANNEL", "")

            def real_submit(**kwargs):
                return sender.submit(**kwargs)

            def real_notify(mac_title, mac_msg, discord_msg, *, mac=True, discord=True):
                return notifier.notify(
                    mac_title, mac_msg, discord_msg,
                    bot_token=bot_token, dm_channel=dm_channel, mac=mac, discord=discord,
                )

            if not email or not password or password == "your_password_here":
                log.info("ERROR: .env にメールアドレス・パスワードが未設定です")
                return 1

            result = run_once(
                conn,
                submit_fn=real_submit,
                notify_fn=real_notify,
                email=email,
                password=password,
                screenshot_dir=SCRIPT_DIR / "logs",
                logger=log,
            )
            log.info(f"tick完了: {result}")
            return 0
        finally:
            conn.close()


if __name__ == "__main__":
    sys.exit(main())
