"""超级管理员操作：改归属、停用/恢复、改角色、部门视图。

这些动作的影响面比部门管理员的审批大得多，所以集中在一处，并且每条都
只由 `/admin/users/*` 路由调用（超管专属 + CSRF 校验）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session as OrmSession

from app.models import Assignment, Role, Status, User, utcnow


class AdminOpError(Exception):
    """操作被规则拒绝（而不是程序出错）。"""


@dataclass
class DepartmentRow:
    admin: User
    member_count: int
    pending_count: int
    last_scheduled_at: Optional[datetime]


def all_admins(db: OrmSession) -> List[User]:
    """所有"有部门"的管理者：普通管理员 + 超管（超管也管一个部门）。"""
    return list(
        db.scalars(
            select(User)
            .where(User.role.in_((Role.ADMIN.value, Role.SUPER_ADMIN.value)))
            .order_by(User.student_no)
        ).all()
    )


def department_rows(db: OrmSession) -> List[DepartmentRow]:
    rows: List[DepartmentRow] = []
    for admin in all_admins(db):
        members = list(db.scalars(select(User).where(User.owner_admin_id == admin.id)).all())
        last = db.scalar(
            select(func.max(Assignment.created_at)).where(Assignment.admin_id == admin.id)
        )
        rows.append(
            DepartmentRow(
                admin=admin,
                member_count=len([m for m in members if m.status == Status.APPROVED.value]),
                pending_count=len([m for m in members if m.status == Status.PENDING.value]),
                last_scheduled_at=last,
            )
        )
    return rows


def _target(db: OrmSession, user_id: int) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise AdminOpError("没有这个账号")
    return user


def reassign_member(db: OrmSession, user_id: int, new_admin_id: int) -> User:
    """把一个用户改到另一个管理员名下。"""
    user = _target(db, user_id)
    new_admin = _target(db, new_admin_id)
    if new_admin.role != Role.ADMIN.value:
        raise AdminOpError("目标账号不是管理员")
    if user.role == Role.SUPER_ADMIN.value:
        raise AdminOpError("超级管理员不属于任何部门")
    if user.role == Role.TEACHER.value:
        raise AdminOpError("老师账号不归属任何部门，代管关系在部署配置里")
    if user.id == new_admin.id:
        raise AdminOpError("不能把管理员挂到自己名下")
    user.owner_admin_id = new_admin.id
    db.commit()
    return user


def set_disabled(db: OrmSession, user_id: int, disabled: bool) -> User:
    user = _target(db, user_id)
    if user.role == Role.SUPER_ADMIN.value:
        raise AdminOpError("不能停用超级管理员")
    if user.role == Role.TEACHER.value:
        # 老师是交接保险丝：页面上一律不许停，移除只能改部署配置并重启
        raise AdminOpError("老师账号不能在页面停用（在部署配置里移除并重启）")
    user.disabled_at = utcnow() if disabled else None
    db.commit()
    return user


def set_role(db: OrmSession, user_id: int, role: str) -> User:
    """在 user / admin / teacher 之间切换，用来指定或撤销"谁可以管理"。"""
    if role not in (Role.USER.value, Role.ADMIN.value, Role.TEACHER.value):
        raise AdminOpError("只能切换成普通用户、管理员或老师")
    user = _target(db, user_id)
    if user.role == Role.SUPER_ADMIN.value:
        raise AdminOpError("超级管理员的角色不能改")

    user.role = role
    if role in (Role.ADMIN.value, Role.TEACHER.value):
        # 升级成管理员/老师就不再挂在别人名下，并且必须是通过状态才能进后台
        user.owner_admin_id = None
        if user.status != Status.APPROVED.value:
            user.status = Status.APPROVED.value
            user.approved_at = utcnow()
    else:
        user.owner_admin_id = None
    db.commit()
    return user
