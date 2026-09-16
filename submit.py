#!/usr/bin/env python3
"""旧・直接実行スクリプト。

送信処理は sender.py に移し、判定・排他制御・記録は runner.py が行うようになった。
このファイルを直接実行しても送信しない。旧cronの行を誤って有効化しても
二重送信・設定の取りこぼしが起きないようにするための安全弁。
"""

import sys

if __name__ == "__main__":
    print("submit.py の直接実行はできません。runner.py を使ってください。")
    sys.exit(1)
