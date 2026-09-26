#!/usr/bin/env python3
"""未送信・異常の監視。launchdが5分おきに起動する想定。

送信処理（runner.py）とは別プロセスで、固定時刻とログ有無だけに頼っていた
旧healthcheck.shを置き換える。判定はcore.pyの関数を使い、同じ予定について
何度も通知しないようalertsテーブルで一度きりに絞る。
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import timedelta
from pathlib import Path
from typing import Callable

import core

SCRIPT_DIR = Path(__file__).resolve().parent


def _setup_logger(log_dir: Path) -> logging.Logger:
    log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("haicheese.monitor")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        h = logging.FileHandler(log_dir / "monitor.log", encoding="utf-8")
        h.setFormatter(logging.Formatter("%(asctime)s  %(message)s"))
        logger.addHandler(h)
    return logger


def check_once(
    conn,
    *,
    now_fn: Callable = core.default_now,
    notify_fn: Callable = None,
    is_holiday: Callable = core.default_is_holiday,
    load_skip_dates: Callable = core.default_load_skip_dates,
    logger=None,
) -> dict:
    """1回分の監視チェック。戻り値は発報したアラート・再試行待ちの一覧（テスト・手動確認用）。

    通知を預けたあとで record_alert_once を呼ぶ（記録してから通知すると、Discordへの
    配信が失敗した警告が二度と出せなくなるため）。共通配信部の読み込み失敗など
    discord_status が "error" のときは記録しない。次回の監視でもう一度通知を試みる。
    event_key を付けて呼ぶので、既に預けてある分は共通配信部の側で suppressed になり
    二重には届かない。
    """
    log = logger or logging.getLogger("haicheese.monitor")
    now = now_fn()
    settings = core.get_settings(conn)
    skip_dates = load_skip_dates()
    fired = []
    retry = []

    def handle(target: str, kind: str, msg: str) -> None:
        if core.has_alert(conn, target, kind):
            return
        event_key = f"haicheese:alert:{target}:{kind}"
        result = None
        if notify_fn:
            result = notify_fn("はいチーズ！要確認", msg, msg, mac=True, discord=True, event_key=event_key)
        discord_status = (result or {}).get("discord_status")
        if discord_status == "error":
            log.error(f"{target}: {kind} の通知が共通配信部のエラーで送れませんでした。次回の監視で再試行します。")
            retry.append({"target_date": target, "kind": kind})
            return
        if discord_status in ("dead", "not_configured"):
            log.error(f"{target}: {kind} を通知しましたが、Discordへの配信は失敗しました（{discord_status}）。再送はされません。")
        if core.record_alert_once(conn, target, kind, now.isoformat()):
            log.info(f"{target}: {kind} を通知")
            fired.append({"target_date": target, "kind": kind})

    # 1. 最終tickの停滞（有効なときだけ。停止中はtickの停止を要確認にしない）
    if settings.get("enabled"):
        last_tick = core.get_last_tick(conn)
        stale = last_tick is None or (now - last_tick) > timedelta(minutes=core.TICK_STALE_MINUTES)
        if stale:
            target = now.date().isoformat()
            msg = "⚠️ はいチーズ！ runner の定期実行が止まっているようです。Macの起動状態・launchdを確認してください。"
            handle(target, "tick_stale", msg)

    # 2. 未送信・失敗・結果不明（有効かつ今日が対象日のときだけ）
    today = now.date()
    if settings.get("enabled") and core.is_target_date(today, settings, is_holiday, skip_dates):
        scheduled_dt = core.combine_datetime(today, settings["submit_time"])
        if now >= scheduled_dt + timedelta(minutes=core.ALERT_AFTER_MINUTES):
            run = core.get_run(conn, today.isoformat())
            eff = core.effective_result(run, now)
            if eff not in ("success", "already_sent"):
                if eff is None:
                    kind, label = "missing", "予定時刻を過ぎても実行された形跡がありません"
                elif eff == "failed":
                    kind, label = "failed", f"送信に失敗しました（{run.get('reason', '')}）"
                elif eff == "unknown":
                    kind, label = "unknown", f"送信結果が確認できません（{run.get('reason', '')}）"
                else:
                    kind, label = None, None

                if kind:
                    msg = f"⚠️ はいチーズ！ {today.isoformat()}の連絡帳: {label}"
                    handle(today.isoformat(), kind, msg)

    return {"fired": fired, "retry": retry}


def main() -> int:
    from dotenv import load_dotenv
    load_dotenv(SCRIPT_DIR / ".env")

    import notifier

    log = _setup_logger(SCRIPT_DIR / "logs")
    conn = core.connect()
    try:
        dm_channel = os.getenv("DISCORD_DM_CHANNEL", "")

        def real_notify(mac_title, mac_msg, discord_msg, *, mac=True, discord=True, event_key=None):
            return notifier.notify(
                mac_title, mac_msg, discord_msg,
                dm_channel=dm_channel, mac=mac, discord=discord, event_key=event_key,
            )

        result = check_once(conn, notify_fn=real_notify, logger=log)
        log.info(f"チェック完了: {result}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
