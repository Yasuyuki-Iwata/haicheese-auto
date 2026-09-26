"""notifier.py: notify()の集計ロジック。

notify_mac/notify_discord_result はここでは一切呼ばない（osascript・共通配信部へ
アクセスしないように、常にモンキーパッチで差し替える）。共通配信部そのものの
アダプター挙動（notify_discord_result・token非露出など）は
tests/test_discord_delivery_adapter.py で確認する。
"""

from __future__ import annotations

import notifier


def test_notify_all_ok(monkeypatch):
    monkeypatch.setattr(notifier, "notify_mac", lambda *a, **k: None)
    monkeypatch.setattr(notifier, "notify_discord_result", lambda *a, **k: {"status": "sent"})
    result = notifier.notify(
        "title", "mac message", "discord message",
        dm_channel="dummy", mac=True, discord=True,
    )
    assert result == {"status": "ok", "discord_status": "sent"}


def test_notify_discord_queued_is_queued_not_ok(monkeypatch):
    monkeypatch.setattr(notifier, "notify_mac", lambda *a, **k: None)
    monkeypatch.setattr(notifier, "notify_discord_result", lambda *a, **k: {"status": "queued"})
    result = notifier.notify(
        "title", "mac message", "discord message",
        dm_channel="dummy", mac=True, discord=True,
    )
    assert result == {"status": "queued", "discord_status": "queued"}


def test_notify_discord_failure_is_partial(monkeypatch):
    monkeypatch.setattr(notifier, "notify_mac", lambda *a, **k: None)
    monkeypatch.setattr(notifier, "notify_discord_result", lambda *a, **k: {"status": "dead"})
    result = notifier.notify(
        "title", "mac message", "discord message",
        dm_channel="dummy", mac=True, discord=True,
    )
    assert result["status"] == "partial"
    assert result["discord_status"] == "dead"
    assert result["mac_ok"] is True
    assert result["discord_ok"] is False


def test_notify_mac_exception_and_discord_failure_is_failed(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("simulated osascript failure")
    monkeypatch.setattr(notifier, "notify_mac", boom)
    monkeypatch.setattr(notifier, "notify_discord_result", lambda *a, **k: {"status": "dead"})
    result = notifier.notify(
        "title", "mac message", "discord message",
        dm_channel="dummy", mac=True, discord=True,
    )
    assert result == {"status": "failed", "discord_status": "dead"}


def test_notify_discord_not_requested_skips_call(monkeypatch):
    called = {"discord": False}

    def fake_discord_result(*a, **k):
        called["discord"] = True
        return {"status": "sent"}

    monkeypatch.setattr(notifier, "notify_mac", lambda *a, **k: None)
    monkeypatch.setattr(notifier, "notify_discord_result", fake_discord_result)
    result = notifier.notify(
        "title", "mac message", "discord message",
        dm_channel="dummy", mac=True, discord=False,
    )
    assert called["discord"] is False
    assert result == {"status": "ok", "discord_status": "skipped"}


def test_notify_discord_missing_credentials_returns_false():
    """本物の共通配信部・Discord APIへアクセスせず、資格情報が無ければFalseを返すことだけを確認する。"""
    assert notifier.notify_discord("", "", "message") is False
    assert notifier.notify_discord(None, None, "message") is False


def test_notify_discord_bool_compat_true_cases(monkeypatch):
    for status in ("sent", "queued", "suppressed"):
        monkeypatch.setattr(notifier, "notify_discord_result", lambda *a, **k: {"status": status})
        assert notifier.notify_discord("unused", "channel", "message") is True


def test_notify_discord_bool_compat_false_cases(monkeypatch):
    for status in ("dead", "error", "not_configured"):
        monkeypatch.setattr(notifier, "notify_discord_result", lambda *a, **k: {"status": status})
        assert notifier.notify_discord("unused", "channel", "message") is False
