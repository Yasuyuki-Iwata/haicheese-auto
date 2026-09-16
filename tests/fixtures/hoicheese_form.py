"""sender.py のテスト用ローカルfixture HTML生成。

本物のnote.hoi-sys.comへは一切アクセスせず、Playwrightにこのfixtureを読ませて
sender.submit()・sender.find_pool_select()の分岐だけを検証する。
"""

from __future__ import annotations

from typing import Optional


def temp_options_html(step_values):
    return "".join(f'<option value="{v}">{v}</option>' for v in step_values)


def default_temp_values():
    values = []
    v = 350
    while v <= 420:
        values.append(f"{v / 10:.1f}")
        v += 1
    return values


def default_hour_values():
    return [f"{h:02d}" for h in range(24)]


def default_min_values():
    return ["--"] + [f"{m:02d}" for m in range(0, 60, 5)]


def build_options(values):
    return "".join(f'<option value="{v}">{v}</option>' for v in values)


def build_form_html(
    *,
    num_dummy_selects: int = 5,
    include_main_selects: bool = True,
    temp_values: Optional[list] = None,
    hour_values: Optional[list] = None,
    min_values: Optional[list] = None,
    pool_style: str = "text",  # "text" | "value" | "none" | "irrelevant"
    initial_sent: bool = False,
) -> str:
    """sender.submit()が期待するDOM構造を持つ最小限のローカルHTMLを組み立てる。

    select の並び順は sender.IDX_TEMP=5, IDX_HOUR=6, IDX_MIN=7 に合わせて
    num_dummy_selects個のダミーselectのあとに 体温・時・分 を置く。
    プール欄はさらにその後ろに置く（存在する場合）。
    """
    temp_values = temp_values if temp_values is not None else default_temp_values()
    hour_values = hour_values if hour_values is not None else default_hour_values()
    min_values = min_values if min_values is not None else default_min_values()

    dummy_selects = "".join(
        f'<select id="dummy-{i}"><option value="x">x</option></select>' for i in range(num_dummy_selects)
    )

    main_selects = ""
    if include_main_selects:
        main_selects = (
            f'<select id="temp-select">{build_options(temp_values)}</select>'
            f'<select id="hour-select">{build_options(hour_values)}</select>'
            f'<select id="min-select">{build_options(min_values)}</select>'
        )

    pool_html = ""
    if pool_style == "text":
        pool_html = (
            '<select id="pool-select">'
            '<option value="1">参加</option>'
            '<option value="0">不参加</option>'
            "</select>"
        )
    elif pool_style == "value":
        pool_html = (
            '<select id="pool-select">'
            '<option value="true">true</option>'
            '<option value="false">false</option>'
            "</select>"
        )
    elif pool_style == "irrelevant":
        pool_html = (
            '<select id="irrelevant-select">'
            '<option value="a">その他A</option>'
            '<option value="b">その他B</option>'
            "</select>"
        )
    elif pool_style == "none":
        pool_html = ""
    else:
        raise ValueError(pool_style)

    # 「送信済み」ボタンは、is_already_sent()がPlaywrightのlocator().count()で
    # 可視性を問わず数える点に注意し、送信済みでないときはDOMに存在させない
    # （display:noneで隠すだけだと非表示でもcount()に含まれてしまう）。
    already_sent_html = '<div id="already-sent-view"><button type="button">送信済み</button></div>' \
        if initial_sent else '<div id="already-sent-view"></div>'
    form_html = "" if initial_sent else f"""
  {dummy_selects}
  {main_selects}
  {pool_html}
  <button type="button" id="confirm-btn">確認する</button>
  <div id="confirm-view"></div>
"""

    return f"""<!doctype html>
<html lang="ja"><head><meta charset="utf-8"></head>
<body>
{already_sent_html}
<div id="form-view">{form_html}</div>
<script>
(function() {{
  if (location.search.indexOf('sent=1') !== -1) {{
    document.getElementById('form-view').innerHTML = '';
    document.getElementById('already-sent-view').innerHTML =
      '<button type="button">送信済み</button>';
    return;
  }}
  var confirmBtn = document.getElementById('confirm-btn');
  if (confirmBtn) {{
    confirmBtn.onclick = function() {{
      var confirmView = document.getElementById('confirm-view');
      confirmView.innerHTML = '<button type="button" id="send-btn">送信</button>';
      document.getElementById('send-btn').onclick = function() {{
        location.href = location.pathname + '?sent=1';
      }};
    }};
  }}
}})();
</script>
</body></html>
"""


def write_fixture(tmp_path, name: str, html: str) -> str:
    """一時ディレクトリへHTMLファイルを書き出し、file://のURLを返す。"""
    path = tmp_path / name
    path.write_text(html, encoding="utf-8")
    return path.as_uri()
