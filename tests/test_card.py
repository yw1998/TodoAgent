from datetime import datetime

from app.agent.tools import _fmt
from app.channels.feishu import build_card
from app.scheduler.reminder import fmt_local


def test_build_card_shape():
    card = build_card("📋 TodoAgent", "**买菜**\n时间：10:00")
    assert card["header"]["title"]["content"] == "📋 TodoAgent"
    assert card["header"]["template"] == "blue"
    assert card["elements"][0]["tag"] == "markdown"
    assert "买菜" in card["elements"][0]["content"]


def test_local_time_format():
    utc = datetime(2026, 10, 17, 2, 0)  # 10:00 Asia/Shanghai
    assert _fmt(utc) == "2026-10-17 10:00"
    assert fmt_local(utc).startswith("2026-10-17 10:00")
