"""排班服务：把「课表 ∪ 请假」变成排班器的输入，并把结果存回数据库。

这一层只做数据搬运与持久化，不做任何冲突判断——冲突判断全部交给
`app.scheduler`（纯函数、无 I/O、不可行时明说）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from sqlalchemy import delete, select
from sqlalchemy.orm import Session as OrmSession

from app.config import Settings
from app.holidays import holiday_name, is_workday, week_days
from app.models import Assignment, Role, ScheduleSettings, Status, User
from app.scheduler import RESERVED_SLOT, SLOTS, Infeasible, Plan, Request, assign
from app.services import departments, weeknumber
from app.services import leaves as leave_service
from app.services import timetable as timetable_service
from app.xlsx import fillable_slots

Slot = Tuple[str, str]

WEEKDAY_NAMES = ("周一", "周二", "周三", "周四", "周五")


@dataclass
class WeekCalendar:
    """某一周的日历事实：起止日期 + 落在工作日的法定节假日。"""

    week: int
    monday: date
    sunday: date
    holidays: List[dict] = field(default_factory=list)

    @property
    def label(self) -> str:
        return f"{self.monday.strftime('%Y-%m-%d')} ~ {self.sunday.strftime('%m-%d')}"

    def skipped_slots(self, slot_keys) -> List[dict]:
        """哪些班次格因为放假被跳过（只列模板里真有的格子）。"""
        out: List[dict] = []
        for item in self.holidays:
            for slot in SLOTS:
                if (item["day"], slot) in slot_keys:
                    out.append({**item, "slot": slot})
        return out


def week_calendar(semester_start: date, week: int) -> WeekCalendar:
    days = week_days(semester_start, week)
    holidays: List[dict] = []
    for name in WEEKDAY_NAMES:
        day = days[name]
        if not is_workday(day):
            holidays.append(
                {"day": name, "date": day, "name": holiday_name(day) or "法定假日"}
            )
    return WeekCalendar(week=week, monday=days["周一"], sunday=days["周日"], holidays=holidays)


def occupied_slots(db: OrmSession, user: User, semester: str, week: int) -> Set[Slot]:
    """占用 = 线下课 ∪ 请假。线上课不算占用（人不用到教室），请假的人仍然算。"""
    courses = timetable_service.load_courses(db, user, semester, week)
    offline = {(c.day, c.slot) for c in courses if not timetable_service.is_online(c)}
    return offline | leave_service.week_leaves(db, user, semester, week)


def department_settings(db: OrmSession, admin: User, default_max: int = 2) -> ScheduleSettings:
    row = db.get(ScheduleSettings, admin.id)
    if row is None:
        row = ScheduleSettings(admin_id=admin.id, max_per_person=default_max, blacklist_ids="")
        db.add(row)
        db.commit()
    return row


def update_settings(db: OrmSession, admin: User, *, max_per_person: Optional[int] = None,
                    special_person_id: Optional[int] = None,
                    blacklist_ids: Optional[str] = None,
                    semester_start: Optional[date] = None,
                    template_path: Optional[str] = None,
                    template_texts: Optional[str] = None) -> ScheduleSettings:
    row = department_settings(db, admin)
    if max_per_person is not None:
        row.max_per_person = max(0, min(20, max_per_person))
    if special_person_id is not None:
        row.special_person_id = special_person_id or None
    if blacklist_ids is not None:
        row.blacklist_ids = blacklist_ids.strip()[:256]
    if semester_start is not None:
        row.semester_start = semester_start
    if template_path is not None:
        row.template_path = template_path[:512]
    if template_texts is not None:
        row.template_texts = template_texts
    db.commit()
    return row


def department_template(db: OrmSession, admin: User, app_settings: Settings) -> Path:
    """该部门当前该用的模板：部门自定义优先，取不到就退回全局默认。"""
    row = department_settings(db, admin, app_settings.max_per_person)
    if row.template_path:
        candidate = Path(row.template_path)
        if candidate.exists():
            return candidate
    return app_settings.template_path


def template_texts_of(row: ScheduleSettings) -> Dict[str, str]:
    """读出该部门在线改过的模板文案。"""
    raw = row.template_texts or ""
    if not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): str(v) for k, v in data.items() if isinstance(v, (str, int, float))}


def save_template_texts(db: OrmSession, admin: User, texts: Dict[str, str]) -> ScheduleSettings:
    from app.xlsx import TEXT_FIELD_MAX_LEN

    cleaned = {
        key: str(value).strip()[:TEXT_FIELD_MAX_LEN]
        for key, value in texts.items()
        if str(value or "").strip()
    }
    return update_settings(db, admin, template_texts=json.dumps(cleaned, ensure_ascii=False))


def _blacklist(row: ScheduleSettings) -> Set[str]:
    return {part.strip() for part in (row.blacklist_ids or "").split(",") if part.strip()}


def schedulable_members(db: OrmSession, admin: User) -> List[User]:
    """本部门可排人员 = 已审批成员 + 管理员本人。

    现实里管勤工助学的同学自己也在值班表上（老表里就有），所以管理员算一个人，
    而不是只当"发号施令"的角色。
    老师角色是交接保险丝，永不参与排班——这里独立于路由层的代管映射再挡一道，
    不依赖调用方记得先做 acting_admin 转换。
    """
    members = [m for m in departments.members_of(db, admin) if m.status == Status.APPROVED.value]
    if (admin.role != Role.TEACHER.value
            and admin.status == Status.APPROVED.value and not admin.is_disabled):
        members = members + [admin]
    return members


def build_request(db: OrmSession, admin: User, app_settings: Settings, week: int,
                  seed: Optional[int] = None
                  ) -> tuple[Request, List[User], ScheduleSettings, WeekCalendar]:
    row = department_settings(db, admin, app_settings.max_per_person)
    members = schedulable_members(db, admin)

    special = None
    if row.special_person_id:
        candidate = db.get(User, row.special_person_id)
        if candidate is not None and candidate in members:
            special = candidate.student_no

    busy: Dict[str, frozenset[Slot]] = {}
    demand: Dict[str, int] = {}
    for member in members:
        busy[member.student_no] = frozenset(
            occupied_slots(db, member, app_settings.semester, week)
        )
        demand[member.student_no] = 0 if member.student_no == special else row.max_per_person

    # 学期起始日期：本部门优先，其次全局配置；过锚点平移让周历/节假日
    # 跟随周次校正（与 current_week 同一口径）
    semester_start = weeknumber.effective_semester_start(
        db, row.semester_start or app_settings.semester_start)
    calendar = week_calendar(semester_start, week)

    # 法定节假日过滤：只有周一~周五才查日历，周日由 scheduler 硬规则排除
    template_fillable = fillable_slots(department_template(db, admin, app_settings))
    fillable = frozenset(
        slot for slot in template_fillable
        if slot[0] in ("周六", "周日") or is_workday(week_days(semester_start, week)[slot[0]])
    )

    request = Request(
        fillable=fillable,
        busy=busy,
        demand=demand,
        special=special,
        blacklist_1_2=frozenset(_blacklist(row)),
        seed=seed,
    )
    return request, members, row, calendar


def run(db: OrmSession, admin: User, app_settings: Settings, week: int,
        seed: int = 0
        ) -> tuple[Plan | Infeasible, Request, List[User], WeekCalendar]:
    request, members, _row, calendar = build_request(db, admin, app_settings, week, seed)
    return assign(request), request, members, calendar


def save_plan(db: OrmSession, admin: User, semester: str, week: int,
              plan: Plan, members: List[User]) -> int:
    """整体替换该部门该周的排班（重排不会留下旧记录）。"""
    by_no = {m.student_no: m.id for m in members}
    db.execute(
        delete(Assignment).where(
            Assignment.admin_id == admin.id,
            Assignment.semester == semester,
            Assignment.week_no == week,
        )
    )
    count = 0
    for (day, slot), person_no in plan.assignments.items():
        user_id = by_no.get(person_no)
        if user_id is None:
            continue
        db.add(
            Assignment(
                admin_id=admin.id,
                semester=semester,
                week_no=week,
                day=day,
                slot=slot,
                user_id=user_id,
            )
        )
        count += 1
    db.commit()
    return count


def load_plan(db: OrmSession, admin: User, semester: str, week: int) -> Dict[Slot, User]:
    rows = db.scalars(
        select(Assignment).where(
            Assignment.admin_id == admin.id,
            Assignment.semester == semester,
            Assignment.week_no == week,
        )
    ).all()
    out: Dict[Slot, User] = {}
    for row in rows:
        user = db.get(User, row.user_id)
        if user is not None:
            out[(row.day, row.slot)] = user
    return out


def plan_names(db: OrmSession, admin: User, semester: str, week: int) -> Dict[Slot, str]:
    return {key: (user.name or user.student_no) for key, user in load_plan(db, admin, semester, week).items()}


def member_load(db: OrmSession, admin: User, app_settings: Settings, week: int) -> List[dict]:
    """全员总览：每个人在这周的「有课 / 请假 / 被排班」。"""
    duty = plan_names(db, admin, app_settings.semester, week)
    row = department_settings(db, admin, app_settings.max_per_person)
    special_name = None
    if row.special_person_id:
        special_user = db.get(User, row.special_person_id)
        if special_user is not None:
            special_name = special_user.student_no
    rows = []
    for member in schedulable_members(db, admin):
        course_rows = timetable_service.load_courses(db, member, app_settings.semester, week)
        courses = {(c.day, c.slot) for c in course_rows}
        leave = leave_service.week_leaves(db, member, app_settings.semester, week)
        # busy 不含线上课：线上课不算占用，与排班器口径一致；courses 仍含线上课（展示用）
        busy = {(c.day, c.slot) for c in course_rows
                if not timetable_service.is_online(c)} | leave
        # 与排班器口径一致：周日永远不可排；周六和每天的 11-12 节只有常驻人员可排
        is_special = member.student_no == special_name
        days = ("周一", "周二", "周三", "周四", "周五")
        if is_special:
            days = days + ("周六",)
        every_slot = {(day, slot) for day in days for slot in SLOTS}
        if not is_special:
            every_slot -= {(day, RESERVED_SLOT) for day in days}
        rows.append(
            {
                "member": member,
                "courses": courses,
                "leave": leave,
                "busy": busy,
                "free": every_slot - busy,
                "duty": {key for key, name in duty.items()
                         if name == (member.name or member.student_no)},
            }
        )
    return rows
