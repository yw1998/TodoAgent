import json
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


def test_markdown_table_converted_to_native_table():
    md = (
        "我先把今天的状态列出来：\n"
        "| 编号 | 事项 | 状态 |\n"
        "|---|---|---|\n"
        "| #3 | 出门逛街 | 已完成 |\n"
        "| #5 | 洗碗 | 已完成 |\n"
        "确认后告诉我。"
    )
    els = build_card("t", md)["elements"]
    assert els[0]["tag"] == "markdown" and "状态列出来" in els[0]["content"]
    table = els[1]
    assert table["tag"] == "table" and len(table["rows"]) == 2
    assert [c["display_name"] for c in table["columns"]] == ["编号", "事项", "状态"]
    assert table["rows"][0]["c1"] == "出门逛街"
    assert els[2]["tag"] == "markdown" and "确认后" in els[2]["content"]
    assert "|---" not in json.dumps(els, ensure_ascii=False)


def test_table_alignment_colon_and_spacing():
    md = "| A | B |\n| :-- | --: |\n| 1 | 2 |"
    els = build_card("t", md)["elements"]
    assert els[0]["tag"] == "table"
    assert els[0]["rows"][0] == {"c0": "1", "c1": "2"}


def test_non_table_pipes_not_converted():
    md = "回复「完成 | 延后」即可"
    els = build_card("t", md)["elements"]
    assert len(els) == 1 and els[0]["tag"] == "markdown"
