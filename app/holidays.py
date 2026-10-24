"""法定节假日 / 调休查询。

周日排除是排班器的硬规则，不需要日历；这里只负责周一~周五的法定节假日
和调休判定。判定结果按日期缓存，同一周反复查询只查一次。
"""

from __future__ import annotations

from datetime import date
from functools import lru_cache
from typing import Optional

try:
    import chinese_calendar
except ImportError:  # 包没装时排班照常工作，只是不过滤节假日
    chinese_calendar = None


#: chinese_calendar 返回英文节日名，界面上要显示中文
_HOLIDAY_CN = {
    "New Year's Day": "元旦",
    "Spring Festival": "春节",
    "Tomb-sweeping Day": "清明节",
    "Labour Day": "劳动节",
    "Dragon Boat Festival": "端午节",
    "Mid-autumn Festival": "中秋节",
    "National Day": "国庆节",
}


@lru_cache(maxsize=512)
def _lookup(day: date) -> tuple[bool, Optional[str]]:
    """返回 (是否不上班, 原因)。chinesecalendar 返回 (is_holiday, name)。"""
    if chinese_calendar is None:
        return False, None
    try:
        is_holiday, name = chinese_calendar.get_holiday_detail(day)
    except Exception:
        return False, None
    if name is None:
        return bool(is_holiday), None
    return bool(is_holiday), _HOLIDAY_CN.get(name, name)


def is_workday(day: date) -> bool:
    """周一~周五的法定节假日返回 False；调休上班的周末返回 True。"""
    off, _name = _lookup(day)
    return not off


def holiday_name(day: date) -> Optional[str]:
    return _lookup(day)[1]


def week_days(semester_start: date, week_no: int) -> dict[str, date]:
    """第 week_no 周周一到周日的公历日期（semester_start 是第 1 周周一）。"""
    from datetime import timedelta

    monday = semester_start + timedelta(weeks=week_no - 1)
    names = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")
    return {name: monday + timedelta(days=i) for i, name in enumerate(names)}
