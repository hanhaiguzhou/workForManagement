"""运行时配置。全部可用环境变量覆盖，便于本地开发与服务器部署切换。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def semester_name(today: date | None = None) -> str:
    """按校历算学期名：8 月~次年 1 月 = 第一学期，2~7 月 = 第二学期。

    今天 2026-10-23 → ``2026-2027-1``。
    """
    today = today or date.today()
    year, month = today.year, today.month
    if month >= 8:                       # 8~12 月：本学年第一学期
        return f"{year}-{year + 1}-1"
    if month == 1:                       # 1 月：仍属上一学年第一学期
        return f"{year - 1}-{year}-1"
    return f"{year - 1}-{year}-2"        # 2~7 月：上一学年第二学期


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _parse_teacher_accounts(raw: str) -> dict[str, dict[str, str]]:
    """APP_TEACHER_ACCOUNTS：`学号:密码:姓名`，逗号分隔多个；姓名可省略。

    放在部署脚本里而非数据库：换密码 = 改一行 + 重启（交接移交的方式）。
    """
    out: dict[str, dict[str, str]] = {}
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        pieces = part.split(":", 2)
        if len(pieces) < 2 or not pieces[0].strip() or not pieces[1]:
            continue
        entry = {"password": pieces[1]}
        if len(pieces) == 3 and pieces[2].strip():
            entry["name"] = pieces[2].strip()
        out[pieces[0].strip()] = entry
    return out


@dataclass
class Settings:
    db_path: Path = BASE_DIR / "data" / "app.db"
    # 值班表 Excel 模板不随仓库分发（含单位真实信息的表格不公开）。
    # 用 SCHEDULE_TEMPLATE 指向你自己的模板；模板约定（A2 标记/行列结构）见 app/xlsx.py。
    template_path: Path = BASE_DIR / "勤工助学值班表 20XX-20XX -X第X周.xlsx"
    templates_dir: Path = BASE_DIR / "app" / "web" / "templates"
    static_dir: Path = BASE_DIR / "app" / "web" / "static"

    semester: str = field(default_factory=semester_name)
    semester_start: date = date(2026, 9, 7)
    max_per_person: int = 2

    session_days: int = 30
    secure_cookie: bool = False

    # 教务适配器：real 打强智教务系统（默认）；stub 只给自动化测试用
    jiaowu_mode: str = "real"
    # 第一个用此学号注册的人自动成为超级管理员（留空则没有超管入口）
    super_admin_student_no: str | None = None

    # 访问日志落盘位置（None = 不落盘，只走 stderr）
    log_path: Path | None = None

    # stub 模式的假账号：学号 -> 密码，仅本地/测试用
    stub_accounts: dict[str, str] = field(default_factory=dict)

    # 老师账号（本地密码登录，不走教务）：学号 -> {"password": ..., "name": ...}
    teacher_accounts: dict[str, dict[str, str]] = field(default_factory=dict)

    @property
    def cookie_max_age(self) -> int:
        return self.session_days * 24 * 3600

    @classmethod
    def from_env(cls) -> "Settings":
        stub = {}
        raw_stub = os.environ.get("JIAOWU_STUB_ACCOUNTS", "")
        for pair in raw_stub.split(","):
            if ":" in pair:
                student_no, password = pair.split(":", 1)
                stub[student_no.strip()] = password.strip()
        return cls(
            db_path=Path(os.environ.get("APP_DB_PATH", str(BASE_DIR / "data" / "app.db"))),
            template_path=Path(
                os.environ.get("SCHEDULE_TEMPLATE", str(BASE_DIR / "勤工助学值班表 20XX-20XX -X第X周.xlsx"))
            ),
            semester=os.environ.get("APP_SEMESTER") or semester_name(),
            semester_start=(
                date.fromisoformat(os.environ["APP_SEMESTER_START"])
                if os.environ.get("APP_SEMESTER_START")
                else date(2026, 9, 7)
            ),
            session_days=int(os.environ.get("APP_SESSION_DAYS", "30")),
            secure_cookie=_env_bool("APP_SECURE_COOKIE", False),
            jiaowu_mode=os.environ.get("JIAOWU_MODE", "real"),
            super_admin_student_no=os.environ.get("SUPER_ADMIN_STUDENT_NO") or None,
            log_path=Path(os.environ["APP_LOG_PATH"]) if os.environ.get("APP_LOG_PATH") else None,
            stub_accounts=stub,
            teacher_accounts=_parse_teacher_accounts(os.environ.get("APP_TEACHER_ACCOUNTS", "")),
        )
