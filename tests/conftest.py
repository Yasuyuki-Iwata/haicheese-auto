"""pytest共通フィクスチャ。

本番安全のための方針:
- HAICHEESE_DATA_DIR を必ずテストごとの一時ディレクトリへ向け、
  リポジトリの data/app.sqlite3 を作らない。
- app.py をimportする前に HAICHEESE_WEB_TOKEN 等のダミー値を設定し、
  かつ dotenv.load_dotenv を no-op に差し替えて .env を実際には読み込ませない。
- sender.py 経由で本番サイト・Discord・osascriptへアクセスするテストはここでは書かない。
  各テストで submit_fn / notify_fn / now_fn / is_holiday / load_skip_dates を
  すべてダミーに差し替える。
"""

from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

JST = ZoneInfo("Asia/Tokyo")

# app.py を最初にimportする前に、確実にダミー値へ固定しておく。
# （load_dotenv は override=False が既定なので、先に設定しておけば.envの値は使われない）
os.environ.setdefault("HAICHEESE_WEB_TOKEN", "test-dummy-token-0123456789")
os.environ.setdefault("HAICHEESE_EMAIL", "test-dummy-email@example.invalid")
os.environ.setdefault("HAICHEESE_PASSWORD", "test-dummy-password")
os.environ.setdefault("DISCORD_BOT_TOKEN", "test-dummy-discord-bot-token")
os.environ.setdefault("DISCORD_DM_CHANNEL", "test-dummy-discord-channel")
os.environ.setdefault("HAICHEESE_WEB_HOST", "127.0.0.1")
os.environ.setdefault("HAICHEESE_WEB_PORT", "0")

# .env を実際には読み込ませない（中身を見ない・表示しない）。
import dotenv  # noqa: E402

_real_load_dotenv = dotenv.load_dotenv
dotenv.load_dotenv = lambda *a, **k: False  # type: ignore


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path, monkeypatch):
    """すべてのテストで data/app.sqlite3 等をテスト用の一時ディレクトリへ向ける。"""
    data_dir = tmp_path / "haicheese-data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HAICHEESE_DATA_DIR", str(data_dir))
    yield data_dir


@pytest.fixture(autouse=True)
def isolate_discord_delivery(tmp_path, monkeypatch):
    """共通配信部（discord_delivery）が本番の送信箱・Botトークンに触れないよう、
    全テストで隔離する。

    - DISCORD_DELIVERY_HOME: 送信箱（sqlite・添付）の保存先を一時ディレクトリへ
    - DISCORD_DELIVERY_USER_HOME: receipt用のホームを一時ディレクトリへ
    - NO_DISCORD=1: 実際のHTTP送信を止める（送信を試す個別テストだけ外し、
      共通配信部のHTTP層（_http_requestなど）を偽物に差し替える）
    """
    monkeypatch.setenv("DISCORD_DELIVERY_HOME", str(tmp_path / "discord-delivery-outbox"))
    monkeypatch.setenv("DISCORD_DELIVERY_USER_HOME", str(tmp_path))
    monkeypatch.setenv("NO_DISCORD", "1")
    yield


REAL_DISCORD_OUTBOX_DB = Path.home() / "Library/Application Support/discord-delivery/outbox.sqlite3"
REAL_HAICHEESE_DB = Path("/Users/yasuyuki/Developer/haicheese/data/app.sqlite3")


def _read_only_rows(path: Path, query: str, params=()):
    """本番DBを読み取り専用で開いて行を返す。無ければNone。"""
    if not path.exists():
        return None
    import sqlite3
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return connection.execute(query, params).fetchall()
    finally:
        connection.close()


def _production_snapshot():
    # 本番の launchd（com.haicheese.runner）は停止中でも毎分 meta.last_tick を書き、
    # 送信箱は他のアプリの通知と15分巡回で変わる。ファイルのハッシュでは偶発的に落ちる
    # ので、試験が書きうる行だけを比べる: 送信箱の haicheese の行、本番DBの
    # settings・runs・alerts（meta は比べない）。
    return {
        "outbox_haicheese": _read_only_rows(
            REAL_DISCORD_OUTBOX_DB,
            "SELECT id, status, created_at FROM events WHERE source=? ORDER BY id",
            ("haicheese",),
        ),
        "settings": _read_only_rows(REAL_HAICHEESE_DB, "SELECT * FROM settings"),
        "runs": _read_only_rows(REAL_HAICHEESE_DB, "SELECT * FROM runs ORDER BY target_date"),
        "alerts": _read_only_rows(REAL_HAICHEESE_DB, "SELECT * FROM alerts ORDER BY id"),
    }


@pytest.fixture(scope="session", autouse=True)
def fail_if_production_stores_touched():
    """本番の送信箱の haicheese の行と、本番DBの settings・runs・alerts が
    試験の前後で変わらないことを確かめる（読み取り専用で開く）。"""
    before = _production_snapshot()
    yield
    after = _production_snapshot()
    changed = [key for key in before if before[key] != after[key]]
    assert not changed, (
        f"試験の前後で本番の {changed} が変わりました。"
        "DISCORD_DELIVERY_HOME・HAICHEESE_DATA_DIR が一時ディレクトリを向いているか確認してください。"
    )


@pytest.fixture
def conn(isolated_data_dir):
    """テスト用DBへの接続（core.connectを使うのでinit_dbが走り初期値が入る）。"""
    import core

    c = core.connect()
    yield c
    c.close()


def jst(y, mo, d, h=0, mi=0, s=0) -> datetime:
    return datetime(y, mo, d, h, mi, s, tzinfo=JST)


def make_is_holiday(holiday_isos=frozenset()):
    """本物のjpholidayを使わない、テスト用のダミー祝日判定。"""
    def _is_holiday(d):
        return d.isoformat() in holiday_isos
    return _is_holiday


def make_skip_dates(skip_isos=frozenset()):
    def _load():
        return set(skip_isos)
    return _load
