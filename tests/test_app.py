"""app.py: Web API まわりの受け入れ条件テスト。

Flaskのtest_clientのみを使い、実サーバは起動しない（実サーバでのレイアウト確認は
test_ui_responsive.py側）。本番サイト・Discordへは一切アクセスしない。
"""

from __future__ import annotations

import hashlib
import hmac
import sqlite3

import pytest

import app as app_module
import core


TOKEN = app_module.WEB_TOKEN


@pytest.fixture
def client():
    app_module.app.testing = False
    with app_module.app.test_client() as c:
        yield c


def csrf_header():
    token = hmac.new(TOKEN.encode("utf-8"), b"csrf", hashlib.sha256).hexdigest()
    return {"X-CSRF-Token": token}


def auth_cookie_client(client):
    client.set_cookie("haicheese_web_token", TOKEN, domain="localhost")
    return client


# ── 認証 ─────────────────────────────────────────────────────

def test_index_without_token_is_401(client):
    res = client.get("/")
    assert res.status_code == 401


def test_api_status_without_token_is_401(client):
    res = client.get("/api/status")
    assert res.status_code == 401


def test_token_query_sets_httponly_cookie_and_redirects(client):
    res = client.get(f"/?token={TOKEN}")
    assert res.status_code == 302
    set_cookie = res.headers.get("Set-Cookie", "")
    assert "haicheese_web_token" in set_cookie
    assert "HttpOnly" in set_cookie
    assert "SameSite=Strict" in set_cookie


def test_cookie_after_token_redirect_grants_access(client):
    client.get(f"/?token={TOKEN}")  # クッキーがtest_clientのjarに保存される
    res = client.get("/api/status")
    assert res.status_code == 200


def test_wrong_token_is_401(client):
    res = client.get("/api/status", headers={"X-Auth-Token": "wrong-token"})
    assert res.status_code == 401


# ── CSRF / Origin ────────────────────────────────────────────

def test_put_settings_without_csrf_header_is_403(client):
    auth_cookie_client(client)
    res = client.put("/api/settings", json={"revision": 1})
    assert res.status_code == 403


def test_put_settings_with_mismatched_origin_is_403(client):
    auth_cookie_client(client)
    headers = csrf_header()
    headers["Origin"] = "https://evil.example.com"
    res = client.put("/api/settings", json={"revision": 1}, headers=headers)
    assert res.status_code == 403


def test_post_pause_without_csrf_is_403(client):
    auth_cookie_client(client)
    res = client.post("/api/pause", json={})
    assert res.status_code == 403


def test_put_settings_non_json_content_type_is_rejected(client):
    auth_cookie_client(client)
    headers = csrf_header()
    res = client.put(
        "/api/settings", data="not-json", headers=headers,
        content_type="text/plain",
    )
    assert res.status_code == 415


# ── revision不一致 ────────────────────────────────────────────

def test_put_settings_revision_conflict_returns_409(client):
    auth_cookie_client(client)
    headers = csrf_header()
    payload = {
        "enabled": False, "start_date": None, "end_date": None,
        "submit_time": "07:00", "measurement_time": "06:50",
        "temp_min": "36.5", "temp_max": "36.8", "pool_participation": True,
        "revision": 9999,
    }
    res = client.put("/api/settings", json=payload, headers=headers)
    assert res.status_code == 409
    body = res.get_json()
    assert body["error"] == "revision_conflict"


def test_put_settings_success_updates_and_returns_status(client):
    auth_cookie_client(client)
    headers = csrf_header()
    payload = {
        "enabled": True, "start_date": "2026-10-01", "end_date": "2026-11-30",
        "submit_time": "08:15", "measurement_time": "06:55",
        "temp_min": "36.6", "temp_max": "36.9", "pool_participation": False,
        "revision": 1,
    }
    res = client.put("/api/settings", json=payload, headers=headers)
    assert res.status_code == 200
    body = res.get_json()
    assert body["settings"]["submit_time"] == "08:15"
    assert body["settings"]["revision"] == 2


def test_put_settings_invalid_payload_returns_errors(client):
    auth_cookie_client(client)
    headers = csrf_header()
    payload = {
        "enabled": True, "start_date": "2026-12-01", "end_date": "2026-01-01",
        "submit_time": "07:00", "measurement_time": "06:50",
        "temp_min": "36.5", "temp_max": "36.8", "pool_participation": True,
        "revision": 1,
    }
    res = client.put("/api/settings", json=payload, headers=headers)
    assert res.status_code == 400
    assert res.get_json()["errors"]


# ── pause は他項目を変えない ────────────────────────────────

def test_pause_only_disables_and_keeps_other_fields(client):
    auth_cookie_client(client)
    headers = csrf_header()
    payload = {
        "enabled": True, "start_date": "2026-10-01", "end_date": "2026-11-30",
        "submit_time": "08:15", "measurement_time": "06:55",
        "temp_min": "36.6", "temp_max": "36.9", "pool_participation": False,
        "revision": 1,
    }
    client.put("/api/settings", json=payload, headers=headers)

    res = client.post("/api/pause", json={}, headers=headers)
    assert res.status_code == 200
    body = res.get_json()
    s = body["settings"]
    assert s["enabled"] is False
    assert s["start_date"] == "2026-10-01"
    assert s["end_date"] == "2026-11-30"
    assert s["submit_time"] == "08:15"
    assert s["measurement_time"] == "06:55"
    assert s["temp_min"] == "36.6"
    assert s["temp_max"] == "36.9"
    assert s["pool_participation"] is False


# ── レスポンスに資格情報を含まない ────────────────────────

def test_status_response_does_not_leak_credentials(client):
    auth_cookie_client(client)
    res = client.get("/api/status")
    text = res.get_data(as_text=True)
    for secret in ("test-dummy-email@example.invalid", "test-dummy-password",
                   "test-dummy-discord-bot-token", "test-dummy-token-0123456789"):
        assert secret not in text


# ── DB読取失敗時にエラーを返す ───────────────────────────

def test_status_returns_error_when_db_read_fails(client, monkeypatch):
    auth_cookie_client(client)

    def boom(conn):
        raise sqlite3.OperationalError("simulated DB failure")

    monkeypatch.setattr(app_module.core, "get_settings", boom)
    res = client.get("/api/status")
    assert res.status_code >= 500


def test_settings_save_failure_keeps_previous_settings_and_reports_error(client, monkeypatch):
    auth_cookie_client(client)
    headers = csrf_header()

    def boom(conn, cleaned, expected_revision, now_iso):
        raise sqlite3.OperationalError("simulated write failure")

    monkeypatch.setattr(app_module.core, "save_settings", boom)
    payload = {
        "enabled": True, "start_date": None, "end_date": None,
        "submit_time": "09:00", "measurement_time": "06:50",
        "temp_min": "36.5", "temp_max": "36.8", "pool_participation": True,
        "revision": 1,
    }
    res = client.put("/api/settings", json=payload, headers=headers)
    assert res.status_code == 500
    assert "失敗" in res.get_json()["error"]

    # 実際の設定は変わっていないこと（別クライアントで確認）
    with app_module.app.test_client() as c2:
        auth_cookie_client(c2)
        status = c2.get("/api/status").get_json()
        assert status["settings"]["enabled"] is False
        assert status["settings"]["submit_time"] == "07:00"


# ── 8. skip_dates編集UIを追加していないこと ──────────────

def test_index_page_has_no_skip_date_editor(client):
    res = client.get(f"/?token={TOKEN}")
    client.get(f"/?token={TOKEN}")
    res = client.get("/")
    html = res.get_data(as_text=True)
    assert "skip_dates" not in html.lower()
