"""はいチーズ！ノート自動送信 共通ロジック。

設定の読み書き、状態判定、次回予定、送信可否判定など、UI（app.py）と
定期実行（runner.py・monitor.py）の両方から同じ関数を使うためにここへ集める。
時計・祝日判定・skip日付の読み込みはすべて引数で差し替えられるようにしてあるので、
本番サイトへ接続せずにテストできる。
"""

from __future__ import annotations

import os
import random
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable, Optional
from zoneinfo import ZoneInfo

import jpholiday

SCRIPT_DIR = Path(__file__).resolve().parent
JST = ZoneInfo("Asia/Tokyo")

# 体温は 0.1℃単位の整数（例: 36.5℃ -> 365）で保持する。浮動小数の誤差を避けるため。
TEMP_MIN_LIMIT = 350  # 35.0℃
TEMP_MAX_LIMIT = 420  # 42.0℃

# 送信判定: 対象日の予定時刻から何分未満なら送信を開始してよいか
SUBMIT_WINDOW_MINUTES = 3
# 監視: 予定時刻から何分過ぎたら未送信警告の対象にするか
ALERT_AFTER_MINUTES = 20
# 監視: 最終tickがこの分数以上前なら要確認とする
TICK_STALE_MINUTES = 10
# running のまま経過したら結果不明として扱う分数
RUNNING_STALE_MINUTES = 15
# 次回予定・直近対象日を探す最大日数
SEARCH_HORIZON_DAYS = 400

OPERATIONAL_LABELS = {
    "stopped": "停止中",
    "waiting": "開始待ち",
    "active": "稼働中",
    "ended": "期間終了",
}

HEALTH_LABELS = {
    "unknown": "未確認",
    "ok": "正常",
    "warning": "要確認",
}

NEXT_RUN_REASON_LABELS = {
    "stopped": "停止中",
    "ended": "期間終了",
    "no_target": "期間内に対象日なし",
}

RESULT_LABELS = {
    "running": "処理中",
    "success": "送信済み",
    "already_sent": "送信済み（送信先で確認）",
    "failed": "失敗",
    "unknown": "結果不明",
    "cancelled": "スキップ（停止操作）",
}


# ── データディレクトリ・接続 ──────────────────────────────────────

def data_dir() -> Path:
    override = os.getenv("HAICHEESE_DATA_DIR")
    d = Path(override) if override else (SCRIPT_DIR / "data")
    d.mkdir(parents=True, exist_ok=True)
    return d


def db_path() -> Path:
    return data_dir() / "app.sqlite3"


def lock_path() -> Path:
    return data_dir() / "run.lock"


