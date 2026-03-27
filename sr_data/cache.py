"""TTLインメモリキャッシュ（重複API呼び出しを防ぐ）"""
import time
from typing import Any, Optional

_store: dict[str, tuple[float, Any]] = {}
_DEFAULT_TTL = 60  # 秒


def get(key: str) -> Optional[Any]:
    if key in _store:
        ts, val = _store[key]
        if time.time() - ts < _DEFAULT_TTL:
            return val
        del _store[key]
    return None


def set(key: str, value: Any, ttl: int = _DEFAULT_TTL):
    _store[key] = (time.time(), value)


def clear():
    _store.clear()
