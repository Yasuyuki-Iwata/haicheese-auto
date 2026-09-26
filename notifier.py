"""Mac通知・Discord DM通知。

Discordは共通配信部 discord_delivery（既定では ``~/.local/lib``、環境変数
HAICHEESE_DELIVERY_LIB で上書き可）の送信箱へ預ける。送れなかった通知は
共通配信部の15分巡回（kb-reconcile の drain）が送り直す。Bot トークンは
共通配信部が送信直前に token_file（このリポジトリの .env）から読むため、
このモジュールはトークンを読まない・保持しない。

共通配信部の import に失敗した場合は、直送へフォールバックせず失敗として
扱う（docs/design-discord-delivery.md 参照）。

runner.py・monitor.py からは notify_fn として差し替え可能な形で使う。
テストや手動確認では、ここを呼ばないダミー関数を渡して本番通知を防ぐ。
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
from pathlib import Path
from typing import Optional

SCRIPT_DIR = Path(__file__).resolve().parent
TOKEN_FILE = SCRIPT_DIR / ".env"
SOURCE = "haicheese"

_delivery_module_cache: dict = {}

_DISCORD_SUCCESS = {"sent", "suppressed", "skipped"}
_DISCORD_FAILURE = {"dead", "error", "not_configured"}


def notify_mac(title: str, message: str) -> None:
    script = f'display notification "{message}" with title "{title}"'
    subprocess.run(["osascript", "-e", script], check=False)


def _delivery_module():
    """共通配信部 discord_delivery を import して返す。

    HAICHEESE_DELIVERY_LIB が設定されていればそこ、無ければ ~/.local/lib
    から探す。import に失敗した場合は例外がそのまま呼び出し元へ伝わる
    （直送へのフォールバックはしない）。
    """
    lib_dir = os.environ.get("HAICHEESE_DELIVERY_LIB") or str(Path.home() / ".local/lib")
    cached = _delivery_module_cache.get(lib_dir)
    if cached is not None:
        return cached

    module_path = Path(lib_dir) / "discord_delivery.py"
    spec = importlib.util.spec_from_file_location("discord_delivery", module_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"discord_delivery module not found: {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _delivery_module_cache[lib_dir] = module
    return module


def _bot_destination(dm_channel: str) -> dict:
    return {
        "kind": "bot",
        "channel_id": dm_channel,
        "token_file": str(TOKEN_FILE),
        "token_key": "DISCORD_BOT_TOKEN",
    }


def notify_discord_result(dm_channel: str, message: str, *, event_key: Optional[str] = None,
                           ttl_seconds: float = 86400) -> dict:
    """共通配信部へ預け、結果の dict（status・event_id など）をそのまま返す。

    dm_channel か message が空なら送信箱へ登録せず {"status": "not_configured"}。
    共通部の読み込み失敗・submit の例外は {"status": "error", "reason": ...}
    （呼び出し元へ例外を投げない）。
    """
    if not dm_channel or not message:
        return {"status": "not_configured"}

    try:
        delivery = _delivery_module()
    except Exception as e:
        return {"status": "error", "reason": str(e)}

    try:
        return delivery.submit(
            source=SOURCE,
            destination=_bot_destination(dm_channel),
            payload={"content": message},
            event_key=event_key,
            ttl_seconds=ttl_seconds,
        )
    except Exception as e:
        return {"status": "error", "reason": str(e)}


def notify_discord(bot_token: str, dm_channel: str, message: str) -> bool:
    """互換API。bot_token は受け取るだけで使わない。
    預けられた（sent・queued・suppressed）ならTrue。"""
    result = notify_discord_result(dm_channel, message)
    return result.get("status") in ("sent", "queued", "suppressed")


def notify(mac_title: str, mac_message: str, discord_message: str, *,
           bot_token: str = "", dm_channel: str, mac: bool = True, discord: bool = True,
           event_key: Optional[str] = None) -> dict:
    """Mac通知・Discord通知をまとめて行う。戻り値は notify_status に保存する要約。

    discord_status を必ず含む（skipped・sent・queued・suppressed・dead・error・
    not_configured のいずれか）。status は ok・queued・partial・failed のいずれか。
    """
    mac_ok = True
    if mac:
        try:
            notify_mac(mac_title, mac_message)
        except Exception:
            mac_ok = False

    if discord:
        result = notify_discord_result(dm_channel, discord_message, event_key=event_key)
        discord_status = result.get("status", "error")
    else:
        discord_status = "skipped"

    mac_ok_effective = (not mac) or mac_ok
    discord_success = discord_status in _DISCORD_SUCCESS
    discord_queued = discord_status == "queued"
    mac_failed = mac and not mac_ok
    discord_failed = discord and discord_status in _DISCORD_FAILURE

    if mac_ok_effective and discord_success:
        status = "ok"
    elif mac_ok_effective and discord_queued:
        status = "queued"
    elif mac and discord:
        status = "failed" if (mac_failed and discord_failed) else "partial"
    elif mac and not discord:
        # discordを求めていない場合、要求した唯一の手段（mac）が失敗している
        status = "failed"
    elif discord and not mac:
        # macを求めていない場合、要求した唯一の手段（discord）が失敗している
        status = "failed"
    else:
        # どちらも求めていない（呼び出し側の誤用だが、失敗はしていないとみなす）
        status = "ok"

    out = {"status": status, "discord_status": discord_status}
    if status == "partial":
        out["mac_ok"] = mac_ok
        out["discord_ok"] = discord_success
    return out
