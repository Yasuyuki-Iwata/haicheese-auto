"""Mac通知・Discord DM通知。

runner.py・monitor.py からは notify_fn として差し替え可能な形で使う。
テストや手動確認では、ここを呼ばないダミー関数を渡して本番通知を防ぐ。
"""

from __future__ import annotations

import json
import subprocess
import urllib.request


def notify_mac(title: str, message: str) -> None:
    script = f'display notification "{message}" with title "{title}"'
    subprocess.run(["osascript", "-e", script], check=False)


def notify_discord(bot_token: str, dm_channel: str, message: str) -> bool:
    """Discord DMで通知する。成功したらTrue。"""
    if not bot_token or not dm_channel:
        return False
    api = "https://discord.com/api/v10"
    headers = {
        "Authorization": f"Bot {bot_token}",
        "Content-Type": "application/json",
        "User-Agent": "DiscordBot (haicheese, 1.0)",
    }
    try:
        req = urllib.request.Request(
            f"{api}/channels/{dm_channel}/messages",
            data=json.dumps({"content": message}).encode(),
            headers=headers,
            method="POST",
        )
        urllib.request.urlopen(req, timeout=10)
        return True
    except Exception:
        return False


def notify(mac_title: str, mac_message: str, discord_message: str, *,
           bot_token: str, dm_channel: str, mac: bool = True, discord: bool = True) -> dict:
    """Mac通知・Discord通知をまとめて行う。戻り値は notify_status に保存する要約。"""
    mac_ok = True
    discord_ok = True
    if mac:
        try:
            notify_mac(mac_title, mac_message)
        except Exception:
            mac_ok = False
    if discord:
        discord_ok = notify_discord(bot_token, dm_channel, discord_message)
    if mac_ok and discord_ok:
        return {"status": "ok"}
    if not mac_ok and not discord_ok:
        return {"status": "failed"}
    return {"status": "partial", "mac_ok": mac_ok, "discord_ok": discord_ok}
