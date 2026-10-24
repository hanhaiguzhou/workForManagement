"""部门作用域：一个管理员 + 他名下的成员。所有越权都在这里拦。"""

from __future__ import annotations

from typing import List

from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from app.models import Role, Status, User, utcnow


class OutOfScope(PermissionError):
    """试图操作不属于自己部门的账号。"""


def members_of(db: OrmSession, admin: User) -> List[User]:
    return list(
        db.scalars(
            select(User)
            .where(User.owner_admin_id == admin.id)
            .order_by(User.status, User.student_no)
        ).all()
    )


def pending_of(db: OrmSession, admin: User) -> List[User]:
    return [u for u in members_of(db, admin) if u.status == Status.PENDING.value]


def _member_or_raise(db: OrmSession, admin: User, user_id: int) -> User:
    user = db.get(User, user_id)
    if user is None or user.owner_admin_id != admin.id:
        raise OutOfScope("这个账号不在你的部门里")
    return user


def approve_member(db: OrmSession, admin: User, user_id: int) -> User:
    user = _member_or_raise(db, admin, user_id)
    user.status = Status.APPROVED.value
    user.approved_at = utcnow()
    db.commit()
    return user


def reject_member(db: OrmSession, admin: User, user_id: int) -> User:
    user = _member_or_raise(db, admin, user_id)
    user.status = Status.REJECTED.value
    db.commit()
    return user


# ---------------------------------------------------------------- 超管审批管理员


def pending_admin_candidates(db: OrmSession) -> List[User]:
    return list(
        db.scalars(
            select(User)
            .where(User.role == Role.ADMIN.value, User.status == Status.PENDING.value)
            .order_by(User.created_at)
        ).all()
    )


def approve_admin_candidate(db: OrmSession, user_id: int) -> User:
    user = db.get(User, user_id)
    if user is None or user.role != Role.ADMIN.value:
        raise OutOfScope("没有这个待审的管理员申请")
    user.status = Status.APPROVED.value
    user.approved_at = utcnow()
    db.commit()
    return user


def reject_admin_candidate(db: OrmSession, user_id: int) -> User:
    user = db.get(User, user_id)
    if user is None or user.role != Role.ADMIN.value:
        raise OutOfScope("没有这个待审的管理员申请")
    user.status = Status.REJECTED.value
    db.commit()
    return user


def all_users(db: OrmSession) -> List[User]:
    return list(db.scalars(select(User).order_by(User.role, User.student_no)).all())
