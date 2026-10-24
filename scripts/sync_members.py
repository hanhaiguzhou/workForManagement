"""把部门成员的姓名/课表从真实教务同步到本地库。

用途：管理员刚把成员挂进来时，成员自己还没登录过，库里姓名是空的，
导出的值班表就会用学号顶替姓名。跑一次这个脚本，就按 users.txt 里的
密码去教务把每个人的真实姓名和课表抓回来（只存姓名/课表/cookie，不存密码）。

用法：

    python scripts/sync_members.py                      # 同步整个库里的所有账号
    python scripts/sync_members.py 2025000001 2025000003
    python scripts/sync_members.py --admin 2025000001   # 只同步某个部门的成员
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.config import Settings  # noqa: E402
from app.db import init_db, make_engine, make_session_factory  # noqa: E402
from app.jiaowu import JiaowuError, JiaowuVerifier  # noqa: E402
from app.models import User  # noqa: E402
from app.services import timetable as timetable_service  # noqa: E402


def load_passwords() -> dict[str, str]:
    path = Path("users.txt")
    accounts: dict[str, str] = {}
    if not path.exists():
        return accounts
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = re.split(r"[\s,]+", line)
        if len(parts) >= 2:
            accounts[parts[0]] = parts[1]
    return accounts


def main() -> int:
    parser = argparse.ArgumentParser(description="从真实教务同步成员姓名与课表")
    parser.add_argument("student_nos", nargs="*", help="要同步的学号（默认全库）")
    parser.add_argument("--admin", default=None, help="只同步这个管理员名下的成员")
    parser.add_argument("--db", default=None)
    args = parser.parse_args()

    settings = Settings.from_env()
    db_path = Path(args.db) if args.db else settings.db_path
    engine = make_engine(db_path)
    init_db(engine)
    factory = make_session_factory(engine)
    passwords = load_passwords()

    verifier = JiaowuVerifier()
    ok_count = failed = 0

    with factory() as db:
        query = select(User)
        if args.student_nos:
            query = query.where(User.student_no.in_(args.student_nos))
        elif args.admin:
            admin = db.scalar(select(User).where(User.student_no == args.admin))
            if admin is None:
                print(f"× 找不到管理员 {args.admin}")
                return 1
            query = query.where(User.owner_admin_id == admin.id)
        users = list(db.scalars(query.order_by(User.student_no)).all())

        print(f"数据库：{db_path}  待同步 {len(users)} 个账号")
        for user in users:
            password = passwords.get(user.student_no)
            if not password:
                print(f"  - {user.student_no}: users.txt 里没有密码，跳过")
                failed += 1
                continue
            try:
                bundle = verifier.login(user.student_no, password)
            except JiaowuError as exc:
                print(f"  - {user.student_no}: 登录失败 —— {exc}")
                failed += 1
                continue
            if not bundle.ok:
                print(f"  - {user.student_no}: 登录失败 —— {bundle.reason}")
                failed += 1
                continue

            old_name = user.name
            if bundle.name:
                user.name = bundle.name
            if bundle.parse is not None:
                count = timetable_service.store_courses(
                    db, user, settings.semester, bundle.parse
                )
                user.last_parse_notice = "; ".join(bundle.parse.warnings)[:1000]
            else:
                count = 0
            if bundle.cookies:
                user.jiaowu_cookies = json.dumps(bundle.cookies, ensure_ascii=False)
            db.commit()
            ok_count += 1
            print(f"  ✓ {user.student_no}: {old_name or '(空)'} → {user.name or '(空)'}，"
                  f"课表 {count} 条")

    print(f"\n完成：成功 {ok_count}，跳过/失败 {failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
