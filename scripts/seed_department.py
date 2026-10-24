"""把一个部门的管理员和成员一次性建好（本地/演示用，不碰教务系统）。

用法：

    python scripts/seed_department.py --admin 2025000001 \
        2025000003 2025000004 2025000005 2025000002 2025000006

- 管理员必须已经注册过（超管或管理员），脚本不会新建管理员；
- 成员不存在就建、存在就改归属，统一置为「已审批」；
- 可重复执行，幂等。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.config import Settings  # noqa: E402
from app.db import init_db, make_engine, make_session_factory  # noqa: E402
from app.models import Role, Status, User, utcnow  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="给管理员挂一批成员")
    parser.add_argument("members", nargs="+", help="成员学号")
    parser.add_argument("--admin", required=True, help="部门管理员的学号")
    parser.add_argument("--db", default=None, help="数据库路径（默认取 APP_DB_PATH 或 data/app.db）")
    args = parser.parse_args()

    settings = Settings.from_env()
    db_path = Path(args.db) if args.db else settings.db_path
    print(f"数据库：{db_path}")

    engine = make_engine(db_path)
    init_db(engine)
    factory = make_session_factory(engine)

    with factory() as db:
        admin = db.scalar(select(User).where(User.student_no == args.admin))
        if admin is None:
            print(f"× 找不到管理员 {args.admin}，请先让他本人注册/登录一次")
            return 1
        if admin.role not in (Role.ADMIN.value, Role.SUPER_ADMIN.value):
            print(f"× {args.admin} 现在的角色是 {admin.role}，不是管理员")
            return 1

        print(f"部门管理员：{admin.student_no} {admin.name or ''}（{admin.role}）")
        created, moved = [], []
        for no in args.members:
            memory = db.scalar(select(User).where(User.student_no == no))
            if memory is None:
                memory = User(
                    student_no=no,
                    name="",
                    role=Role.USER.value,
                    status=Status.APPROVED.value,
                    approved_at=utcnow(),
                    owner_admin_id=admin.id,
                )
                db.add(memory)
                created.append(no)
            else:
                memory.role = Role.USER.value
                memory.status = Status.APPROVED.value
                memory.approved_at = memory.approved_at or utcnow()
                memory.owner_admin_id = admin.id
                moved.append(no)
        db.commit()

        total = db.scalars(
            select(User.student_no).where(User.owner_admin_id == admin.id)
        ).all()
        print(f"新建成员：{len(created)} 个 {created}")
        print(f"改归到本部门：{len(moved)} 个 {moved}")
        print(f"本部门现有成员（{len(total)}）：{sorted(total)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
