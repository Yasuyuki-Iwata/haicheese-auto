"""notifier.py のDiscordアダプター部分（共通配信部への橋渡し）のテスト。

notify_discord_result・notify_discord の互換性、送信箱に登録するdestinationが
token_fileの参照だけでトークンの値を含まないこと、共通配信部の読み込み失敗時の
扱いを確認する。

conftest.py の autouse fixture により、NO_DISCORD=1・DISCORD_DELIVERY_HOME・
DISCORD_DELIVERY_USER_HOME は既定でテスト用の一時ディレクトリに隔離されている。
実際のHTTP送信を試すテストだけ NO_DISCORD を外し、共通配信部のHTTP層
（_http_request）を偽物に差し替える。本物のDiscord APIには一度もアクセスしない。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import notifier

# 偽の.envに入れる、本物ではないと一目で分かるトークン値。
# 送信箱・戻り値・ログのどこにも出ないことを確認する。
FAKE_TOKEN = "FAKE-TOKEN-e6f1c2a9-not-a-real-secret"


def _write_fake_env(path: Path, token: str = FAKE_TOKEN) -> None:
    path.write_text(f"DISCORD_BOT_TOKEN={token}\n", encoding="utf-8")


# ── notify_discord_result: 未設定・登録スキップ ──────────────────────

def test_notify_discord_result_empty_channel_is_not_configured():
    assert notifier.notify_discord_result("", "message") == {"status": "not_configured"}


def test_notify_discord_result_empty_message_is_not_configured():
    assert notifier.notify_discord_result("channel-id", "") == {"status": "not_configured"}


def test_notify_discord_result_does_not_register_when_not_configured(monkeypatch):
    """dm_channel・messageが空のときは送信箱へ登録しない（共通部のsubmitを呼ばない）。"""
    called = {"submit": False}

    class FakeDelivery:
        @staticmethod
        def submit(**kwargs):
            called["submit"] = True
            return {"status": "queued"}

    monkeypatch.setattr(notifier, "_delivery_module", lambda: FakeDelivery)
    notifier.notify_discord_result("", "message")
    notifier.notify_discord_result("channel", "")
    assert called["submit"] is False


# ── 共通部の読み込み失敗・submit例外 ────────────────────────────────

def test_notify_discord_result_module_load_failure_is_error(monkeypatch):
    def boom():
        raise ImportError("no module")

    monkeypatch.setattr(notifier, "_delivery_module", boom)
    result = notifier.notify_discord_result("channel", "message")
    assert result["status"] == "error"
    assert "no module" in result["reason"]


def test_notify_discord_result_submit_exception_is_error(monkeypatch):
    class FakeDelivery:
        @staticmethod
        def submit(**kwargs):
            raise RuntimeError("boom")

    monkeypatch.setattr(notifier, "_delivery_module", lambda: FakeDelivery)
    result = notifier.notify_discord_result("channel", "message")
    assert result == {"status": "error", "reason": "boom"}


def test_module_load_failure_does_not_fall_back_to_direct_http(monkeypatch, tmp_path):
    """共通部の読み込みに失敗しても、urllibで直接送信しない（直送フォールバック禁止）。"""
    import urllib.request

    def fail_open(*a, **k):
        raise AssertionError("urllib.request.urlopen が直接呼ばれました")

    monkeypatch.setattr(urllib.request, "urlopen", fail_open)
    monkeypatch.setenv("HAICHEESE_DELIVERY_LIB", str(tmp_path / "does-not-exist"))
    result = notifier.notify_discord_result("channel", "message")
    assert result["status"] == "error"


# ── 呼び出し内容・destinationの検証 ──────────────────────────────────

def test_notify_discord_result_passes_event_key_and_message(monkeypatch):
    captured = {}

    class FakeDelivery:
        @staticmethod
        def submit(**kwargs):
            captured.update(kwargs)
            return {"status": "sent", "event_id": "abc"}

    monkeypatch.setattr(notifier, "_delivery_module", lambda: FakeDelivery)
    result = notifier.notify_discord_result("channel-id", "hello", event_key="k1", ttl_seconds=100)

    assert result == {"status": "sent", "event_id": "abc"}
    assert captured["source"] == "haicheese"
    assert captured["payload"] == {"content": "hello"}
    assert captured["event_key"] == "k1"
    assert captured["ttl_seconds"] == 100

    destination = captured["destination"]
    assert destination["kind"] == "bot"
    assert destination["channel_id"] == "channel-id"
    assert destination["token_key"] == "DISCORD_BOT_TOKEN"
    assert destination["token_file"] == str(notifier.TOKEN_FILE)
    # destinationはkind・channel_id・token_file・token_keyだけを持ち、
    # トークンの値そのものを持つキーは無い（値は共通配信部が送信直前にtoken_fileから読む）。
    assert set(destination.keys()) == {"kind", "channel_id", "token_file", "token_key"}


def test_notify_discord_result_uses_repo_env_as_token_file():
    """destinationのtoken_fileはリポジトリの.env（notifier.pyと同じディレクトリ）を指す。
    実際に読みに行くのは共通配信部が送信直前だけで、ここでは参照先だけを確認する。"""
    assert notifier.TOKEN_FILE == notifier.SCRIPT_DIR / ".env"


# ── notify_discord: bool互換 ───────────────────────────────────────

@pytest.mark.parametrize("status,expected", [
    ("sent", True), ("queued", True), ("suppressed", True),
    ("dead", False), ("error", False), ("not_configured", False),
])
def test_notify_discord_bool_compat(monkeypatch, status, expected):
    monkeypatch.setattr(notifier, "notify_discord_result", lambda *a, **k: {"status": status})
    assert notifier.notify_discord("unused-token", "channel", "message") is expected


# ── 本物の共通配信部を使い、HTTPだけ偽物に差し替える ───────────────────

def test_real_delivery_queues_on_network_failure_then_drain_sends(monkeypatch, tmp_path):
    """通信断でqueued、drainでsent（届いた本文が一致）。偽.envの偽トークンは
    戻り値・送信箱のdestination/payloadのどこにも出ない。"""
    monkeypatch.delenv("NO_DISCORD", raising=False)  # このテストだけ実際の送信フローを試す

    fake_env = tmp_path / "fake.env"
    _write_fake_env(fake_env)
    monkeypatch.setattr(notifier, "TOKEN_FILE", fake_env)

    delivery = notifier._delivery_module()

    calls = []

    def fake_http_request_fail(url, data, headers):
        calls.append({"url": url, "data": data})
        raise delivery._TransientTransportError()

    monkeypatch.setattr(delivery, "_http_request", fake_http_request_fail)

    result = notifier.notify_discord_result("111222333", "hello world", event_key="adapter-test:once")
    assert result["status"] == "queued"
    assert FAKE_TOKEN not in json.dumps(result)
    assert len(calls) == 1

    # 送信箱（sqlite）にトークンの値が保存されていないことを確認する（中身は最小限だけ読む）。
    import sqlite3

    outbox_db = delivery._outbox_dir() / "outbox.sqlite3"
    conn = sqlite3.connect(str(outbox_db))
    try:
        rows = conn.execute("SELECT destination, payload FROM events").fetchall()
    finally:
        conn.close()
    assert rows, "送信箱にイベントが登録されていません"
    for destination_json, payload_json in rows:
        assert FAKE_TOKEN not in destination_json
        assert FAKE_TOKEN not in payload_json

    # 通信が復旧したことにして drain すると送信される。再試行の待ち時間（60秒）が
    # 経つまでは due にならないため、_now を進めて再試行時刻を過ぎたことにする。
    import time as _time

    real_now = _time.time()
    monkeypatch.setattr(delivery, "_now", lambda: real_now + 61)

    def fake_http_request_success(url, data, headers):
        calls.append({"url": url, "data": data})
        body = json.dumps({"id": "999", "channel_id": "111222333"}).encode("utf-8")
        return 200, {}, body

    monkeypatch.setattr(delivery, "_http_request", fake_http_request_success)
    drained = delivery.drain(limit=10)
    assert any(r.get("status") == "sent" for r in drained)

    sent_payload = json.loads(calls[-1]["data"].decode("utf-8"))
    assert sent_payload["content"] == "hello world"
    assert FAKE_TOKEN not in calls[-1]["data"].decode("utf-8")
