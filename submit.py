#!/usr/bin/env python3
"""はいチーズ！ノート 毎朝自動送信スクリプト"""

import json
import os
import random
import subprocess
import urllib.request
from datetime import date, datetime
from pathlib import Path

import jpholiday
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

SCRIPT_DIR = Path(__file__).parent
load_dotenv(SCRIPT_DIR / ".env")

EMAIL              = os.getenv("HAICHEESE_EMAIL")
PASSWORD           = os.getenv("HAICHEESE_PASSWORD")
DISCORD_BOT_TOKEN  = os.getenv("DISCORD_BOT_TOKEN")
DISCORD_DM_CHANNEL = os.getenv("DISCORD_DM_CHANNEL")
LOG_DIR = SCRIPT_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)

LOGIN_URL = "https://note.hoi-sys.com/"

# 検温時間（固定）
TEMP_HOUR = "06"   # 6時（2桁ゼロ埋め）
TEMP_MINUTE = "50" # 50分

# 連絡帳フォームの select インデックス（デバッグ調査済み）
IDX_TEMP = 5    # 体温
IDX_HOUR = 6    # 検温時間（時）
IDX_MIN  = 7    # 検温時間（分）
IDX_POOL = 11   # プール参加


def random_temp() -> str:
    """36.0〜36.9 の範囲でランダムな体温を返す"""
    val = 36.0 + random.randint(0, 9) / 10
    return f"{val:.1f}"


def log(msg: str):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"{now}  {msg}"
    print(line)
    log_file = LOG_DIR / f"{datetime.now().strftime('%Y-%m')}.log"
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def notify(title: str, message: str):
    script = f'display notification "{message}" with title "{title}"'
    subprocess.run(["osascript", "-e", script], check=False)


def notify_discord(message: str):
    """Discord の DM で通知を送る"""
    api = "https://discord.com/api/v10"
    headers = {
        "Authorization": f"Bot {DISCORD_BOT_TOKEN}",
        "Content-Type": "application/json",
        "User-Agent": "DiscordBot (haicheese, 1.0)",
    }

    try:
        req = urllib.request.Request(
            f"{api}/channels/{DISCORD_DM_CHANNEL}/messages",
            data=json.dumps({"content": message}).encode(),
            headers=headers,
            method="POST",
        )
        urllib.request.urlopen(req)
    except Exception as e:
        log(f"Discord通知失敗: {e}")


def is_weekday() -> bool:
    """今日が平日（土日・祝日でない）かどうかを返す"""
    today = date.today()
    if today.weekday() >= 5:  # 5=土, 6=日
        return False
    if jpholiday.is_holiday(today):
        return False
    return True


def run():
    if not EMAIL or not PASSWORD or PASSWORD == "your_password_here":
        log("ERROR: .env にメールアドレス・パスワードが未設定です")
        notify("はいチーズ！エラー", ".env の設定が未完了です")
        return

    if not is_weekday():
        today = date.today()
        reason = "祝日" if jpholiday.is_holiday(today) else "土日"
        log(f"スキップ（{reason}）— {today}")
        return

    temp = random_temp()
    log(f"開始 — 体温:{temp}℃ / 検温:{TEMP_HOUR}:{TEMP_MINUTE} / プール:参加")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 390, "height": 844})

        try:
            # ログイン
            page.goto(LOGIN_URL, wait_until="networkidle")
            page.get_by_role("textbox").nth(0).fill(EMAIL)
            page.get_by_role("textbox").nth(1).fill(PASSWORD)
            page.get_by_role("button", name="ログイン").click()
            page.wait_for_url("**/main**", timeout=15000)
            log("ログイン成功")

            # 「連絡」タブ
            page.get_by_role("link", name="連絡").click()
            page.wait_for_load_state("networkidle")

            # 「連絡帳の送信」
            page.get_by_text("連絡帳の送信").click()
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(2000)
            log("連絡帳フォーム読み込み完了")

            selects = page.locator("select").all()
            log(f"select要素数: {len(selects)}")

            # 体温
            selects[IDX_TEMP].select_option(temp)
            log(f"体温: {temp}℃")

            # 検温時間（時）
            selects[IDX_HOUR].select_option(TEMP_HOUR)
            log(f"検温時間（時）: {TEMP_HOUR}")

            # 検温時間（分）
            selects[IDX_MIN].select_option(TEMP_MINUTE)
            log(f"検温時間（分）: {TEMP_MINUTE}")

            # プール参加
            selects[IDX_POOL].select_option("true")
            log("プール: 参加")

            # 「確認する」ボタン
            page.get_by_role("button", name="確認する").click()
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(1000)

            # 確認画面から「送信」ボタン（ある場合）
            send_btn = page.locator("button").filter(has_text="送信")
            if send_btn.count() > 0:
                send_btn.first.click()
                page.wait_for_load_state("networkidle")
                log("確認画面から送信完了")
            else:
                log("送信完了")

            msg = f"体温 {temp}℃ / {TEMP_HOUR}:{TEMP_MINUTE} / プール参加"
            log(f"SUCCESS: {msg}")
            notify("✓ はいチーズ！送信完了", msg)
            notify_discord(f"✅ はいチーズ！連絡帳 送信完了\n{msg}")

        except PlaywrightTimeoutError as e:
            log(f"ERROR (timeout): {e}")
            page.screenshot(path=str(LOG_DIR / f"error_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"))
            notify("はいチーズ！エラー", "タイムアウト — ログを確認してください")
            notify_discord("⚠️ はいチーズ！連絡帳 送信失敗（タイムアウト）\nログを確認してください")

        except Exception as e:
            log(f"ERROR: {e}")
            page.screenshot(path=str(LOG_DIR / f"error_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"))
            notify("はいチーズ！エラー", str(e)[:60])
            notify_discord(f"⚠️ はいチーズ！連絡帳 送信失敗\n{str(e)[:100]}")

        finally:
            browser.close()


if __name__ == "__main__":
    run()
