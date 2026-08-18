"""验证 Web 层的"相对时间 / 是否算 NEW"辅助函数，这两者是即时性在界面上的直接体现。"""
from datetime import datetime, timedelta, timezone

from app.web.routes import _is_recent, _relative_time


def test_relative_time_just_now():
    assert _relative_time(datetime.now(timezone.utc)) == "刚刚"


def test_relative_time_minutes_ago():
    dt = datetime.now(timezone.utc) - timedelta(minutes=5)
    assert _relative_time(dt) == "5分钟前"


def test_relative_time_hours_ago():
    dt = datetime.now(timezone.utc) - timedelta(hours=3)
    assert _relative_time(dt) == "3小时前"


def test_relative_time_handles_naive_datetime():
    # SQLite 存取的时间可能不带时区信息，函数需要能兼容处理
    dt = datetime.utcnow() - timedelta(minutes=2)
    assert _relative_time(dt) == "2分钟前"


def test_is_recent_true_within_threshold():
    dt = datetime.now(timezone.utc) - timedelta(seconds=60)
    assert _is_recent(dt, threshold_seconds=300) is True


def test_is_recent_false_outside_threshold():
    dt = datetime.now(timezone.utc) - timedelta(seconds=600)
    assert _is_recent(dt, threshold_seconds=300) is False