def connect(path: Optional[Path] = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path or db_path()), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    init_db(conn)
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS settings (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            enabled INTEGER NOT NULL DEFAULT 0,
            start_date TEXT,
            end_date TEXT,
            submit_time TEXT NOT NULL DEFAULT '07:00',
            measurement_time TEXT NOT NULL DEFAULT '06:50',
            temp_min INTEGER NOT NULL DEFAULT 365,
            temp_max INTEGER NOT NULL DEFAULT 368,
            pool_participation INTEGER NOT NULL DEFAULT 1,
            updated_at TEXT NOT NULL,
            revision INTEGER NOT NULL DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            target_date TEXT NOT NULL UNIQUE,
            scheduled_time TEXT,
            started_at TEXT,
            finished_at TEXT,
            settings_snapshot TEXT,
            temp INTEGER,
            result TEXT NOT NULL,
            reason TEXT,
            notify_status TEXT
        );

        CREATE TABLE IF NOT EXISTS alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            target_date TEXT NOT NULL,
            kind TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(target_date, kind)
        );

        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT
        );
        """
    )
    row = conn.execute("SELECT id FROM settings WHERE id=1").fetchone()
    if row is None:
        now_iso = default_now().isoformat()
        conn.execute(
            "INSERT INTO settings (id, enabled, start_date, end_date, submit_time, "
            "measurement_time, temp_min, temp_max, pool_participation, updated_at, revision) "
            "VALUES (1, 0, NULL, NULL, '07:00', '06:50', 365, 368, 1, ?, 1)",
            (now_iso,),
        )
    conn.commit()


# ── 時計・祝日・skip日付（差し替え可能な既定実装） ──────────────────

def default_now() -> datetime:
    return datetime.now(JST)


def default_is_holiday(d: date) -> bool:
    return jpholiday.is_holiday(d)


def default_load_skip_dates(path: Optional[Path] = None) -> set:
    p = path or (SCRIPT_DIR / "skip_dates.txt")
    if not p.exists():
        return set()
    return {line.strip() for line in p.read_text(encoding="utf-8").splitlines() if line.strip()}


# ── 体温 ─────────────────────────────────────────────────────

def temp_tenths_to_str(tenths: int) -> str:
    """36.5 -> '36.5'（送信フォームのselect値と同じ書式）"""
    return f"{tenths / 10:.1f}"


def generate_temp(temp_min: int, temp_max: int, rng: random.Random = None) -> int:
    """temp_min〜temp_max（0.1℃単位の整数、両端含む）からランダムに1つ返す。"""
    r = rng or random
    return r.randint(temp_min, temp_max)


# ── 設定の検証 ────────────────────────────────────────────────

def _parse_date(payload: dict, key: str, errors: list) -> Optional[str]:
    v = payload.get(key)
    if v in (None, ""):
        return None
    if not isinstance(v, str):
        errors.append(f"{key}の形式が不正です")
        return None
    try:
        datetime.strptime(v, "%Y-%m-%d")
    except ValueError:
        errors.append(f"{key}の形式が不正です（YYYY-MM-DD）")
        return None
    return v


def _parse_hhmm(payload: dict, key: str, errors: list, minute_step: int = 1) -> Optional[str]:
    v = payload.get(key)
    if not isinstance(v, str) or ":" not in v:
        errors.append(f"{key}の形式が不正です（HH:MM）")
        return None
    parts = v.split(":")
    if len(parts) != 2:
        errors.append(f"{key}の形式が不正です（HH:MM）")
        return None
    try:
        h, m = int(parts[0]), int(parts[1])
    except ValueError:
        errors.append(f"{key}の形式が不正です（HH:MM）")
        return None
    if not (0 <= h <= 23 and 0 <= m <= 59):
        errors.append(f"{key}の範囲が不正です")
        return None
    if minute_step > 1 and m % minute_step != 0:
        errors.append(f"{key}は{minute_step}分刻みで指定してください")
        return None
    return f"{h:02d}:{m:02d}"


def _parse_temp(payload: dict, key: str, errors: list) -> Optional[int]:
    v = payload.get(key)
    try:
        f = float(v)
    except (TypeError, ValueError):
        errors.append(f"{key}が不正です")
        return None
    tenths = round(f * 10)
    if abs(tenths - f * 10) > 1e-6:
        errors.append(f"{key}は0.1℃刻みで指定してください")
        return None
    if not (TEMP_MIN_LIMIT <= tenths <= TEMP_MAX_LIMIT):
        errors.append(f"{key}は35.0〜42.0℃の範囲にしてください")
        return None
    return tenths


def validate_settings(payload: dict) -> tuple:
    """入力を検証する。戻り値は (cleaned_settings, errors)。
    cleaned_settings は enabled/start_date/end_date/submit_time/measurement_time/
    temp_min/temp_max/pool_participation を持つ辞書。不正なら None。"""
    errors: list = []

    enabled = bool(payload.get("enabled", False))
    start_date = _parse_date(payload, "start_date", errors)
    end_date = _parse_date(payload, "end_date", errors)
    if start_date and end_date and start_date > end_date:
        errors.append("開始日は終了日より前（または同じ日）にしてください")

    submit_time = _parse_hhmm(payload, "submit_time", errors, minute_step=1)
    measurement_time = _parse_hhmm(payload, "measurement_time", errors, minute_step=5)

    temp_min = _parse_temp(payload, "temp_min", errors)
    temp_max = _parse_temp(payload, "temp_max", errors)
    if temp_min is not None and temp_max is not None and temp_min > temp_max:
        errors.append("体温の下限は上限以下にしてください")

    pool_participation = bool(payload.get("pool_participation", True))

    if errors:
        return None, errors

    return {
        "enabled": enabled,
        "start_date": start_date,
        "end_date": end_date,
        "submit_time": submit_time,
        "measurement_time": measurement_time,
        "temp_min": temp_min,
        "temp_max": temp_max,
        "pool_participation": pool_participation,
    }, []


# ── 設定の読み書き（DB） ──────────────────────────────────────

def get_settings(conn: sqlite3.Connection) -> dict:
    row = conn.execute("SELECT * FROM settings WHERE id=1").fetchone()
    return dict(row)


def save_settings(conn: sqlite3.Connection, cleaned: dict, expected_revision, now_iso: str):
    """全項目を検証済みの内容で保存する。expected_revision が None なら競合チェックしない
    （pause専用の set_enabled はこちらを使わず別関数を使う）。
    戻り値は (ok: bool, error: 'revision_conflict' | None)。"""
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute("SELECT revision FROM settings WHERE id=1").fetchone()
        current_rev = row["revision"]
        if expected_revision is not None and int(expected_revision) != current_rev:
            conn.rollback()
            return False, "revision_conflict"
        new_rev = current_rev + 1
        conn.execute(
            "UPDATE settings SET enabled=?, start_date=?, end_date=?, submit_time=?, "
            "measurement_time=?, temp_min=?, temp_max=?, pool_participation=?, "
            "updated_at=?, revision=? WHERE id=1",
            (
                int(cleaned["enabled"]), cleaned["start_date"], cleaned["end_date"],
                cleaned["submit_time"], cleaned["measurement_time"], cleaned["temp_min"],
                cleaned["temp_max"], int(cleaned["pool_participation"]), now_iso, new_rev,
            ),
        )
        conn.commit()
        return True, None
    except Exception:
        conn.rollback()
        raise


def set_enabled(conn: sqlite3.Connection, enabled: bool, now_iso: str) -> dict:
    """自動送信の有効・無効だけを変更する（POST /api/pause 用）。他項目は触らない。"""
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute("SELECT revision FROM settings WHERE id=1").fetchone()
        new_rev = row["revision"] + 1
        conn.execute(
            "UPDATE settings SET enabled=?, updated_at=?, revision=? WHERE id=1",
            (int(enabled), now_iso, new_rev),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return get_settings(conn)


# ── 対象日・状態判定・次回予定 ────────────────────────────────────

def is_target_date(d: date, settings: dict, is_holiday: Callable, skip_dates: set) -> bool:
    """平日・祝日でない・skip_dates.txtに無い・設定の期間内（両端含む）かどうか。
    自動送信の有効・無効はここでは見ない。"""
    if d.weekday() >= 5:
        return False
    if is_holiday(d):
        return False
    iso = d.isoformat()
    if iso in skip_dates:
        return False
    start = settings.get("start_date")
    end = settings.get("end_date")
    if start and iso < start:
        return False
    if end and iso > end:
        return False
    return True


def combine_datetime(d: date, hhmm: str) -> datetime:
    h, m = hhmm.split(":")
    return datetime(d.year, d.month, d.day, int(h), int(m), tzinfo=JST)


def compute_operational_status(settings: dict, today: date) -> str:
    """停止中／開始待ち／稼働中／期間終了（初日・最終日を含む）を返す。"""
    if not settings.get("enabled"):
        return "stopped"
    iso = today.isoformat()
    start = settings.get("start_date")
    end = settings.get("end_date")
    if start and iso < start:
        return "waiting"
    if end and iso > end:
        return "ended"
    return "active"


def should_attempt_submit(
    settings: dict,
    now: datetime,
    is_holiday: Callable,
    skip_dates: set,
    has_run: Callable[[str], bool],
) -> bool:
    """今このタイミングで送信を開始してよいかどうか。
    対象日かつ予定時刻〜3分未満の間で、設定の更新日時が予定時刻より前で、
    その日のrunsが無いときだけ True になる。"""
    if not settings.get("enabled"):
        return False
    today = now.date()
    if not is_target_date(today, settings, is_holiday, skip_dates):
        return False
    scheduled_dt = combine_datetime(today, settings["submit_time"])
    window_end = scheduled_dt + timedelta(minutes=SUBMIT_WINDOW_MINUTES)
    if not (scheduled_dt <= now < window_end):
        return False
    updated_at = _parse_iso(settings.get("updated_at"))
    if updated_at is not None and updated_at >= scheduled_dt:
        # 予定時刻後に有効化・変更された場合は、その日は即時送信しない
        return False
    if has_run(today.isoformat()):
        return False
    return True


def compute_next_run(
    settings: dict,
    now: datetime,
    is_holiday: Callable,
    skip_dates: set,
    has_run: Callable[[str], bool],
) -> dict:
    """次回送信予定日を返す。{"date": "YYYY-MM-DD"|None, "reason": None|"stopped"|"ended"|"no_target"}"""
    status = compute_operational_status(settings, now.date())
    if status == "stopped":
        return {"date": None, "reason": "stopped"}
    if status == "ended":
        return {"date": None, "reason": "ended"}

    for i in range(0, SEARCH_HORIZON_DAYS + 1):
        d = now.date() + timedelta(days=i)
        if not is_target_date(d, settings, is_holiday, skip_dates):
            continue
        if has_run(d.isoformat()):
            continue
        if i == 0:
            scheduled_dt = combine_datetime(d, settings["submit_time"])
            window_end = scheduled_dt + timedelta(minutes=SUBMIT_WINDOW_MINUTES)
            if now >= window_end:
                # 今日の送信可能な時間帯はもう過ぎている
                continue
        return {"date": d.isoformat(), "reason": None}

    return {"date": None, "reason": "no_target"}


def most_recent_target_date(
    today: date, settings: dict, is_holiday: Callable, skip_dates: set
) -> Optional[date]:
    """今日以前で直近の対象日を返す（無ければNone）。"""
    for i in range(0, SEARCH_HORIZON_DAYS + 1):
        d = today - timedelta(days=i)
        if is_target_date(d, settings, is_holiday, skip_dates):
            return d
    return None


def _parse_iso(v) -> Optional[datetime]:
    if not v:
        return None
    dt = datetime.fromisoformat(v)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=JST)
    return dt


def effective_result(run: Optional[dict], now: datetime) -> Optional[str]:
    """runs.result をそのまま返すが、15分以上runningのままの記録は結果不明として扱う。"""
    if run is None:
        return None
    result = run["result"] if isinstance(run, sqlite3.Row) else run.get("result")
    if result == "running":
        started = run["started_at"] if isinstance(run, sqlite3.Row) else run.get("started_at")
        started_dt = _parse_iso(started)
        if started_dt is not None and (now - started_dt) > timedelta(minutes=RUNNING_STALE_MINUTES):
            return "unknown"
    return result


def compute_health_status(
    conn: sqlite3.Connection,
    now: datetime,
    is_holiday: Callable,
    skip_dates: set,
) -> str:
    """未確認／正常／要確認。

    停止中（enabled=0）はtick停滞・未実行のどちらも要確認にしない（未実行判定は
    有効中だけ行う）。一方、runsに記録済みのfailed/unknown（15分超のrunningを
    含む）は実際の異常なので、停止中でも要確認として表示する。
    """
    last_tick = get_last_tick(conn)
    if last_tick is None:
        return "unknown"
    settings = get_settings(conn)
    enabled = bool(settings.get("enabled"))
    if enabled and (now - last_tick) > timedelta(minutes=TICK_STALE_MINUTES):
        return "warning"
    d = most_recent_target_date(now.date(), settings, is_holiday, skip_dates)
    if d is None:
        return "ok"
    run = get_run(conn, d.isoformat())
    eff = effective_result(run, now)
    if eff is None:
        if not enabled:
            return "ok"
        scheduled_dt = combine_datetime(d, settings["submit_time"])
        if now >= scheduled_dt + timedelta(minutes=ALERT_AFTER_MINUTES):
            return "warning"
        return "ok"
    if eff in ("failed", "unknown"):
        return "warning"
    return "ok"


# ── runs / alerts / meta ─────────────────────────────────────

def get_run(conn: sqlite3.Connection, target_date: str) -> Optional[dict]:
    row = conn.execute("SELECT * FROM runs WHERE target_date=?", (target_date,)).fetchone()
    return dict(row) if row else None


def get_recent_runs(conn: sqlite3.Connection, limit: int = 5) -> list:
    rows = conn.execute(
        "SELECT * FROM runs ORDER BY target_date DESC LIMIT ?", (limit,)
    ).fetchall()
    return [dict(r) for r in rows]


def claim_run(conn: sqlite3.Connection, target_date: str, scheduled_time: str,
              settings_snapshot: str, started_at_iso: str) -> bool:
    """その日のrunsをrunningとして確保する。既にあればFalse（送らない）。"""
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(
            "INSERT INTO runs (target_date, scheduled_time, started_at, "
            "settings_snapshot, result) VALUES (?, ?, ?, ?, 'running')",
            (target_date, scheduled_time, started_at_iso, settings_snapshot),
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        conn.rollback()
        return False


def finish_run(conn: sqlite3.Connection, target_date: str, *, result: str,
               reason: str, temp: Optional[int], finished_at_iso: str) -> None:
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(
            "UPDATE runs SET result=?, reason=?, temp=?, finished_at=? WHERE target_date=?",
            (result, reason, temp, finished_at_iso, target_date),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def set_notify_status(conn: sqlite3.Connection, target_date: str, status: str) -> None:
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(
            "UPDATE runs SET notify_status=? WHERE target_date=?", (status, target_date)
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def has_alert(conn: sqlite3.Connection, target_date: str, kind: str) -> bool:
    """同じ日・種類のアラートが既に記録済みかどうかを読むだけ（副作用なし）。"""
    row = conn.execute(
        "SELECT 1 FROM alerts WHERE target_date=? AND kind=?", (target_date, kind)
    ).fetchone()
    return row is not None


def record_alert_once(conn: sqlite3.Connection, target_date: str, kind: str, created_at_iso: str) -> bool:
    """同じ日・種類のアラートを一度だけ記録する。初めてならTrue（通知してよい）。"""
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(
            "INSERT INTO alerts (target_date, kind, created_at) VALUES (?, ?, ?)",
            (target_date, kind, created_at_iso),
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        conn.rollback()
        return False


def record_tick(conn: sqlite3.Connection, now: datetime) -> None:
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(
            "INSERT INTO meta (key, value) VALUES ('last_tick', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (now.isoformat(),),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def get_last_tick(conn: sqlite3.Connection) -> Optional[datetime]:
    row = conn.execute("SELECT value FROM meta WHERE key='last_tick'").fetchone()
    if not row:
        return None
    return _parse_iso(row["value"])
