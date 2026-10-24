"""FastAPI 应用：会话、三级角色、学生与管理员首页骨架。

作用域强制在查询层：`user` 只见自己，`admin` 只见 owner_admin_id 指向自己的成员，
`super_admin` 全量。页面只负责展示，不作为权限边界。
"""

from __future__ import annotations

import hmac
import json
import secrets
import tempfile
import time
import urllib.parse
from datetime import date, timedelta
from pathlib import Path
from typing import Iterator, Optional

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from app.config import Settings
from app.db import init_db, make_engine, make_session_factory
from app import logging_setup
from app.holidays import week_days
from app.jiaowu import DAYS, SLOTS, IdentityVerifier, JiaowuClient, LoginBundle, build_verifier, parse_timetable_html
from app.models import ExportArchive, Role, ScheduleSettings, SessionRow, Status, User, utcnow
from app.scheduler import Infeasible, validate_plan
from app.security import COOKIE_NAME, new_csrf, new_token, token_hash
from app.services import admin_ops, archives, departments, leaves as leave_service, scheduling, weeknumber
from app.services import timetable as timetable_service
from app.theme import seconds_until_next_switch, theme_for
from app.xlsx import (
    REQUIRED_CELL_COUNT,
    TEXT_FIELD_LABELS,
    TemplateChanged,
    export_schedule_bytes,
    fillable_slots,
    read_template_texts,
    schedule_filename,
    store_template,
)

HOME_BY_ROLE = {
    Role.SUPER_ADMIN.value: "/admin/users",
    Role.TEACHER.value: "/admin/users",   # 老师与超管同权，落地页一致
    Role.ADMIN.value: "/admin",
    Role.USER.value: "/me",
}

#: 值班表模板上传上限，防止有人传一个巨大的表格进来
MAX_TEMPLATE_BYTES = 5 * 1024 * 1024


def _clamp_week(raw: str, fallback: int) -> int:
    try:
        value = int(raw) if str(raw).strip() else fallback
    except (TypeError, ValueError):
        value = fallback
    return max(1, min(30, value))


def _schedule_context(db: OrmSession, admin: User, week: int, this_week: int,
                      app_settings: Settings, max_per_person: int = 2,
                      message: str = "", error=None,
                      cycle_start: Optional[date] = None) -> dict:
    semester = app_settings.semester
    row = scheduling.department_settings(db, admin, max_per_person)
    names = scheduling.plan_names(db, admin, semester, week)
    assignments = [
        {"day": day, "slot": slot, "name": names[(day, slot)]}
        for day in DAYS
        for slot in SLOTS
        if (day, slot) in names
    ]
    template_path = scheduling.department_template(db, admin, app_settings)
    calendar = scheduling.week_calendar(
        weeknumber.effective_semester_start(
            db, row.semester_start or app_settings.semester_start),
        week,
    )
    try:
        template_slots = fillable_slots(template_path)
        template_error = ""
    except Exception as exc:  # noqa: BLE001 - 模板坏了要在页面上说清楚，而不是 500
        template_slots = set()
        template_error = str(exc)
    saved_texts = scheduling.template_texts_of(row)
    try:
        template_texts = read_template_texts(template_path)
    except Exception:  # noqa: BLE001
        template_texts = {}
    # 在线改过的值优先显示，没改过的用模板原文
    effective_texts = {**template_texts, **saved_texts}

    # 总体导出卡片：默认当前周期，可通过 ?cycle= 查看相邻周期
    if cycle_start is None:
        cycle_start = archives.current_cycle_start()
    _, cycle_label = archives.cycle_for(cycle_start)
    range_start, range_end = archives.cycle_range(cycle_start)
    archive_items = [
        {
            "id": a.id,
            "filename": a.filename,
            "when": archives.beijing_str(a.created_at),
            "week": a.week_no,
        }
        for a in archives.list_archives(db, admin.id, cycle_start)
    ]
    current_start = archives.current_cycle_start()
    # 历史周期：每周期一个独立折叠块（标签+起止+份数+文件列表+zip）
    history_cycles = archives.history_summary(db, admin.id, current_start)
    # 周次校正：显示当前口径下的"本周"，方便学期初对照
    anchor = weeknumber.anchor_of(db)

    return {
        "week": week,
        "this_week": this_week,
        "message": message,
        "error": error,
        "members": scheduling.schedulable_members(db, admin),
        "settings_row": row,
        "assignments": assignments,
        "semester": semester,
        "calendar": calendar,
        "skipped_slots": calendar.skipped_slots(template_slots),
        "template_name": template_path.name,
        "template_is_custom": bool(row.template_path),
        "template_cells": len(template_slots),
        "required_cells": REQUIRED_CELL_COUNT,
        "template_error": template_error,
        "template_texts": effective_texts,
        "template_text_fields": TEXT_FIELD_LABELS,
        "template_text_defaults": template_texts,
        "archives_list": archive_items,
        "history_cycles": history_cycles,
        "week_anchor_monday": anchor[0].isoformat() if anchor else "",
        "week_anchor_week": anchor[1] if anchor else 0,
        "week_max": weeknumber.MAX_WEEK,
        "cycle_label": cycle_label,
        "cycle_range_label": f"{range_start.strftime('%m-%d')} ~ {range_end.strftime('%m-%d')}",
        "cycle_start_iso": cycle_start.isoformat(),
        "prev_cycle_iso": archives.shift_cycle(cycle_start, -1).isoformat(),
        # 未来周期没有存档可看，最多翻到当前周期
        "next_cycle_iso": (archives.shift_cycle(cycle_start, 1).isoformat()
                           if cycle_start < current_start else None),
    }


