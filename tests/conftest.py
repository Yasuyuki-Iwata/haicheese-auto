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

import hashlib
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


def _file_hash(path: Path):
    """ファイルのsha256だけを返す（中身は読み取らない・表示しない）。無ければNone。"""
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture(scope="session", autouse=True)
def fail_if_production_stores_touched():
    """本番の送信箱・本番のhaicheese DBのハッシュが変わっていないことを確認する
    （読むだけで、内容は表示しない）。isolate_discord_delivery・isolated_data_dir
    が正しく効いていれば、テストはこれらのファイルに一切触れない。

    注意: 本番の launchd（com.haicheese.runner・com.haicheese.monitor）は
    このテストとは無関係に本体の /Users/yasuyuki/Developer/haicheese 側で動き続けており、
    停止中（settings.enabled=0）でも runner が毎分 last_tick を更新するため、
    このテスト実行中にたまたま本番側のtickが走るとハッシュが変わり、ここが偽陽性で
    落ちることがある（テストコードが本番DBに触れたことを意味しない）。セッション単位に
    絞っているのは、失敗時に無関係な個別テストの teardown ではなくセッション全体の
    1箇所にまとめて出すため。"""
    watched = [REAL_DISCORD_OUTBOX_DB, REAL_HAICHEESE_DB]
    before = [_file_hash(p) for p in watched]
    yield
    after = [_file_hash(p) for p in watched]
    assert before == after, (
        "テストが本番の送信箱（~/Library/Application Support/discord-delivery）"
        "または本番の haicheese/data/app.sqlite3 を変更した可能性があります"
        "（本番launchdによる無関係な更新の可能性もあります）。"
        "DISCORD_DELIVERY_HOME・HAICHEESE_DATA_DIR をtmp_pathへ向けてください。"
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
