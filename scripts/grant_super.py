"""把一个学号提升为超级管理员（本地运维小工具）。

    .venv\\Scripts\\python.exe scripts\\grant_super.py 2025000001
    .venv\\Scripts\\python.exe scripts\\grant_super.py 2025000001 --db data\\dev.db

账号不存在时直接建一个（不需要密码：超管登录仍然走教务系统验证）。
运行时请先停掉服务，避免 SQLite 写冲突。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import init_db, make_engine
from app.models import Role, Status, User, utcnow


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("student_no")
    parser.add_argument("--db", default=None, help="数据库路径，默认 APP_DB_PATH 或 data/app.db")
    args = parser.parse_args()

    settings = Settings.from_env()
    db_path = Path(args.db) if args.db else settings.db_path
    if not db_path.is_absolute():
        db_path = ROOT / db_path

    engine = make_engine(db_path)
    init_db(engine)
    print(f"数据库：{db_path}")

    with Session(engine) as db:
        user = db.scalar(select(User).where(User.student_no == args.student_no))
        if user is None:
            user = User(
                student_no=args.student_no,
                name="",
                role=Role.SUPER_ADMIN.value,
                status=Status.APPROVED.value,
                approved_at=utcnow(),
            )
            db.add(user)
            action = "新建"
        else:
            action = f"{user.role} -> super_admin"
            user.role = Role.SUPER_ADMIN.value
            user.status = Status.APPROVED.value
            user.approved_at = user.approved_at or utcnow()
            user.owner_admin_id = None
            user.disabled_at = None
        db.commit()
        db.refresh(user)
        print(f"{action}")
        print(f"  id={user.id} 学号={user.student_no} 姓名={user.name or '（登录后自动填）'} "
              f"角色={user.role} 状态={user.status} 停用={user.disabled_at}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
