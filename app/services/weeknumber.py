"""周次口径：全局锚点优先，自然周结构永不变。

学校的教学周和自然周（周一~周日）天然同步。周次校正只平移编号：
锚点 = （某自然周的周一, 该周的周次号），任何日期的周次 = 锚点周次 +
两个周一之间的整周差，绝无周中起算。没校正过时退回按学期起始日期推算。

日期标签（"本周 = 几号到几号"）始终显示该自然周真实的周一~周日，
与编号天然一致。
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Optional

from sqlalchemy.orm import Session as OrmSession

from app.config import Settings
from app.models import GlobalSetting
from app.theme import beijing_today

#: 全局配置键：周次锚点 JSON {"monday": "YYYY-MM-DD", "week": N}
ANCHOR_KEY = "week_number_anchor"

MAX_WEEK = 30


def monday_of(day: date) -> date:
    """这一天所在自然周的周一。"""
    return day - timedelta(days=day.weekday())


def week_for(day: date, anchor_monday: date, anchor_week: int) -> int:
    """按锚点算某天的周次：整周差 + 锚点周次（可为负/超学期，调用方负责夹取）。"""
    delta_days = (monday_of(day) - monday_of(anchor_monday)).days
    return anchor_week + delta_days // 7


def anchor_of(db: OrmSession) -> Optional[tuple[date, int]]:
    """读全局锚点；没校正过返回 None。"""
    row = db.get(GlobalSetting, ANCHOR_KEY)
    if row is None or not row.value:
        return None
    try:
        data = json.loads(row.value)
        return date.fromisoformat(data["monday"]), int(data["week"])
    except (ValueError, TypeError, KeyError):
        return None   # 坏数据当没校正过，回退默认口径


def save_anchor(db: OrmSession, this_week_is: int, today: Optional[date] = None) -> date:
    """校正：把今天所在自然周指定为第 N 周。返回锚点周一。"""
    today = today or beijing_today()
    anchor_monday = monday_of(today)
    row = db.get(GlobalSetting, ANCHOR_KEY)
    payload = json.dumps({"monday": anchor_monday.isoformat(), "week": this_week_is},
                         ensure_ascii=False)
    if row is None:
        db.add(GlobalSetting(key=ANCHOR_KEY, value=payload))
    else:
        row.value = payload
    db.commit()
    return anchor_monday


def effective_semester_start(db: OrmSession, base_start: date) -> date:
    """锚点在时把学期起始平移到锚点口径（锚周一 − (锚周次−1) 周）。

    week_days / week_calendar / 节假日过滤都拿"学期起始"推第 N 周的日期，
    包一层这个函数后它们自动跟随校正：编号 N ↔ 日期 = 锚点周 ± 整周差，
    与 current_week 的编号口径严格一致，不会出现"编号对了日期还是旧的"。
    """
    anchor = anchor_of(db)
    if anchor is None:
        return base_start
    anchor_monday, anchor_week = anchor
    return anchor_monday - timedelta(weeks=anchor_week - 1)


def current_week(settings: Settings, db: OrmSession,
                 semester_start: Optional[date] = None,
                 today: Optional[date] = None) -> int:
    """今天（北京时间）是第几周：全局锚点优先，其次学期起始推算。

    这是全站唯一的周次口径入口——/me、排班页、总览都走它，
    保证所有人看到的"本周"一致。
    """
    day = today or beijing_today()
    anchor = anchor_of(db)
    if anchor is not None:
        week = week_for(day, anchor[0], anchor[1])
    else:
        start = semester_start or settings.semester_start
        anchor_week = 1
        # 学期起始可能不是周一：按其所在自然周的周一起算整周差
        week = week_for(day, start, anchor_week)
    return max(1, min(MAX_WEEK, week))
