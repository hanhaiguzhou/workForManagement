"""数据库模型。密码不在此出现——系统永不存储教务密码。"""

from __future__ import annotations

from datetime import date, datetime, timezone
from enum import Enum

from sqlalchemy import Date, DateTime, ForeignKey, Integer, LargeBinary, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Role(str, Enum):
    SUPER_ADMIN = "super_admin"
    ADMIN = "admin"
    TEACHER = "teacher"      # 交接保险丝：超管同权、本地密码登录、不参与排班
    USER = "user"


class Status(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


def utcnow() -> datetime:
    """朴素 UTC 时间，SQLite 下比较不会踩 aware/naive 的坑。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    student_no: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(64), default="")
    role: Mapped[str] = mapped_column(String(16), default=Role.USER.value)
    status: Mapped[str] = mapped_column(String(16), default=Status.PENDING.value)

    # 「部门」= 一个管理员及其名下用户；超管与管理员自身为空
    owner_admin_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # 最近一次抓课表的解析告警（页面要可见，不能让漏课悄悄发生）
    last_parse_notice: Mapped[str] = mapped_column(String(1024), default="")
    # 教务会话 cookie（JSON 序列化），用于免密刷新课表
    jiaowu_cookies: Mapped[str] = mapped_column(String(2048), default="")

    @property
    def is_super_admin(self) -> bool:
        return self.role == Role.SUPER_ADMIN.value

    @property
    def is_admin(self) -> bool:
        """超管兼部门管理员：超管自己也能挂人、排班、看总览。"""
        return self.role in (Role.ADMIN.value, Role.SUPER_ADMIN.value)

    @property
    def is_approved(self) -> bool:
        return self.status == Status.APPROVED.value

    @property
    def is_disabled(self) -> bool:
        return self.disabled_at is not None


class SessionRow(Base):
    """服务端会话。库里只存 token 的 sha256，泄露也拿不到可用凭证。"""

    __tablename__ = "sessions"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    csrf: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Course(Base):
    """最近一次抓取的课表缓存，按 user+semester 覆盖写。"""

    __tablename__ = "courses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    semester: Mapped[str] = mapped_column(String(32), index=True)
    name: Mapped[str] = mapped_column(String(128), default="")
    teacher: Mapped[str] = mapped_column(String(64), default="")
    room: Mapped[str] = mapped_column(String(64), default="")
    day: Mapped[str] = mapped_column(String(8))
    slot: Mapped[str] = mapped_column(String(8))
    weeks: Mapped[str] = mapped_column(String(128), default="")
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Leave(Base):
    """请假：一人一周一日一节一条。按周独立由唯一约束保证。"""

    __tablename__ = "leaves"
    __table_args__ = (
        UniqueConstraint("user_id", "semester", "week_no", "day", "slot", name="uq_leave_one_per_slot"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    semester: Mapped[str] = mapped_column(String(32), index=True)
    week_no: Mapped[int] = mapped_column(Integer, index=True)
    day: Mapped[str] = mapped_column(String(8))
    slot: Mapped[str] = mapped_column(String(8))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Assignment(Base):
    """排班结果：一个部门的某一周，重排时整体替换。"""

    __tablename__ = "assignments"
    __table_args__ = (
        UniqueConstraint("admin_id", "semester", "week_no", "day", "slot", name="uq_assignment_slot"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    admin_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    semester: Mapped[str] = mapped_column(String(32), index=True)
    week_no: Mapped[int] = mapped_column(Integer, index=True)
    day: Mapped[str] = mapped_column(String(8))
    slot: Mapped[str] = mapped_column(String(8))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ScheduleSettings(Base):
    """每个部门（管理员）自己的排班规则。"""

    __tablename__ = "schedule_settings"

    admin_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    max_per_person: Mapped[int] = mapped_column(Integer, default=2)
    special_person_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    blacklist_ids: Mapped[str] = mapped_column(String(256), default="")  # 逗号分隔
    semester: Mapped[str] = mapped_column(String(32), default="")
    semester_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    template_path: Mapped[str] = mapped_column(String(512), default="")
    # 在线改过的模板文案（JSON：{"title": ..., "place": ..., "teacher_name": ...}）
    template_texts: Mapped[str] = mapped_column(Text, default="")


class GlobalSetting(Base):
    """全局配置（键值对）。目前存周次校正锚点；建表走 create_all 自动迁移。"""

    __tablename__ = "global_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")


class ExportArchive(Base):
    """总体导出存档：一键排班成功后该周值班表 Excel 的快照。

    （部门, 学期, 周次）唯一：同一周重排直接覆盖旧字节，天然只留最新版。
    周期口径见 services/archives.py：每月 25 日 → 次月 25 日，25 号及之后归次月。
    """

    __tablename__ = "export_archives"
    __table_args__ = (
        UniqueConstraint("admin_id", "semester", "week_no", name="uq_archive_one_per_week"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    admin_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    semester: Mapped[str] = mapped_column(String(32), index=True)
    week_no: Mapped[int] = mapped_column(Integer, index=True)
    filename: Mapped[str] = mapped_column(String(128))
    xlsx: Mapped[bytes] = mapped_column(LargeBinary)
    cycle_start: Mapped[date] = mapped_column(Date, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
