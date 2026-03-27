"""ターミナルUIパッケージ — UTF-8対応コンソール"""
import sys
import io
from rich.console import Console

# Windows cp932環境でもUTF-8で出力する
if sys.platform == "win32":
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    except AttributeError:
        pass

shared_console = Console(highlight=False)
