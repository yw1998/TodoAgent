"""进程内共享状态（单人）：最近一次被提醒的待办。"""

import time

_last_reminded: tuple[int, float] | None = None


def set_last_reminded(todo_id: int) -> None:
    global _last_reminded
    _last_reminded = (todo_id, time.time())


def get_last_reminded(ttl_minutes: int = 30) -> int | None:
    if _last_reminded and time.time() - _last_reminded[1] < ttl_minutes * 60:
        return _last_reminded[0]
    return None


def clear_if_matches(todo_id: int) -> None:
    global _last_reminded
    if _last_reminded and _last_reminded[0] == todo_id:
        _last_reminded = None


def clear_last_reminded() -> None:
    global _last_reminded
    _last_reminded = None
