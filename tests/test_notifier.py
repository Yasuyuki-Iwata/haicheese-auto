"""notifier.py: notify()の集計ロジック。

notify_mac/notify_discord はここでは一切呼ばない（osascript・Discord APIへ
アクセスしないように、常にモンキーパッチで差し替える）。
"""

from __future__ import annotations

import notifier


def test_notify_all_ok(monkeypatch):
    monkeypatch.setattr(notifier, "notify_mac", lambda *a, **k: None)
    monkeypatch.setattr(notifier, "notify_discord", lambda *a, **k: True)
    result = notifier.notify(
        "title", "mac message", "discord message",
        bot_token="dummy", dm_channel="dummy", mac=True, discord=True,
    )
    assert result == {"status": "ok"}


def test_notify_discord_failure_is_partial(monkeypatch):
    monkeypatch.setattr(notifier, "notify_mac", lambda *a, **k: None)
    monkeypatch.setattr(notifier, "notify_discord", lambda *a, **k: False)
    result = notifier.notify(
        "title", "mac message", "discord message",
        bot_token="dummy", dm_channel="dummy", mac=True, discord=True,
    )
    assert result["status"] == "partial"
    assert result["mac_ok"] is True
    assert result["discord_ok"] is False


def test_notify_mac_exception_and_discord_failure_is_failed(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("simulated osascript failure")
    monkeypatch.setattr(notifier, "notify_mac", boom)
    monkeypatch.setattr(notifier, "notify_discord", lambda *a, **k: False)
    result = notifier.notify(
        "title", "mac message", "discord message",
        bot_token="dummy", dm_channel="dummy", mac=True, discord=True,
    )
    assert result == {"status": "failed"}


def test_notify_discord_not_requested_skips_call(monkeypatch):
    called = {"discord": False}

    def fake_discord(*a, **k):
        called["discord"] = True
        return True

    monkeypatch.setattr(notifier, "notify_mac", lambda *a, **k: None)
    monkeypatch.setattr(notifier, "notify_discord", fake_discord)
    result = notifier.notify(
        "title", "mac message", "discord message",
        bot_token="dummy", dm_channel="dummy", mac=True, discord=False,
    )
    assert called["discord"] is False
    assert result == {"status": "ok"}


def test_notify_discord_missing_credentials_returns_false():
    """本物のDiscord APIへアクセスせず、資格情報が無ければFalseを返すことだけを確認する。"""
    assert notifier.notify_discord("", "", "message") is False
    assert notifier.notify_discord(None, None, "message") is False
