"""界面主题：按北京时间自动在亮色/暗色之间切换。

规则只有一条——北京时间 08:00 到 17:00 用亮色，其他时间用暗色。

为什么不直接用 ``datetime.now()``：线上跑在 UTC 容器里，用本机小时会让亮色
晚 8 个小时才出现（北京 08:00 时容器还停在 00:00）。所以这里把「现在几点」
统一换算到东八区再判断；中国不实行夏令时，固定 +08:00 就是准的，
不依赖 tzdata（Windows 上默认没有）。
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Optional

# 东八区。用固定偏移而不是 ZoneInfo("Asia/Shanghai")，省掉 tzdata 依赖，
# 对中国（无夏令时）完全等价。
BEIJING = timezone(timedelta(hours=8))

# 亮色时段：[DAY_START_HOUR, DAY_END_HOUR)
DAY_START_HOUR = 8
DAY_END_HOUR = 17

LIGHT = "light"
DARK = "dark"


def beijing_now(moment: Optional[datetime] = None) -> datetime:
    """把任意时刻换算成北京时间。

    - 不给参数：取当前时间（UTC）再换算。
    - 带时区的 datetime：换算到东八区。
    - 不带时区的 datetime：原样返回，视为已经是北京时间。
      （调用方明确给出「几点」时，通常本来就是本地钟点，不该再偏 8 小时。）
    """
    if moment is None:
        return datetime.now(timezone.utc).astimezone(BEIJING)
    if moment.tzinfo is None:
        return moment
    return moment.astimezone(BEIJING)


def theme_for(moment: Optional[datetime] = None) -> str:
    """当前该用哪个主题：``light`` 或 ``dark``。"""
    local = beijing_now(moment)
    if DAY_START_HOUR <= local.hour < DAY_END_HOUR:
        return LIGHT
    return DARK


def beijing_today(moment: Optional[datetime] = None) -> date:
    """北京时间的今天。

    「今天是第几周」这类判断也得按北京算：容器若是 UTC，北京周一早上
    8 点之前它还是周日，会把周次算少一周。
    """
    return beijing_now(moment).date()


def seconds_until_next_switch(moment: Optional[datetime] = None) -> int:
    """距离下一次亮/暗切换还有多少秒。

    页面开着不动时要自己换主题。前端如果拿设备时钟倒计时，用户手机时间不准
    就会换错；所以让服务端一次算好，页面只管等这么多秒再翻。
    """
    local = beijing_now(moment)
    if local.tzinfo is None:
        local = local.replace(tzinfo=BEIJING)

    today = local.date()
    upcoming = []
    for day_offset in (0, 1):
        day = today + timedelta(days=day_offset)
        for hour in (DAY_START_HOUR, DAY_END_HOUR):
            candidate = datetime.combine(day, time(hour), tzinfo=BEIJING)
            if candidate > local:
                upcoming.append(candidate)

    if not upcoming:
        # 理论上不会发生（明天一定还有两个边界），兜底一小时后再看
        return 3600
    return max(1, int((min(upcoming) - local).total_seconds()))
