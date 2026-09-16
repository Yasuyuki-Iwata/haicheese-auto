"""sender.py: 受け入れ条件 6・停止の再確認 のテスト。

本物のnote.hoi-sys.comへは一切アクセスしない。sender.login / sender.open_contact_form
はテスト内でローカルfixture HTMLへ差し替え（モンキーパッチ）、
sender.submit() 自体は一切書き換えない。
"""

from __future__ import annotations

import pytest
from playwright.sync_api import sync_playwright

import sender
from fixtures.hoicheese_form import build_form_html, write_fixture


@pytest.fixture
def page():
    """1テストにつき独立したsync_playwrightインスタンスを開閉する。

    sender.submit()自身も内部でsync_playwright()を開くため、
    モジュール/セッションスコープで使い回すと二重起動でエラーになる。
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        pg = browser.new_page()
        yield pg
        browser.close()


def never_stop():
    return False


def always_stop():
    return True


def stop_after(n):
    """n回目の呼び出しから停止(True)を返す。1始まり。"""
    counter = {"n": 0}

    def _check():
        counter["n"] += 1
        return counter["n"] >= n
    return _check


def patch_login_to_fixture(monkeypatch, url: str):
    monkeypatch.setattr(sender, "login", lambda page, email, password: page.goto(url))
    monkeypatch.setattr(sender, "open_contact_form", lambda page: None)


# ── 6. find_pool_select（ローカルfixture、page.set_content） ─────────

def test_find_pool_select_text_participation(page):
    page.set_content(build_form_html(pool_style="text"))
    sel, value = sender.find_pool_select(page, True)
    assert sel is not None
    assert value == "1"


def test_find_pool_select_text_non_participation(page):
    page.set_content(build_form_html(pool_style="text"))
    sel, value = sender.find_pool_select(page, False)
    assert sel is not None
    assert value == "0"


def test_find_pool_select_value_true_false(page):
    page.set_content(build_form_html(pool_style="value"))
    sel, value = sender.find_pool_select(page, True)
    assert sel is not None
    assert value == "true"
    sel2, value2 = sender.find_pool_select(page, False)
    assert value2 == "false"


def test_find_pool_select_none_when_missing(page):
    page.set_content(build_form_html(pool_style="none"))
    sel, value = sender.find_pool_select(page, True)
    assert sel is None
    assert value is None


def test_find_pool_select_ignores_irrelevant_select(page):
    page.set_content(build_form_html(pool_style="irrelevant"))
    sel, value = sender.find_pool_select(page, True)
    assert sel is None
    assert value is None


# ── submit(): 正常系・体温/時刻/プールの値がフォームへ反映される ────

def test_submit_success_with_text_pool_participation(monkeypatch, tmp_path):
    url = write_fixture(tmp_path, "form.html", build_form_html(pool_style="text"))
    patch_login_to_fixture(monkeypatch, url)

    outcome = sender.submit(
        email="dummy@example.invalid", password="dummy",
        temp_str="36.7", hour="06", minute="50",
        pool_participation=True,
        screenshot_dir=tmp_path / "logs",
        check_stop=never_stop,
    )
    assert outcome.result == "success"


def test_submit_success_with_value_pool_non_participation(monkeypatch, tmp_path):
    url = write_fixture(tmp_path, "form.html", build_form_html(pool_style="value"))
    patch_login_to_fixture(monkeypatch, url)

    outcome = sender.submit(
        email="dummy@example.invalid", password="dummy",
        temp_str="36.7", hour="06", minute="50",
        pool_participation=False,
        screenshot_dir=tmp_path / "logs",
        check_stop=never_stop,
    )
    assert outcome.result == "success"


def test_submit_already_sent_at_open(monkeypatch, tmp_path):
    url = write_fixture(tmp_path, "form.html", build_form_html(initial_sent=True))
    patch_login_to_fixture(monkeypatch, url)

    outcome = sender.submit(
        email="dummy@example.invalid", password="dummy",
        temp_str="36.7", hour="06", minute="50",
        pool_participation=True,
        screenshot_dir=tmp_path / "logs",
        check_stop=never_stop,
    )
    assert outcome.result == "already_sent"


def test_submit_fails_without_pressing_when_pool_select_missing(monkeypatch, tmp_path):
    """プールの選択肢が見つからない場合は、確認するボタンを押さずにfailedにする。"""
    url = write_fixture(tmp_path, "form.html", build_form_html(pool_style="none"))
    patch_login_to_fixture(monkeypatch, url)

    outcome = sender.submit(
        email="dummy@example.invalid", password="dummy",
        temp_str="36.7", hour="06", minute="50",
        pool_participation=True,
        screenshot_dir=tmp_path / "logs",
        check_stop=always_stop,  # 押されたら即bugが見える。failedなら呼ばれないはず
    )
    assert outcome.result == "failed"
    assert "プール" in outcome.reason


def test_submit_fails_when_temp_not_in_options(monkeypatch, tmp_path):
    limited_temp_values = ["35.0", "35.1", "35.2"]
    url = write_fixture(
        tmp_path, "form.html",
        build_form_html(pool_style="text", temp_values=limited_temp_values),
    )
    patch_login_to_fixture(monkeypatch, url)

    outcome = sender.submit(
        email="dummy@example.invalid", password="dummy",
        temp_str="36.7", hour="06", minute="50",
        pool_participation=True,
        screenshot_dir=tmp_path / "logs",
        check_stop=always_stop,
    )
    assert outcome.result == "failed"
    assert "選択肢にありません" in outcome.reason


def test_submit_fails_when_selects_are_insufficient(monkeypatch, tmp_path):
    url = write_fixture(
        tmp_path, "form.html",
        build_form_html(num_dummy_selects=3, include_main_selects=False, pool_style="none"),
    )
    patch_login_to_fixture(monkeypatch, url)

    outcome = sender.submit(
        email="dummy@example.invalid", password="dummy",
        temp_str="36.7", hour="06", minute="50",
        pool_participation=True,
        screenshot_dir=tmp_path / "logs",
        check_stop=always_stop,
    )
    assert outcome.result == "failed"
    assert "不足" in outcome.reason


# ── 停止の再確認: check_stopがTrueならその場で押さずcancelled ────

def test_submit_cancelled_before_confirm(monkeypatch, tmp_path):
    url = write_fixture(tmp_path, "form.html", build_form_html(pool_style="text"))
    patch_login_to_fixture(monkeypatch, url)

    outcome = sender.submit(
        email="dummy@example.invalid", password="dummy",
        temp_str="36.7", hour="06", minute="50",
        pool_participation=True,
        screenshot_dir=tmp_path / "logs",
        check_stop=always_stop,  # 最初の呼び出し（確認するを押す前）で停止
    )
    assert outcome.result == "cancelled"
    assert "確認する" in outcome.reason


def test_submit_cancelled_before_final_send(monkeypatch, tmp_path):
    url = write_fixture(tmp_path, "form.html", build_form_html(pool_style="text"))
    patch_login_to_fixture(monkeypatch, url)

    outcome = sender.submit(
        email="dummy@example.invalid", password="dummy",
        temp_str="36.7", hour="06", minute="50",
        pool_participation=True,
        screenshot_dir=tmp_path / "logs",
        check_stop=stop_after(2),  # 1回目(確認する前)は続行、2回目(送信前)で停止
    )
    assert outcome.result == "cancelled"
    assert "送信" in outcome.reason


# ── 10. 通信失敗はsuccessにならない・押した後の異常はunknown ────

def test_submit_login_exception_before_commit_is_failed(monkeypatch, tmp_path):
    def raising_login(page, email, password):
        raise TimeoutError("simulated network failure")
    monkeypatch.setattr(sender, "login", raising_login)

    outcome = sender.submit(
        email="dummy@example.invalid", password="dummy",
        temp_str="36.7", hour="06", minute="50",
        pool_participation=True,
        screenshot_dir=tmp_path / "logs",
        check_stop=never_stop,
    )
    assert outcome.result == "failed"


def test_submit_exception_after_commit_is_unknown(monkeypatch, tmp_path):
    url = write_fixture(tmp_path, "form.html", build_form_html(pool_style="text"))
    patch_login_to_fixture(monkeypatch, url)

    call_count = {"n": 0}
    real_is_already_sent = sender.is_already_sent

    def flaky_is_already_sent(page):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return real_is_already_sent(page)
        raise RuntimeError("simulated post-commit failure (確認不能)")

    monkeypatch.setattr(sender, "is_already_sent", flaky_is_already_sent)

    outcome = sender.submit(
        email="dummy@example.invalid", password="dummy",
        temp_str="36.7", hour="06", minute="50",
        pool_participation=True,
        screenshot_dir=tmp_path / "logs",
        check_stop=never_stop,
    )
    assert outcome.result == "unknown"
