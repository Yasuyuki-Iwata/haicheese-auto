"""受け入れ条件 12: 320px/390px/1024px幅でのレイアウト確認（Playwright）。

Flaskのapp.pyを127.0.0.1の一時ポートでテスト用に起動し、ダミートークン・
一時DBで表示する。本番サイト・Discordへは一切アクセスしない。
起動したサーバーは各テスト終了時に必ず停止する。
"""

from __future__ import annotations

import threading

import pytest
from playwright.sync_api import sync_playwright
from werkzeug.serving import make_server

import app as app_module


@pytest.fixture
def live_server():
    server = make_server("127.0.0.1", 0, app_module.app)
    thread = threading.Thread(target=server.serve_forever)
    thread.daemon = True
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)


TOKEN = app_module.WEB_TOKEN


@pytest.mark.parametrize("width", [320, 390, 1024])
def test_no_horizontal_scroll_and_buttons_visible(live_server, width):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            context = browser.new_context(viewport={"width": width, "height": 800})
            page = context.new_page()
            page.goto(f"{live_server}/?token={TOKEN}")
            page.wait_for_selector("#save-btn")

            scroll_width = page.evaluate("document.documentElement.scrollWidth")
            inner_width = page.evaluate("window.innerWidth")
            assert scroll_width <= inner_width, (
                f"width={width}: scrollWidth={scroll_width} > innerWidth={inner_width}"
            )

            assert page.is_visible("#save-btn")
            assert page.is_visible("#pause-btn")
        finally:
            browser.close()


def test_unsaved_indicator_appears_after_edit(live_server):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            context = browser.new_context(viewport={"width": 390, "height": 800})
            page = context.new_page()
            page.goto(f"{live_server}/?token={TOKEN}")
            page.wait_for_selector("#save-btn")
            page.wait_for_function(
                "document.getElementById('footer-summary').textContent.includes('保存済み')"
            )

            footer_before_class = page.get_attribute("#footer-summary", "class")
            assert "dirty" not in (footer_before_class or "")

            page.click("#enabled-on")
            page.wait_for_function(
                "document.getElementById('footer-summary').classList.contains('dirty')"
            )
            footer_after_class = page.get_attribute("#footer-summary", "class")
            assert "dirty" in footer_after_class
        finally:
            browser.close()
