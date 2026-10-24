"""总体导出存档：按周期（每月 25 日 → 次月 25 日）归档每周值班表 Excel。

周期口径（北京时间）：日期 ≥ 当月 25 日 → 周期 = [当月 25 日, 次月 25 日)，标签 = 次月；
否则周期 = [上月 25 日, 当月 25 日)，标签 = 当月。"25 号之后"的排班天然归入次月的
月度材料，和勤工助学办公室的月度结算口径一致。

存档是快照语义：记录"排班那一刻"导出的字节，之后改模板文案不回填，
重新排班该周才会覆盖。重排发生在新周期时，该周存档随最新版迁移到新周期
（旧周期相应减少一条）——这是"同一周只留最新、旧的直接删掉"的自然结果。
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from app.config import Settings
from app.models import ExportArchive, User, utcnow
from app.services import scheduling
from app.theme import BEIJING, beijing_today
from app.xlsx import export_schedule_bytes, schedule_filename

logger = logging.getLogger(__name__)

DAY_25 = 25


def cycle_for(day: date) -> tuple[date, str]:
    """某日期所属周期：返回（周期起点，标签如 "2026年11月"）。"""
    if day.day >= DAY_25:
        start = date(day.year, day.month, DAY_25)
        label_year, label_month = (
            (day.year + 1, 1) if day.month == 12 else (day.year, day.month + 1)
        )
    else:
        label_year, label_month = day.year, day.month
        prev_year, prev_month = (
            (day.year - 1, 12) if day.month == 1 else (day.year, day.month - 1)
        )
        start = date(prev_year, prev_month, DAY_25)
    return start, f"{label_year}年{label_month}月"


def cycle_range(start: date) -> tuple[date, date]:
    """周期区间 [起点, 终点)：终点 = 起点的次月 25 日。"""
    return start, shift_cycle(start, 1)


def shift_cycle(start: date, months: int) -> date:
    """把周期起点整体平移 N 个月（保持 25 日），跨年安全。"""
    index = start.year * 12 + (start.month - 1) + months
    return date(index // 12, index % 12 + 1, DAY_25)


def current_cycle_start(moment: Optional[datetime] = None) -> date:
    start, _ = cycle_for(beijing_today(moment))
    return start


def beijing_str(moment: Optional[datetime]) -> str:
    """库里存的是 naive UTC，展示串按北京时间。"""
    if moment is None:
        return ""
    return moment.replace(tzinfo=timezone.utc).astimezone(BEIJING).strftime("%m-%d %H:%M")


def record_archive(db: OrmSession, admin: User, app_settings: Settings,
                   week_no: int) -> tuple[Optional[ExportArchive], str]:
    """排班成功后存档该周 Excel。

    返回 (存档, 提示)：提示非空时会展示给用户。任何失败只返回固定文案并把
    原始异常记进日志，绝不阻断排班主流程——值班表生成的问题不该让已经
    排好的班消失。
    """
    try:
        semester = app_settings.semester
        names = scheduling.plan_names(db, admin, semester, week_no)
        if not names:
            return None, "该周没有排班，未生成存档"
        row = scheduling.department_settings(db, admin, app_settings.max_per_person)
        content = export_schedule_bytes(
            scheduling.department_template(db, admin, app_settings),
            names,
            week=week_no,
            texts=scheduling.template_texts_of(row),
        )
        filename = schedule_filename(week_no, semester)
        cycle_start, _ = cycle_for(beijing_today())
        archive = db.scalar(select(ExportArchive).where(
            ExportArchive.admin_id == admin.id,
            ExportArchive.semester == semester,
            ExportArchive.week_no == week_no,
        ))
        if archive is None:
            archive = ExportArchive(
                admin_id=admin.id, semester=semester, week_no=week_no,
            )
            db.add(archive)
        archive.filename = filename
        archive.xlsx = content
        archive.cycle_start = cycle_start
        archive.created_at = utcnow()
        db.commit()
        return archive, ""
    except Exception:
        logger.exception("总体导出存档失败 week=%s admin=%s", week_no, admin.id)
        return None, "总体导出存档失败（详情见服务日志），排班本身不受影响"


def list_archives(db: OrmSession, admin_id: int, cycle_start: date) -> List[ExportArchive]:
    """某部门某个周期内的全部存档，按周次排序。

    不按学期过滤：跨学期翻看上一周期时（比如寒假换学期后补查 1 月材料），
    周期本身就标识了时间范围，学期号不该把旧材料藏起来。
    """
    return list(db.scalars(
        select(ExportArchive)
        .where(ExportArchive.admin_id == admin_id,
               ExportArchive.cycle_start == cycle_start)
        .order_by(ExportArchive.week_no)
    ).all())


def history_summary(db: OrmSession, admin_id: int, current_start: date) -> List[dict]:
    """历史周期（早于当前周期）汇总：每个周期一条，含该周期全部存档元数据。

    页面上每个周期渲染一个独立折叠块：摘要 = 标签 + 起止 + 份数，
    展开就是文件列表；最近的周期排前面。
    """
    rows = db.scalars(
        select(ExportArchive)
        .where(ExportArchive.admin_id == admin_id,
               ExportArchive.cycle_start < current_start)
        .order_by(ExportArchive.cycle_start.desc(), ExportArchive.week_no)
    ).all()
    grouped: dict[date, List[dict]] = {}
    for a in rows:
        grouped.setdefault(a.cycle_start, []).append({
            "id": a.id,
            "filename": a.filename,
            "when": beijing_str(a.created_at),
            "week": a.week_no,
        })
    out: List[dict] = []
    for start in sorted(grouped, reverse=True):
        _, label = cycle_for(start)
        range_start, range_end = cycle_range(start)
        out.append({
            "start_iso": start.isoformat(),
            "label": label,
            "range_label": f"{range_start.strftime('%m-%d')} ~ {range_end.strftime('%m-%d')}",
            "items": grouped[start],
        })
    return out
