from src.ai_handler import _build_notification_content, _get_notification_status_label


def test_get_notification_status_label():
    assert _get_notification_status_label(True) == "推荐"
    assert _get_notification_status_label(False) == "不推荐"
    assert _get_notification_status_label(None) == "通知"


def test_build_notification_content_for_recommended_item():
    title, message = _build_notification_content(
        {
            "商品标题": "MacBook Air M1",
            "当前售价": "3999",
            "商品链接": "https://www.goofish.com/item?id=123",
        },
        "成色不错",
        is_recommended=True,
    )

    assert "推荐" in title
    assert "结论: 推荐" in message
    assert "原因: 成色不错" in message


def test_build_notification_content_for_not_recommended_item():
    title, message = _build_notification_content(
        {
            "商品标题": "MacBook Air M1",
            "当前售价": "3999",
            "商品链接": "https://www.goofish.com/item?id=123",
        },
        "电池健康不足",
        is_recommended=False,
    )

    assert "不推荐" in title
    assert "结论: 不推荐" in message
    assert "原因: 电池健康不足" in message