def create_app(settings: Settings | None = None, verifier: IdentityVerifier | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    logger = logging_setup.configure(settings.log_path)
    engine = make_engine(settings.db_path)
    init_db(engine)
    factory = make_session_factory(engine)

    app = FastAPI(title="课表排班系统", docs_url=None, redoc_url=None)
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = factory
    app.state.verifier = verifier or build_verifier(settings.jiaowu_mode, settings.stub_accounts)

    templates = Jinja2Templates(directory=str(settings.templates_dir))
    app.mount("/static", StaticFiles(directory=str(settings.static_dir)), name="static")

    @app.middleware("http")
    async def access_log(request: Request, call_next):
        response = await call_next(request)
        # 只记方法/路径/状态码：不记 cookie、不记表单、不记课表内容
        logger.info("access %s %s -> %s", request.method, request.url.path, response.status_code)
        return response

    # ------------------------------------------------------------- 基础设施

    def get_db() -> Iterator[OrmSession]:
        db = factory()
        try:
            yield db
        finally:
            db.close()

    def _set_session_cookie(response, token: str) -> None:
        response.set_cookie(
            COOKIE_NAME,
            token,
            max_age=settings.cookie_max_age,
            httponly=True,
            samesite="lax",
            secure=settings.secure_cookie,
            path="/",
        )

    def _issue_session(db: OrmSession, user: User, request: Request) -> str:
        token = new_token()
        db.add(
            SessionRow(
                token_hash=token_hash(token),
                user_id=user.id,
                csrf=new_csrf(),
                expires_at=utcnow() + timedelta(days=settings.session_days),
            )
        )
        db.commit()
        request.state.new_session_token = token
        return token

    def _current(db: OrmSession, request: Request) -> tuple[Optional[User], Optional[SessionRow]]:
        raw = request.cookies.get(COOKIE_NAME)
        if not raw:
            return None, None
        row = db.scalar(select(SessionRow).where(SessionRow.token_hash == token_hash(raw)))
        if row is None:
            return None, None
        now = utcnow()
        if row.expires_at <= now:
            db.delete(row)
            db.commit()
            return None, None

        # 滑动续期：只要活动就永远不过期，每次请求都重置为 30 天
        row.expires_at = now + timedelta(days=settings.session_days)
        row.last_seen_at = now
        db.commit()
        request.state.slide_token = raw

        user = db.get(User, row.user_id)
        if user is None:
            return None, None
        return user, row

    @app.middleware("http")
    async def session_cookie_writer(request: Request, call_next):
        response = await call_next(request)
        token = getattr(request.state, "new_session_token", None)
        if token:
            _set_session_cookie(response, token)
        else:
            slide = getattr(request.state, "slide_token", None)
            if slide:
                _set_session_cookie(response, slide)
        return response

    def _csrf_token(db: OrmSession, request: Request) -> str:
        raw = request.cookies.get(COOKIE_NAME)
        if not raw:
            return ""
        row = db.scalar(select(SessionRow).where(SessionRow.token_hash == token_hash(raw)))
        return row.csrf if row is not None else ""

    def _require_csrf(db: OrmSession, request: Request, token: str) -> None:
        expected = _csrf_token(db, request)
        if not expected or not token:
            raise HTTPException(status_code=403, detail="表单令牌失效，请刷新页面后重试")
        if not hmac.compare_digest(expected.encode("utf-8"), token.encode("utf-8")):
            raise HTTPException(status_code=403, detail="表单令牌失效，请刷新页面后重试")

    def _require_department_admin(db: OrmSession, request: Request) -> User:
        """排班/总览是部门级操作：管理员、超管、老师（代管）都可以。"""
        user = _require_admin(db, request)
        if user.role not in (Role.ADMIN.value, Role.SUPER_ADMIN.value, Role.TEACHER.value):
            raise HTTPException(status_code=403, detail="只有部门管理员可以做排班")
        return user

    def _acting_admin(db: OrmSession, user: User) -> User:
        """老师代管超管的部门：部门级数据一律以超管身份落库。

        老师没有自己的部门（也不该有，注册流程对老师不可见），所以他要操作的
        就是超管名下那套数据。当前部署只有一个超管（env 引导），按角色找即可；
        将来出现多个超管时，这里要升级成显式的代管关系。
        """
        if user.role != Role.TEACHER.value:
            return user
        owner = db.scalar(
            select(User)
            .where(User.role == Role.SUPER_ADMIN.value)
            .order_by(User.id)
        )
        if owner is None:
            raise HTTPException(status_code=503, detail="系统没有超级管理员，老师暂无法代管部门")
        return owner

    def _render(request: Request, name: str, current_user: Optional[User] = None,
                status_code: int = 200, **extra) -> HTMLResponse:
        # theme 每页都算：页面直接带着正确的 data-theme 出来，
        # 不用等 JS 执行，暗色时段打开不会先闪一下白。
        # theme_switch_in 是给前端的倒计时：到点自己翻主题，
        # 不用设备时钟（用户手机时间不准也不会换错）。
        context = {"request": request, "current_user": current_user,
                   "theme": theme_for(),
                   "theme_switch_in": seconds_until_next_switch(), **extra}
        return templates.TemplateResponse(request, name, context, status_code=status_code)

    def _semester_start_for(db: OrmSession, user: User, app_settings: Settings) -> "date":
        """这个人的课表按哪份开学日期算：本人所在部门优先，其次全局配置。

        普通成员归属审批他的管理员；管理员/超管自己就是部门，所以取自己的行。
        这样同一个人的「我的课表」和「一键排班」算出来的周次不会打架。
        最后过 effective_semester_start：周次校正锚点在时平移起点，
        让日期标签/周历/节假日与校正后的编号保持同一口径。
        """
        owner_id = user.owner_admin_id
        if owner_id is None and user.is_admin:
            owner_id = user.id
        if owner_id is None:
            base = app_settings.semester_start
        else:
            row = db.get(ScheduleSettings, owner_id)
            base = (row.semester_start if row and row.semester_start
                    else app_settings.semester_start)
        return weeknumber.effective_semester_start(db, base)

    def _require_user(db: OrmSession, request: Request) -> User:
        user, _ = _current(db, request)
        if user is None:
            raise HTTPException(status_code=303, headers={"Location": "/login"})
        if user.is_disabled:
            raise HTTPException(status_code=403, detail="账号已停用，请联系管理员")
        return user

    def _require_approved(db: OrmSession, request: Request) -> User:
        user = _require_user(db, request)
        if not user.is_approved:
            raise HTTPException(status_code=303, headers={"Location": "/pending"})
        return user

    def _require_admin(db: OrmSession, request: Request) -> User:
        user = _require_approved(db, request)
        if user.role not in (Role.ADMIN.value, Role.SUPER_ADMIN.value, Role.TEACHER.value):
            raise HTTPException(status_code=403, detail="需要管理员权限")
        return user

    def _require_super(db: OrmSession, request: Request) -> User:
        user = _require_approved(db, request)
        if user.role not in (Role.SUPER_ADMIN.value, Role.TEACHER.value):
            raise HTTPException(status_code=403, detail="需要超级管理员权限")
        return user

    def _landing_for(user: User) -> str:
        if not user.is_approved:
            return "/pending"
        return HOME_BY_ROLE.get(user.role, "/me")

    # ------------------------------------------------------------- 路由

    @app.get("/")
    def index(request: Request, db: OrmSession = Depends(get_db)):
        user, _ = _current(db, request)
        if user is None:
            return RedirectResponse("/login", status_code=303)
        return RedirectResponse(_landing_for(user), status_code=303)

    @app.get("/login")
    def login_form(request: Request, db: OrmSession = Depends(get_db)):
        user, _ = _current(db, request)
        if user is not None:
            return RedirectResponse(_landing_for(user), status_code=303)
        return _render(request, "login.html")

    @app.post("/login")
    def login_submit(
        request: Request,
        student_no: str = Form(...),
        password: str = Form(...),
        db: OrmSession = Depends(get_db),
    ):
        student_no = student_no.strip()
        user = db.scalar(select(User).where(User.student_no == student_no))
        bundle = None
        teacher_cfg = settings.teacher_accounts.get(student_no)
        # 老师本地认证：不走教务。已有账号不是 teacher 时绝不认本地密码，
        # 防止自定义密码绕过教务冒充真实学生。
        if (teacher_cfg is not None
                and hmac.compare_digest(teacher_cfg["password"].encode("utf-8"),
                                        password.encode("utf-8"))
                and (user is None or user.role == Role.TEACHER.value)):
            if user is None:
                # 老师首登即建成已审批账号：不需要任何人审批，应急通道随时可用
                user = User(
                    student_no=student_no,
                    name=teacher_cfg.get("name", ""),
                    role=Role.TEACHER.value,
                    status=Status.APPROVED.value,
                    approved_at=utcnow(),
                )
                db.add(user)
            elif not user.name and teacher_cfg.get("name"):
                user.name = teacher_cfg["name"]
            db.commit()
            db.refresh(user)
        else:
            bundle = app.state.verifier.login(student_no, password)
            if not bundle.ok:
                return _render(request, "login.html", error=bundle.reason,
                               student_no=student_no, status_code=401)

            if user is None:
                # 没有单独的注册步骤：第一次登录就用这个学号建账号。
                # 建出来是「待审批」且还没归属部门，登录后到 /pending 挑一个管理员申请。
                is_bootstrap_super = (
                    bool(settings.super_admin_student_no)
                    and student_no == settings.super_admin_student_no
                )
                if is_bootstrap_super:
                    user = User(
                        student_no=student_no,
                        name=bundle.name or "",
                        role=Role.SUPER_ADMIN.value,
                        status=Status.APPROVED.value,
                        approved_at=utcnow(),
                    )
                else:
                    user = User(
                        student_no=student_no,
                        name=bundle.name or "",
                        role=Role.USER.value,
                        status=Status.PENDING.value,
                    )
                db.add(user)
                db.commit()
                db.refresh(user)

        if user.is_disabled:
            return _render(request, "login.html", error="账号已停用，请联系管理员",
                           status_code=403)

        if not user.name and bundle and bundle.name:
            user.name = bundle.name
            db.commit()

        # 教务登录才有课表和会话可抓；老师本地登录到此为止
        if bundle is not None:
            # 登录这一刻密码在内存里，顺手把课表抓下来缓存；此后页面不再需要密码
            if bundle.parse is not None:
                timetable_service.store_courses(db, user, settings.semester, bundle.parse)
                user.last_parse_notice = "; ".join(bundle.parse.warnings)[:1000]
            # 保存教务会话 cookie，刷新课表时免密复用
            if bundle.cookies:
                import json
                user.jiaowu_cookies = json.dumps(bundle.cookies)
            db.commit()

        token = _issue_session(db, user, request)
        response = RedirectResponse(_landing_for(user), status_code=303)
        _set_session_cookie(response, token)
        return response

    def _joinable_admins(db: OrmSession, user: User) -> list[User]:
        """新用户登录后可以申请加入的部门管理员（超管自己也是一个部门）。"""
        return list(
            db.scalars(
                select(User)
                .where(
                    User.role.in_((Role.ADMIN.value, Role.SUPER_ADMIN.value)),
                    User.status == Status.APPROVED.value,
                    User.id != user.id,
                )
                .order_by(User.role.desc(), User.student_no)
            ).all()
        )

    @app.post("/logout")
    def logout(request: Request, db: OrmSession = Depends(get_db)):
        raw = request.cookies.get(COOKIE_NAME)
        if raw:
            row = db.scalar(select(SessionRow).where(SessionRow.token_hash == token_hash(raw)))
            if row is not None:
                db.delete(row)
                db.commit()
        response = RedirectResponse("/login", status_code=303)
        response.delete_cookie(COOKIE_NAME, path="/")
        return response

    def _pending_context(db: OrmSession, request: Request, user: User,
                         error: str = "") -> dict:
        """待审批页的上下文：没选部门就摆管理员列表，选了就显示等谁审批。"""
        approver = db.get(User, user.owner_admin_id) if user.owner_admin_id else None
        choosing = user.role == Role.USER.value and user.owner_admin_id is None
        return {
            "approver": approver,
            "choosing": choosing,
            "choices": _joinable_admins(db, user) if choosing else [],
            "csrf": _csrf_token(db, request),
            "error": error,
        }

    @app.get("/pending")
    def pending(request: Request, db: OrmSession = Depends(get_db)):
        user = _require_user(db, request)
        if user.is_approved:
            return RedirectResponse(_landing_for(user), status_code=303)
        return _render(request, "pending.html", user,
                       **_pending_context(db, request, user))

    @app.post("/pending/choose")
    def pending_choose(
        request: Request,
        admin_id: int = Form(...),
        csrf: str = Form(""),
        db: OrmSession = Depends(get_db),
    ):
        """挑一个管理员申请加入他的部门。"""
        user = _require_user(db, request)
        _require_csrf(db, request, csrf)
        if user.is_approved:
            return RedirectResponse(_landing_for(user), status_code=303)

        approver = db.get(User, admin_id)
        if (approver is None or not approver.is_admin or approver.is_disabled
                or approver.status != Status.APPROVED.value):
            return _render(request, "pending.html", user,
                           **_pending_context(db, request, user,
                                              error="这个管理员不存在或已停用，请重新选一个"),
                           status_code=400)

        user.owner_admin_id = approver.id
        # 之前点过「申请成为管理员」的话，现在改成进部门，角色要退回来
        if user.role != Role.USER.value:
            user.role = Role.USER.value
        db.commit()
        return RedirectResponse("/pending", status_code=303)

    @app.post("/pending/become-admin")
    def pending_become_admin(
        request: Request,
        csrf: str = Form(""),
        db: OrmSession = Depends(get_db),
    ):
        """不加入任何部门，改申请当部门管理员，由超管审批。"""
        user = _require_user(db, request)
        _require_csrf(db, request, csrf)
        if user.is_approved:
            return RedirectResponse(_landing_for(user), status_code=303)
        user.role = Role.ADMIN.value
        user.owner_admin_id = None
        db.commit()
        return RedirectResponse("/pending", status_code=303)

    @app.get("/me")
    def student_home(request: Request, week: str = "", db: OrmSession = Depends(get_db)):
        user = _require_approved(db, request)
        semester_start = _semester_start_for(db, user, settings)
        this_week = timetable_service.current_week(settings, db, semester_start=semester_start)
        try:
            view_week = int(week) if week else this_week
        except ValueError:
            view_week = this_week
        view_week = max(1, min(30, view_week))

        on_leave = leave_service.week_leaves(db, user, settings.semester, view_week)
        grid = timetable_service.build_grid(db, user, settings.semester, view_week, on_leave)
        total_courses = sum(len(cell.courses) for row in grid for cell in row)
        # 表头要标出这一周每天是几号，所以得知道第 1 周从哪天开始
        dates = week_days(semester_start, view_week)
        return _render(
            request, "student_home.html", user,
            grid=grid, days=DAYS, slots=SLOTS, week=view_week, this_week=this_week,
            dates=dates,
            leave_count=len(on_leave), total_courses=total_courses,
            semester=settings.semester, message=request.query_params.get("msg", ""),
            csrf=_csrf_token(db, request), notice=user.last_parse_notice,
        )

    @app.post("/me/leave")
    def toggle_leave(
        request: Request,
        day: str = Form(...),
        slot: str = Form(...),
        week: int = Form(...),
        csrf: str = Form(""),
        db: OrmSession = Depends(get_db),
    ):
        user = _require_approved(db, request)
        _require_csrf(db, request, csrf)
        if day not in DAYS or slot not in SLOTS:
            raise HTTPException(status_code=400, detail="星期或节次不合法")
        # 请假请的是"课余时间"：有线下课的时段本来就排不了班，用不着请假。
        # 线上课不算占用——被排了值班之后是可以请假的。
        if timetable_service.slot_has_offline_course(db, user, settings.semester, week, day, slot):
            raise HTTPException(status_code=400,
                                detail="这个时段你有线下课，本来就排不了班，不需要请假")
        leave_service.toggle_leave(db, user, settings.semester, week, day, slot)
        return RedirectResponse(f"/me?week={week}", status_code=303)

    @app.post("/me/refresh")
    def refresh_timetable(
        request: Request,
        csrf: str = Form(""),
        db: OrmSession = Depends(get_db),
    ):
        """解析课表：拿登录时存下的教务 cookie 直接查，不再要密码。"""
        user = _require_approved(db, request)
        _require_csrf(db, request, csrf)

        import json
        try:
            cookies = json.loads(user.jiaowu_cookies) if user.jiaowu_cookies else {}
        except ValueError:
            cookies = {}

        bundle = app.state.verifier.refresh(user.student_no, cookies)
        if not bundle.ok:
            # 教务会话过期：清掉坏 cookie，让用户重新登录一次（登录时才需要密码）
            user.jiaowu_cookies = ""
            db.commit()
            return RedirectResponse(f"/me?msg={bundle.reason}", status_code=303)

        if bundle.parse is None:
            return RedirectResponse("/me?msg=课表解析失败，请稍后再试", status_code=303)
        count = timetable_service.store_courses(db, user, settings.semester, bundle.parse)
        if bundle.name and bundle.name != user.name:
            user.name = bundle.name   # 顺手把姓名刷新成教务上的真名
        user.last_parse_notice = "; ".join(bundle.parse.warnings)[:1000]
        if bundle.cookies:
            user.jiaowu_cookies = json.dumps(bundle.cookies)
        db.commit()
        return RedirectResponse(f"/me?msg=已解析，共 {count} 个课次", status_code=303)

    @app.get("/admin")
    def admin_home(request: Request, db: OrmSession = Depends(get_db)):
        user = _require_admin(db, request)
        admin = _acting_admin(db, user)
        members = departments.members_of(db, admin)
        pending = [m for m in members if m.status == Status.PENDING.value]
        approved = [m for m in members if m.status == Status.APPROVED.value]
        return _render(request, "admin_home.html", user,
                       members=members, pending=pending, approved=approved,
                       csrf=_csrf_token(db, request),
                       message=request.query_params.get("msg", ""))

    @app.post("/admin/decide")
    def admin_decide(
        request: Request,
        user_id: int = Form(...),
        decision: str = Form(...),
        csrf: str = Form(""),
        db: OrmSession = Depends(get_db),
    ):
        user = _require_admin(db, request)
        _require_csrf(db, request, csrf)
        admin = _acting_admin(db, user)
        try:
            if decision == "approve":
                departments.approve_member(db, admin, user_id)
                msg = "已通过"
            elif decision == "reject":
                departments.reject_member(db, admin, user_id)
                msg = "已拒绝"
            else:
                raise HTTPException(status_code=400, detail="未知的审批动作")
        except departments.OutOfScope:
            raise HTTPException(status_code=403, detail="这个账号不在你的部门里")
        return RedirectResponse(f"/admin?msg={msg}", status_code=303)

    @app.get("/admin/users")
    def super_home(request: Request, db: OrmSession = Depends(get_db)):
        user = _require_super(db, request)
        everyone = departments.all_users(db)
        return _render(request, "super_home.html", user,
                       everyone=everyone,
                       pending_admins=departments.pending_admin_candidates(db),
                       departments_rows=admin_ops.department_rows(db),
                       admins=admin_ops.all_admins(db),
                       total_users=len(everyone),
                       csrf=_csrf_token(db, request),
                       message=request.query_params.get("msg", ""))

    @app.post("/admin/users/decide")
    def super_decide(
        request: Request,
        user_id: int = Form(...),
        decision: str = Form(...),
        csrf: str = Form(""),
        db: OrmSession = Depends(get_db),
    ):
        _require_super(db, request)
        _require_csrf(db, request, csrf)
        try:
            if decision == "approve":
                departments.approve_admin_candidate(db, user_id)
                msg = "已通过管理员申请"
            elif decision == "reject":
                departments.reject_admin_candidate(db, user_id)
                msg = "已拒绝管理员申请"
            else:
                raise HTTPException(status_code=400, detail="未知的审批动作")
        except departments.OutOfScope:
            raise HTTPException(status_code=404, detail="没有这个待审申请")
        return RedirectResponse(f"/admin/users?msg={msg}", status_code=303)

    # ---------------- 工单 10：超管用户管理 ----------------

    @app.post("/admin/users/disable")
    def super_disable(
        request: Request,
        user_id: int = Form(...),
        disabled: str = Form("1"),
        csrf: str = Form(""),
        db: OrmSession = Depends(get_db),
    ):
        _require_super(db, request)
        _require_csrf(db, request, csrf)
        try:
            target = admin_ops.set_disabled(db, user_id, disabled == "1")
        except admin_ops.AdminOpError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        msg = "已停用" if target.is_disabled else "已恢复"
        return RedirectResponse(f"/admin/users?msg={msg} {target.student_no}", status_code=303)

    @app.post("/admin/users/role")
    def super_role(
        request: Request,
        user_id: int = Form(...),
        role: str = Form(...),
        csrf: str = Form(""),
        db: OrmSession = Depends(get_db),
    ):
        _require_super(db, request)
        _require_csrf(db, request, csrf)
        try:
            target = admin_ops.set_role(db, user_id, role)
        except admin_ops.AdminOpError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        label = {"teacher": "老师", "admin": "管理员"}.get(target.role, "普通用户")
        return RedirectResponse(f"/admin/users?msg={target.student_no} 现在是{label}", status_code=303)

    @app.post("/admin/users/reassign")
    def super_reassign(
        request: Request,
        user_id: int = Form(...),
        admin_id: int = Form(...),
        csrf: str = Form(""),
        db: OrmSession = Depends(get_db),
    ):
        _require_super(db, request)
        _require_csrf(db, request, csrf)
        try:
            target = admin_ops.reassign_member(db, user_id, admin_id)
        except admin_ops.AdminOpError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return RedirectResponse(f"/admin/users?msg=已把 {target.student_no} 改到新部门", status_code=303)

    # ---------------- 工单 08：全员总览 ----------------

    @app.get("/admin/overview")
    def admin_overview(request: Request, week: str = "", db: OrmSession = Depends(get_db)):
        user = _require_department_admin(db, request)
        admin = _acting_admin(db, user)
        this_week = timetable_service.current_week(
            settings, db, semester_start=_semester_start_for(db, admin, settings))
        view_week = _clamp_week(week, this_week)
        rows = scheduling.member_load(db, admin, settings, view_week)
        duty = scheduling.plan_names(db, admin, settings.semester, view_week)

        grid = []
        for slot in SLOTS:
            line = []
            for day in DAYS:
                key = (day, slot)
                line.append(
                    {
                        "day": day,
                        "slot": slot,
                        "free": [r["member"].name or r["member"].student_no
                                 for r in rows if key in r["free"]],
                        "courses": [r["member"].name or r["member"].student_no
                                    for r in rows if key in r["courses"]],
                        "on_leave": [r["member"].name or r["member"].student_no
                                     for r in rows if key in r["leave"]],
                        "duty": duty.get(key, ""),
                    }
                )
            grid.append(line)

        return _render(
            request, "admin_overview.html", user,
            grid=grid, days=DAYS, slots=SLOTS, week=view_week, this_week=this_week,
            rows=rows, member_count=len(rows), semester=settings.semester,
            message=request.query_params.get("msg", ""),
        )

    # ---------------- 工单 09：一键排班与下载 ----------------

    @app.get("/admin/schedule")
    def schedule_page(request: Request, week: str = "", cycle: str = "",
                      db: OrmSession = Depends(get_db)):
        user = _require_department_admin(db, request)
        admin = _acting_admin(db, user)
        this_week = timetable_service.current_week(
            settings, db, semester_start=_semester_start_for(db, admin, settings))
        view_week = _clamp_week(week, this_week)
        cycle_start = None
        if cycle:
            try:
                cycle_start = date.fromisoformat(cycle)
            except ValueError:
                cycle_start = None   # 乱写的 cycle 参数就当没传，回当前周期
        return _render(
            request, "admin_schedule.html", user,
            **_schedule_context(db, admin, view_week, this_week, settings,
                                settings.max_per_person,
                                message=request.query_params.get("msg", ""),
                                cycle_start=cycle_start),
            csrf=_csrf_token(db, request),
        )

    @app.post("/admin/schedule/settings")
    def schedule_save_settings(
        request: Request,
        week: int = Form(1),
        max_per_person: int = Form(2),
        special_person_id: str = Form(""),
        blacklist: str = Form(""),
        csrf: str = Form(""),
        db: OrmSession = Depends(get_db),
    ):
        """只存部门排班设置，不触发排班。

        常驻岗位（周六 + 每天 11-12 节固定人员）平时可能只想改个人，不该被迫重排整周、
        把已经交上去的排班覆盖掉，所以单独开这个入口。
        """
        user = _require_department_admin(db, request)
        admin = _acting_admin(db, user)
        _require_csrf(db, request, csrf)
        view_week = max(1, min(30, week))
        scheduling.update_settings(
            db, admin,
            max_per_person=max_per_person,
            # 选"（不指定）"要能清空上次的固定人员，所以空值传 0 而不是 None
            special_person_id=int(special_person_id) if special_person_id.strip().isdigit() else 0,
            blacklist_ids=blacklist,
        )
        return RedirectResponse(
            f"/admin/schedule?week={view_week}&msg=设置已保存（没有重新排班，本周排班保持不变）",
            status_code=303,
        )

    @app.post("/admin/schedule/run")
    def schedule_run(
        request: Request,
        week: int = Form(...),
        max_per_person: int = Form(2),
        special_person_id: str = Form(""),
        blacklist: str = Form(""),
        csrf: str = Form(""),
        db: OrmSession = Depends(get_db),
    ):
        user = _require_department_admin(db, request)
        admin = _acting_admin(db, user)
        _require_csrf(db, request, csrf)
        view_week = max(1, min(30, week))
        # 学期起始日期不在这里改：它是学期常量，由 APP_SEMESTER_START 统一配置，
        # 表单里只读展示「第 N 周 = 几月几号」，避免和全局配置口径分裂。
        scheduling.update_settings(
            db, admin,
            max_per_person=max_per_person,
            # 下拉框选"（不指定）"时必须能清掉上次的固定人员，所以空值传 0 而不是 None
            special_person_id=int(special_person_id) if special_person_id.strip().isdigit() else 0,
            blacklist_ids=blacklist,
        )

        # 每次排班换一个随机种子：同一份数据多次运行能拿到不同的合法解
        plan, request_obj, members, calendar = scheduling.run(
            db, admin, settings, view_week, seed=secrets.randbelow(2**31)
        )
        if isinstance(plan, Infeasible):
            # 排不出来就直说，不给"看起来像排好了"的错表
            return _render(
                request, "admin_schedule.html", user,
                **_schedule_context(db, admin, view_week, view_week, settings,
                                    settings.max_per_person, error=plan),
                csrf=_csrf_token(db, request),
                status_code=409,
            )

        # 出口再自检一次：撞占用、次数不对、周六/11-12 不独占、黑名单——一条不对就不出表
        try:
            problems = validate_plan(plan, request_obj)
        except AssertionError as exc:
            # 排班器自证失败（AssertionError）也走同一个带问题清单的错误页，不裸 500
            return _render(
                request, "admin_schedule.html", user,
                **_schedule_context(db, admin, view_week, view_week, settings,
                                    settings.max_per_person),
                csrf=_csrf_token(db, request),
                validation=[str(exc)],
                status_code=500,
            )
        if problems:
            return _render(
                request, "admin_schedule.html", user,
                **_schedule_context(db, admin, view_week, view_week, settings,
                                    settings.max_per_person),
                csrf=_csrf_token(db, request),
                validation=problems,
                status_code=500,
            )

        count = scheduling.save_plan(db, admin, settings.semester, view_week, plan, members)
        skipped = len(calendar.holidays)
        tail = f"，已跳过 {skipped} 天节假日" if skipped else ""
        # 总体导出：排班成功即存档该周 Excel（快照）；失败只附加警告，不影响排班结果
        _archive, archive_warn = archives.record_archive(db, admin, settings, view_week)
        note = f"；{archive_warn}" if archive_warn else "；本周表已自动存档到总体导出"
        return RedirectResponse(
            f"/admin/schedule?week={view_week}&msg=排好了 {count} 个班次{tail}{note}，点下面的按钮下载表格",
            status_code=303,
        )

    @app.get("/admin/schedule/download")
    def schedule_download(request: Request, week: int = 1, db: OrmSession = Depends(get_db)):
        user = _require_department_admin(db, request)
        admin = _acting_admin(db, user)
        view_week = max(1, min(30, week))
        names = scheduling.plan_names(db, admin, settings.semester, view_week)
        if not names:
            raise HTTPException(status_code=404, detail="这一周还没有排班，先一键排班")
        row = scheduling.department_settings(db, admin, settings.max_per_person)
        try:
            content = export_schedule_bytes(
                scheduling.department_template(db, admin, settings),
                names,
                week=view_week,
                texts=scheduling.template_texts_of(row),
            )
        except TemplateChanged as exc:
            raise HTTPException(status_code=500, detail=str(exc))
        filename = schedule_filename(view_week, settings.semester)
        quoted = urllib.parse.quote(filename)
        return Response(
            content,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f"attachment; filename*=utf-8''{quoted}"},
        )

    @app.get("/admin/schedule/archives/zip")
    def archives_zip(request: Request, cycle: str = "", db: OrmSession = Depends(get_db)):
        """打包某个历史周期的全部存档为 zip（内存生成，不落临时文件）。"""
        import io
        import zipfile as zipfile_mod

        user = _require_department_admin(db, request)
        admin = _acting_admin(db, user)
        try:
            cycle_start = date.fromisoformat(cycle)
        except ValueError:
            raise HTTPException(status_code=400, detail="周期参数不合法")
        if cycle_start >= archives.current_cycle_start():
            raise HTTPException(status_code=404, detail="只有历史周期提供 zip 打包")
        items = archives.list_archives(db, admin.id, cycle_start)
        if not items:
            raise HTTPException(status_code=404, detail="这个周期还没有存档")
        buffer = io.BytesIO()
        with zipfile_mod.ZipFile(buffer, "w", zipfile_mod.ZIP_DEFLATED) as zf:
            for item in items:
                zf.writestr(item.filename, item.xlsx)
        _, label = archives.cycle_for(cycle_start)
        quoted = urllib.parse.quote(f"值班表存档 {label}.zip")
        return Response(
            buffer.getvalue(),
            media_type="application/zip",
            headers={"Content-Disposition": f"attachment; filename*=utf-8''{quoted}"},
        )

    @app.post("/admin/schedule/week-calibrate")
    def week_calibrate(request: Request, week: int = Form(...),
                       csrf: str = Form(""), db: OrmSession = Depends(get_db)):
        """周次校正：把今天所在自然周指定为第 N 周，全站立即生效。

        学期初学校实际教学周和默认推算对不上时用；自然周结构不变，只平移编号。
        """
        user = _require_department_admin(db, request)
        _require_csrf(db, request, csrf)
        if not 1 <= week <= weeknumber.MAX_WEEK:
            raise HTTPException(status_code=400, detail=f"周次必须在 1~{weeknumber.MAX_WEEK} 之间")
        weeknumber.save_anchor(db, week)
        return RedirectResponse(
            f"/admin/schedule?msg=周次已校正：本周为第 {week} 周，全站即时生效",
            status_code=303,
        )

    @app.get("/admin/schedule/archives/{archive_id}/download")
    def archive_download(archive_id: int, request: Request, db: OrmSession = Depends(get_db)):
        """下载一条周期存档：内容 = 排班那一刻生成的 Excel 快照。"""
        user = _require_department_admin(db, request)
        admin = _acting_admin(db, user)
        archive = db.get(ExportArchive, archive_id)
        if archive is None or archive.admin_id != admin.id:
            raise HTTPException(status_code=404, detail="没有这个存档")
        quoted = urllib.parse.quote(archive.filename)
        return Response(
            archive.xlsx,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f"attachment; filename*=utf-8''{quoted}"},
        )

    # ---------------- 值班表模板：在线改文案（不用重传 Excel）----------------

    @app.get("/admin/schedule/template/download")
    def download_template(request: Request, db: OrmSession = Depends(get_db)):
        """下载当前生效的模板：姓名格全空，带上在线改过的文案。"""
        user = _require_department_admin(db, request)
        admin = _acting_admin(db, user)
        row = scheduling.department_settings(db, admin, settings.max_per_person)
        path = scheduling.department_template(db, admin, settings)
        try:
            # 空排班 = export 前会先把所有姓名格清空，得到一张干净模板
            content = export_schedule_bytes(path, {}, texts=scheduling.template_texts_of(row))
        except Exception:  # noqa: BLE001 - 模板坏了也得让人下载，退回原始文件
            content = path.read_bytes()
        quoted = urllib.parse.quote("值班表模板.xlsx")
        return Response(
            content,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f"attachment; filename*=utf-8''{quoted}"},
        )

    @app.get("/admin/schedule/template/download-default")
    def download_default_template(request: Request, db: OrmSession = Depends(get_db)):
        """下载系统自带的默认模板原始文件（不带任何部门的在线文案）。"""
        _require_department_admin(db, request)
        content = settings.template_path.read_bytes()
        quoted = urllib.parse.quote("值班表模板（默认）.xlsx")
        return Response(
            content,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f"attachment; filename*=utf-8''{quoted}"},
        )

    @app.post("/admin/schedule/template/texts")
    def save_template_texts_route(
        request: Request,
        title: str = Form(""),
        place: str = Form(""),
        teacher_name: str = Form(""),
        teacher_phone: str = Form(""),
        note: str = Form(""),
        csrf: str = Form(""),
        db: OrmSession = Depends(get_db),
    ):
        user = _require_department_admin(db, request)
        admin = _acting_admin(db, user)
        _require_csrf(db, request, csrf)
        scheduling.save_template_texts(db, admin, {
            "title": title,
            "place": place,
            "teacher_name": teacher_name,
            "teacher_phone": teacher_phone,
            "note": note,
        })
        return RedirectResponse(
            "/admin/schedule?msg=模板文案已保存，下次导出就用新文字", status_code=303
        )

    @app.post("/admin/schedule/template/reset")
    def reset_template_texts(request: Request, csrf: str = Form(""),
                             db: OrmSession = Depends(get_db)):
        user = _require_department_admin(db, request)
        admin = _acting_admin(db, user)
        _require_csrf(db, request, csrf)
        scheduling.update_settings(db, admin, template_texts=json.dumps({}))
        return RedirectResponse("/admin/schedule?msg=已恢复模板原始文案", status_code=303)

    @app.post("/admin/schedule/template/upload")
    async def upload_template(request: Request, csrf: str = Form(""),
                              file: UploadFile = File(...),
                              db: OrmSession = Depends(get_db)):
        user = _require_department_admin(db, request)
        admin = _acting_admin(db, user)
        _require_csrf(db, request, csrf)
        raw = await file.read()
        if not raw:
            raise HTTPException(status_code=400, detail="上传的文件是空的")
        if len(raw) > MAX_TEMPLATE_BYTES:
            raise HTTPException(status_code=400, detail="模板文件太大了（上限 5 MB）")
        dest = settings.db_path.parent / "templates" / f"{admin.id}_{int(time.time())}.xlsx"
        try:
            layout = store_template(raw, dest)
        except TemplateChanged as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except Exception as exc:  # noqa: BLE001 - 不是合法的 xlsx
            raise HTTPException(status_code=400, detail=f"这个文件读不出来：{exc}")
        scheduling.update_settings(db, admin, template_path=str(dest))
        return RedirectResponse(
            f"/admin/schedule?msg=模板已更换（识别到 {len(layout)} 个可填格）", status_code=303
        )

    @app.post("/admin/schedule/template/use-default")
    def use_default_template(request: Request, csrf: str = Form(""),
                             db: OrmSession = Depends(get_db)):
        user = _require_department_admin(db, request)
        admin = _acting_admin(db, user)
        _require_csrf(db, request, csrf)
        scheduling.update_settings(db, admin, template_path="")
        return RedirectResponse("/admin/schedule?msg=已恢复使用默认模板", status_code=303)

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    return app


app = create_app()
