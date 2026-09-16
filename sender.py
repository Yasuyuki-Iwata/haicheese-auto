"""はいチーズ！ノートへのPlaywright操作。

旧 submit.py の送信処理をそのまま移した。ログイン→「連絡」タブ→「連絡帳の送信」→
体温・検温時刻・プールの入力→確認→送信、送信済み判定までをここに閉じ込め、
runner.py から呼び出す。本番サイトへの実際のアクセスはこのモジュールを直接
呼んだときだけ発生する。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError, sync_playwright

LOGIN_URL = "https://note.hoi-sys.com/"

# 連絡帳フォームの select インデックス（デバッグ調査済み、submit.py から引き継ぎ）
IDX_TEMP = 5    # 体温
IDX_HOUR = 6    # 検温時間（時）
IDX_MIN = 7     # 検温時間（分）


@dataclass
class SubmitOutcome:
    result: str  # success / already_sent / failed / unknown / cancelled
    reason: str
    screenshot_path: Optional[str] = None


def react_set(locator, value: str) -> None:
    """select_option + blur + focusout でReact内部stateを確実に更新する。
    select_option だけではonBlurが発火せず体温stateがnullのままになるため必須。"""
    locator.select_option(value)
    locator.dispatch_event("blur")
    locator.dispatch_event("focusout")


def login(page: Page, email: str, password: str) -> None:
    page.goto(LOGIN_URL, wait_until="networkidle")
    page.get_by_role("textbox").nth(0).fill(email)
    page.get_by_role("textbox").nth(1).fill(password)
    page.get_by_role("button", name="ログイン").click()
    page.wait_for_url("**/main**", timeout=15000)


def open_contact_form(page: Page) -> None:
    """「連絡」タブ→「連絡帳の送信」を開く。"""
    page.get_by_role("link", name="連絡").click()
    page.wait_for_load_state("networkidle")
    page.get_by_text("連絡帳の送信").click()
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(2000)


def is_already_sent(page: Page) -> bool:
    """既に送信済みだと入力欄がread-only表示になり「送信済み」ボタンが出る。"""
    return page.locator("button", has_text="送信済み").count() > 0


def _select_options(select_locator) -> list:
    return select_locator.evaluate(
        "el => Array.from(el.options).map(o => ({text: (o.textContent||'').trim(), value: o.value}))"
    )


def find_pool_select(page: Page, participation: bool):
    """options に 参加/不参加 または true/false を持つselectを探す。
    見つからなければ (None, None)。設定値を文言(参加/不参加)か値(true/false)の
    どちらで選ぶかは、そのselectが持つ選択肢に合わせて決める。値は推測で固定しない。"""
    selects = page.locator("select").all()
    for sel in selects:
        opts = _select_options(sel)
        texts = {o["text"] for o in opts}
        values = {o["value"] for o in opts}
        if {"参加", "不参加"} <= texts:
            target_text = "参加" if participation else "不参加"
            value = next(o["value"] for o in opts if o["text"] == target_text)
            return sel, value
        if {"true", "false"} <= values:
            value = "true" if participation else "false"
            return sel, value
    return None, None


def _save_screenshot(page: Page, screenshot_dir: Path) -> Optional[str]:
    try:
        screenshot_dir.mkdir(parents=True, exist_ok=True)
        name = f"error_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        path = screenshot_dir / name
        page.screenshot(path=str(path))
        return str(path)
    except Exception:
        return None


def submit(
    *,
    email: str,
    password: str,
    temp_str: str,
    hour: str,
    minute: str,
    pool_participation: bool,
    screenshot_dir: Path,
    check_stop: Callable[[], bool],
    headless: bool = True,
) -> SubmitOutcome:
    """1回分の連絡帳送信を試みる。

    check_stop() は「確認する」を押す直前と最後の「送信」を押す直前に呼ばれ、
    True を返すと押さずに cancelled で終了する。
    最後の送信ボタンを押す前の異常は failed、押した後の異常は unknown として返す。
    """
    committed = False
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        page = browser.new_page(viewport={"width": 390, "height": 844})
        try:
            login(page, email, password)
            open_contact_form(page)

            if is_already_sent(page):
                return SubmitOutcome("already_sent", "開いた時点で送信済み")

            selects = page.locator("select").all()
            if len(selects) <= IDX_MIN:
                return SubmitOutcome("failed", f"select要素が不足しています（{len(selects)}個）")

            temp_opts = {o["value"] for o in _select_options(selects[IDX_TEMP])}
            hour_opts = {o["value"] for o in _select_options(selects[IDX_HOUR])}
            min_opts = {o["value"] for o in _select_options(selects[IDX_MIN])}

            if temp_str not in temp_opts:
                return SubmitOutcome("failed", f"体温{temp_str}℃が選択肢にありません")
            if hour not in hour_opts:
                return SubmitOutcome("failed", f"検温時間（時）{hour}が選択肢にありません")
            if minute not in min_opts:
                return SubmitOutcome("failed", f"検温時間（分）{minute}が選択肢にありません")

            pool_select, pool_value = find_pool_select(page, pool_participation)
            if pool_select is None:
                return SubmitOutcome("failed", "プールの選択肢が見つかりません")

            react_set(selects[IDX_TEMP], temp_str)
            react_set(selects[IDX_HOUR], hour)
            react_set(selects[IDX_MIN], minute)
            react_set(pool_select, pool_value)

            if check_stop():
                return SubmitOutcome("cancelled", "「確認する」を押す前に停止しました")

            page.get_by_role("button", name="確認する").click()
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(1000)

            if check_stop():
                return SubmitOutcome("cancelled", "「送信」を押す前に停止しました")

            send_btn = page.locator("button").filter(has_text="送信")
            committed = True
            if send_btn.count() > 0:
                send_btn.first.click()
                page.wait_for_load_state("networkidle")

            page.wait_for_timeout(1000)
            page.reload()
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(1000)

            if is_already_sent(page):
                return SubmitOutcome("success", "送信完了")
            return SubmitOutcome("unknown", "送信後に送信済み表示を確認できませんでした")

        except Exception as e:
            path = _save_screenshot(page, screenshot_dir)
            label = "タイムアウト" if isinstance(e, PlaywrightTimeoutError) else "エラー"
            result = "unknown" if committed else "failed"
            return SubmitOutcome(result, f"{label}: {e}", path)

        finally:
            browser.close()
