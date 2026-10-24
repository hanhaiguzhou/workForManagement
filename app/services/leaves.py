"""请假：颗粒度是「一节课」，作用域是「一周」。"""

from __future__ import annotations

from typing import Iterable, Set, Tuple

from sqlalchemy import delete, select
from sqlalchemy.orm import Session as OrmSession

from app.models import Leave, User

Slot = Tuple[str, str]


def week_leaves(db: OrmSession, user: User, semester: str, week_no: int) -> Set[Slot]:
    rows = db.scalars(
        select(Leave).where(
            Leave.user_id == user.id,
            Leave.semester == semester,
            Leave.week_no == week_no,
        )
    ).all()
    return {(row.day, row.slot) for row in rows}


def is_on_leave(db: OrmSession, user: User, semester: str, week_no: int, day: str, slot: str) -> bool:
    row = db.scalar(
        select(Leave).where(
            Leave.user_id == user.id,
            Leave.semester == semester,
            Leave.week_no == week_no,
            Leave.day == day,
            Leave.slot == slot,
        )
    )
    return row is not None


def toggle_leave(db: OrmSession, user: User, semester: str, week_no: int, day: str, slot: str) -> bool:
    """点一下请假、再点一下取消。返回操作后是否处于请假状态。"""
    existing = db.scalar(
        select(Leave).where(
            Leave.user_id == user.id,
            Leave.semester == semester,
            Leave.week_no == week_no,
            Leave.day == day,
            Leave.slot == slot,
        )
    )
    if existing is not None:
        db.delete(existing)
        db.commit()
        return False
    db.add(Leave(user_id=user.id, semester=semester, week_no=week_no, day=day, slot=slot))
    db.commit()
    return True


def clear_week(db: OrmSession, user: User, semester: str, week_no: int) -> int:
    rows = db.scalars(
        select(Leave).where(
            Leave.user_id == user.id,
            Leave.semester == semester,
            Leave.week_no == week_no,
        )
    ).all()
    for row in rows:
        db.delete(row)
    db.commit()
    return len(rows)
