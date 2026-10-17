"""进程内共享状态：每个用户最近一次被提醒的待办。"""

import time

_last_reminded: dict[int, tuple[int, float]] = {}


def set_last_reminded(user_id: int, todo_id: int) -> None:
    _last_reminded[user_id] = (todo_id, time.time())


def get_last_reminded(user_id: int, ttl_minutes: int = 30) -> int | None:
    entry = _last_reminded.get(user_id)
    if entry and time.time() - entry[1] < ttl_minutes * 60:
        return entry[0]
    return None


def clear_if_matches(user_id: int, todo_id: int) -> None:
    entry = _last_reminded.get(user_id)
    if entry and entry[0] == todo_id:
        _last_reminded.pop(user_id, None)
