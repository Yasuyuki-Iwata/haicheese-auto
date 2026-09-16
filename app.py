"""はいチーズ！ノート自動送信 設定画面（Flask）。

状態判定・次回予定はcore.pyの関数をそのまま使い、UIの表示とrunner/monitorの
判定がずれないようにする。認証はTailscale限定運用を前提に共有トークン＋
Cookieで行い、変更系APIにはCSRFトークンとJSON限定を課す。
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import re
import secrets
import sys
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv
from flask import Flask, g, jsonify, redirect, render_template, request, Response

import core

SCRIPT_DIR = Path(__file__).resolve().parent
load_dotenv(SCRIPT_DIR / ".env")

WEB_TOKEN = os.getenv("HAICHEESE_WEB_TOKEN", "")
COOKIE_NAME = "haicheese_web_token"
CSRF_HEADER = "X-CSRF-Token"

app = Flask(__name__)


# ── ログのトークン伏せ字（近鉄アプリと同じ考え方） ──────────────────
# werkzeugのアクセスログはクエリ文字列ごと書くため、?token=の値が平文で残る。

_LOG_QUERY_RE = re.compile(r"\?[^\s\x1b]*")


def _redact_log_text(text: str) -> str:
    return _LOG_QUERY_RE.sub("?[REDACTED]", text)


class _RedactQueryFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:
            return True
        redacted = _redact_log_text(message)
        if redacted != message:
            record.msg, record.args = redacted, ()
        return True


logging.getLogger("werkzeug").addFilter(_RedactQueryFilter())


# ── DB接続（リクエストごと） ──────────────────────────────────

def get_db():
    if "db" not in g:
        g.db = core.connect()
    return g.db


@app.teardown_appcontext
def _close_db(exception=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


# ── 認証・CSRF ────────────────────────────────────────────────

def csrf_token() -> str:
    return hmac.new(WEB_TOKEN.encode("utf-8"), b"csrf", hashlib.sha256).hexdigest()


@app.before_request
def _require_auth():
    if not WEB_TOKEN:
        return Response("HAICHEESE_WEB_TOKEN が未設定です\n", status=503, mimetype="text/plain")

    url_token = request.args.get("token")
    provided = (
        request.headers.get("X-Auth-Token")
        or url_token
        or request.cookies.get(COOKIE_NAME, "")
    )
    if not provided or not secrets.compare_digest(
        str(provided).encode("utf-8"), WEB_TOKEN.encode("utf-8")
    ):
        return Response("認証が必要です\n", status=401, mimetype="text/plain")

    if url_token:
        resp = redirect(request.path or "/")
        resp.set_cookie(
            COOKIE_NAME, WEB_TOKEN, max_age=60 * 60 * 24 * 365,
            httponly=True, samesite="Strict",
        )
        return resp
    return None


@app.before_request
def _csrf_guard():
    if request.method not in ("POST", "PUT", "PATCH", "DELETE"):
        return None
    if not request.path.startswith("/api/"):
        return None

    origin = request.headers.get("Origin")
    if origin:
        origin_host = urlparse(origin).netloc
        if origin_host != request.host:
            return jsonify({"error": "不正なOriginです"}), 403

    provided = request.headers.get(CSRF_HEADER, "")
    if not provided or not secrets.compare_digest(provided, csrf_token()):
        return jsonify({"error": "CSRFトークンが不正です"}), 403

    if not request.is_json:
        return jsonify({"error": "JSONで送ってください"}), 415
    return None


# ── 表示用の変換 ──────────────────────────────────────────────

def settings_for_display(settings: dict) -> dict:
    return {
        "enabled": bool(settings["enabled"]),
        "start_date": settings["start_date"],
        "end_date": settings["end_date"],
        "submit_time": settings["submit_time"],
        "measurement_time": settings["measurement_time"],
        "temp_min": core.temp_tenths_to_str(settings["temp_min"]),
        "temp_max": core.temp_tenths_to_str(settings["temp_max"]),
        "pool_participation": bool(settings["pool_participation"]),
        "updated_at": settings["updated_at"],
        "revision": settings["revision"],
    }


def run_for_display(run: dict) -> dict:
    return {
        "target_date": run["target_date"],
        "scheduled_time": run["scheduled_time"],
        "started_at": run["started_at"],
        "finished_at": run["finished_at"],
        "result": run["result"],
        "result_label": core.RESULT_LABELS.get(run["result"], run["result"]),
        "reason": run["reason"],
        "temp": core.temp_tenths_to_str(run["temp"]) if run["temp"] is not None else None,
        "notify_status": run["notify_status"],
    }


def build_status(conn, now) -> dict:
    settings = core.get_settings(conn)
    skip_dates = core.default_load_skip_dates()
    is_holiday = core.default_is_holiday

    operational = core.compute_operational_status(settings, now.date())
    health = core.compute_health_status(conn, now, is_holiday, skip_dates)

    def has_run(d: str) -> bool:
        return core.get_run(conn, d) is not None

    next_run = core.compute_next_run(settings, now, is_holiday, skip_dates, has_run)
    recent = core.get_recent_runs(conn, limit=5)
    running_now = any(r["result"] == "running" for r in recent)

    return {
        "settings": settings_for_display(settings),
        "operational_status": operational,
        "operational_label": core.OPERATIONAL_LABELS[operational],
        "health_status": health,
        "health_label": core.HEALTH_LABELS[health],
        "next_run": {
            "date": next_run["date"],
            "reason": next_run["reason"],
            "reason_label": (
                core.NEXT_RUN_REASON_LABELS.get(next_run["reason"])
                if next_run["reason"] else None
            ),
        },
        "recent_runs": [run_for_display(r) for r in recent],
        "running_now": running_now,
        "revision": settings["revision"],
        "server_time": now.isoformat(),
    }


def build_preview_summary(cleaned: dict, operational: str, next_run: dict) -> str:
    if not cleaned["enabled"]:
        return "自動送信は無効のまま保存されます。"
    label = core.OPERATIONAL_LABELS[operational]
    if next_run["date"]:
        return f"保存すると{label}になり、次回は{next_run['date']}に送信予定です。"
    reason_label = core.NEXT_RUN_REASON_LABELS.get(next_run["reason"], "")
    return f"保存すると{label}になりますが、次回送信の予定はありません（{reason_label}）。"


# ── ルーティング ──────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html", csrf_token=csrf_token())


@app.route("/api/status")
def api_status():
    conn = get_db()
    now = core.default_now()
    return jsonify(build_status(conn, now))


@app.route("/api/preview", methods=["POST"])
def api_preview():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "JSONを解釈できません"}), 400

    cleaned, errors = core.validate_settings(data)
    if errors:
        return jsonify({"valid": False, "errors": errors}), 400

    conn = get_db()
    now = core.default_now()
    is_holiday = core.default_is_holiday
    skip_dates = core.default_load_skip_dates()

    hypothetical = dict(cleaned)
    hypothetical["updated_at"] = now.isoformat()

    operational = core.compute_operational_status(hypothetical, now.date())

    def has_run(d: str) -> bool:
        return core.get_run(conn, d) is not None

    next_run = core.compute_next_run(hypothetical, now, is_holiday, skip_dates, has_run)

    return jsonify({
        "valid": True,
        "operational_status": operational,
        "operational_label": core.OPERATIONAL_LABELS[operational],
        "next_run": {
            "date": next_run["date"],
            "reason": next_run["reason"],
            "reason_label": (
                core.NEXT_RUN_REASON_LABELS.get(next_run["reason"])
                if next_run["reason"] else None
            ),
        },
        "summary": build_preview_summary(cleaned, operational, next_run),
    })


@app.route("/api/settings", methods=["PUT"])
def api_settings():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "JSONを解釈できません"}), 400

    cleaned, errors = core.validate_settings(data)
    if errors:
        return jsonify({"errors": errors}), 400

    revision = data.get("revision")
    if revision is None:
        return jsonify({"error": "revisionが必要です"}), 400

    conn = get_db()
    now = core.default_now()
    try:
        ok, err = core.save_settings(conn, cleaned, revision, now.isoformat())
    except Exception:
        return jsonify({"error": "保存に失敗しました。設定は変更されていません。"}), 500

    if not ok:
        if err == "revision_conflict":
            return jsonify({
                "error": "revision_conflict",
                "message": "他の変更が先に保存されています。再読込してください。",
            }), 409
        return jsonify({"error": "保存に失敗しました。設定は変更されていません。"}), 500

    return jsonify(build_status(conn, now))


@app.route("/api/pause", methods=["POST"])
def api_pause():
    conn = get_db()
    now = core.default_now()
    try:
        core.set_enabled(conn, False, now.isoformat())
    except Exception:
        return jsonify({"error": "停止の保存に失敗しました"}), 500
    return jsonify(build_status(conn, now))


def main() -> int:
    if not WEB_TOKEN:
        print("ERROR: .env に HAICHEESE_WEB_TOKEN を設定してください。未設定のため起動しません。")
        return 1

    host = os.getenv("HAICHEESE_WEB_HOST", "127.0.0.1")
    port = int(os.getenv("HAICHEESE_WEB_PORT", "5002"))
    try:
        app.run(host=host, port=port)
    except OSError as e:
        print(f"{host}:{port} で待ち受けできません（{e}）。127.0.0.1で起動します。")
        app.run(host="127.0.0.1", port=port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
