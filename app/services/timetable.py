"""课表缓存：登录时写入，页面按周读取。视图永远只碰自己的行。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import List, Optional

from sqlalchemy import delete, select
from sqlalchemy.orm import Session as OrmSession

from app.config import Settings
from app.jiaowu import DAYS, SLOTS, ParseResult, week_matches
from app.models import Course, User
from app.theme import beijing_today


def store_courses(db: OrmSession, user: User, semester: str, parse: ParseResult) -> int:
    """整体替换该用户该学期的课表缓存（每次登录刷新一次）。"""
    db.execute(delete(Course).where(Course.user_id == user.id, Course.semester == semester))
    for raw in parse.courses:
        db.add(
            Course(
                user_id=user.id,
                semester=semester,
                name=raw.name,
                teacher=raw.teacher,
                room=raw.room,
                day=raw.day,
                slot=raw.slot,
                weeks=raw.weeks,
            )
        )
    db.commit()
    return len(parse.courses)


def load_courses(db: OrmSession, user: User, semester: str, week: int) -> List[Course]:
    """该用户该周的课程（只查 user_id == 自己，作用域在查询层）。"""
    rows = db.scalars(
        select(Course).where(Course.user_id == user.id, Course.semester == semester)
    ).all()
    return [row for row in rows if week_matches(week, row.weeks)]


def is_online(course: Course) -> bool:
    """教室为「线上」的课不算占用：人不用到教室，值班和网课互不影响。"""
    return "线上" in (course.room or "")


def slot_has_offline_course(db: OrmSession, user: User, semester: str, week: int,
                            day: str, slot: str) -> bool:
    """这个时段有没有「真要去教室」的课。线上课不算（线上课时段可排班、可请假）。"""
    return any(
        c.day == day and c.slot == slot and not is_online(c)
        for c in load_courses(db, user, semester, week)
    )


def current_week(settings: Settings, db=None, today: Optional[date] = None,
                 semester_start: Optional[date] = None) -> int:
    """今天是第几周。

    全站唯一口径：有数据库会话时走 services.weeknumber（全局校正锚点优先），
    保证 /me、排班页、总览看到的"本周"完全一致。没有会话的纯计算场景
    （本地脚本）退回按学期起始推算——同样按周一整周对齐，与锚点口径
    的数学一致，只是看不到校正。
    """
    from app.services import weeknumber

    day = today or beijing_today()
    if db is not None:
        return weeknumber.current_week(settings, db, semester_start=semester_start,
                                       today=day)
    start = semester_start or settings.semester_start
    week = weeknumber.week_for(day, start, 1)
    return max(1, min(weeknumber.MAX_WEEK, week))


@dataclass
class GridCell:
    day: str
    slot: str
    courses: List[Course] = field(default_factory=list)
    on_leave: bool = False

    @property
    def offline_courses(self) -> List[Course]:
        return [c for c in self.courses if not is_online(c)]

    @property
    def online_courses(self) -> List[Course]:
        return [c for c in self.courses if is_online(c)]

    @property
    def busy(self) -> bool:
        """有线下课才算占用；线上课的格子可排班、可请假。"""
        return bool(self.offline_courses)

    @property
    def online(self) -> bool:
        """没线下课但有线上课：照常上课，同时可能被排了值班。"""
        return not self.busy and bool(self.online_courses)

    @property
    def can_leave(self) -> bool:
        """只有课余时间能请假：线下课时间本来就排不了班。"""
        return not self.busy

    @property
    def label(self) -> str:
        if not self.courses:
            return ""
        return " / ".join(c.name for c in self.courses)

    @property
    def rooms(self) -> str:
        rooms = [c.room for c in self.courses if c.room]
        return " / ".join(dict.fromkeys(rooms))


def build_grid(db: OrmSession, user: User, semester: str, week: int,
               leaves: set[tuple[str, str]] | None = None) -> List[List[GridCell]]:
    """行=节次（6 段），列=星期（周一到周日）。"""
    leaves = leaves or set()
    courses = load_courses(db, user, semester, week)
    grid: List[List[GridCell]] = []
    for slot in SLOTS:
        row: List[GridCell] = []
        for day in DAYS:
            cell = GridCell(day=day, slot=slot,
                            courses=[c for c in courses if c.day == day and c.slot == slot],
                            on_leave=(day, slot) in leaves)
            row.append(cell)
        grid.append(row)
    return grid
