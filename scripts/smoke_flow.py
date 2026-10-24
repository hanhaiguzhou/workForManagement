"""本地冒烟（第二阶段）：注册 → 审批 → 课表 → 请假 全链路。

用法：.venv\\Scripts\\python.exe scripts\\smoke_flow.py [base_url]
需要 dev server 以 JIAOWU_MODE=stub 运行，且数据库里还没有这些账号。
"""

from __future__ import annotations

import sys

import httpx2 as httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"

SUPER_NO, SUPER_PW = "2025000001", "demo-pass-1"
ADMIN_NO, ADMIN_PW = "2025000002", "demo-pass-2"
STU_NO, STU_PW = "2025000003", "demo-pass-3"

results: list[tuple[str, bool, str]] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    results.append((label, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {label}" + (f"  [{detail}]" if detail else ""))


def main() -> int:
    with httpx.Client(base_url=BASE, follow_redirects=False, timeout=10) as sup:
        # 1) 超管注册（引导口）
        r = sup.post("/register", data={"student_no": SUPER_NO, "password": SUPER_PW, "admin_key": ""})
        if r.status_code == 409:
            print("数据库里已经有这些账号了。这是首跑脚本，请先删掉 data/dev.db 并重启服务再运行。")
            return 2
        check("超管注册", r.status_code == 303, str(r.status_code))

        # 2) 管理员申请（用超管学号作为标识）→ 超管审批
        with httpx.Client(base_url=BASE, follow_redirects=False, timeout=10) as cand:
            r = cand.post("/register", data={"student_no": ADMIN_NO, "password": ADMIN_PW, "admin_key": SUPER_NO})
            check("管理员申请提交", r.status_code == 303, str(r.status_code))

        page = sup.get("/admin/users")
        check("超管看到管理员申请", ADMIN_NO in page.text)
        admin_id = _find_user_id(sup, ADMIN_NO)
        r = sup.post("/admin/users/decide",
                     data={"user_id": admin_id, "decision": "approve", "csrf": _csrf(sup, "/admin/users")})
        check("超管通过管理员申请", r.status_code == 303, str(r.status_code))

        # 3) 学生注册到该部门 → 管理员审批
        with httpx.Client(base_url=BASE, follow_redirects=False, timeout=10) as stu:
            r = stu.post("/register", data={"student_no": STU_NO, "password": STU_PW, "admin_key": ADMIN_NO})
            check("学生注册到管理员名下", r.status_code == 303, str(r.status_code))

        with httpx.Client(base_url=BASE, follow_redirects=False, timeout=10) as adm:
            r = adm.post("/login", data={"student_no": ADMIN_NO, "password": ADMIN_PW})
            check("管理员登录", r.headers.get("location") == "/admin", r.headers.get("location", ""))
            page = adm.get("/admin")
            check("管理员看到待审成员", STU_NO in page.text)

            stu_id = _find_user_id(sup, STU_NO)
            r = adm.post("/admin/decide",
                         data={"user_id": stu_id, "decision": "approve", "csrf": _csrf(adm, "/admin")})
            check("管理员通过成员", r.status_code == 303, str(r.status_code))

        # 4) 学生登录 → 课表 → 请假
        with httpx.Client(base_url=BASE, follow_redirects=False, timeout=10) as stu:
            r = stu.post("/login", data={"student_no": STU_NO, "password": STU_PW})
            check("学生登录落到 /me", r.headers.get("location") == "/me", r.headers.get("location", ""))

            me = stu.get("/me?week=5")
            check("课表页渲染成功", me.status_code == 200 and "我的课表" in me.text)
            cell = _first_leave_cell(me.text)
            check("有可请假的课余格子", cell is not None, str(cell))

            if cell:
                day, slot = cell
                r = stu.post("/me/leave",
                             data={"day": day, "slot": slot, "week": 5, "csrf": _csrf(stu, "/me?week=5")})
                check("点请假提交成功", r.status_code == 303, str(r.status_code))

                after = stu.get("/me?week=5")
                check("请假后该格标红", "on-leave" in after.text and "已请假 1 节" in after.text)

                later = stu.get("/me?week=6")
                check("下一周不受影响", "已请假 0 节" in later.text)

    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)}/{len(results)} 通过")
    return 1 if failed else 0


def _csrf(client, path: str) -> str:
    """从页面 meta 里取 CSRF 令牌（表单提交必须带上）。"""
    import re

    page = client.get(path)
    match = re.search(r'name="csrf-token" content="([^"]+)"', page.text)
    assert match and match.group(1), f"{path} 页面没有 CSRF 令牌"
    return match.group(1)


def _find_user_id(client, student_no: str) -> int:
    """本地冒烟脚本直接查 sqlite 拿 id（页面上的隐藏域只对待审项存在）。"""
    import os
    import sqlite3

    db_path = os.environ.get("APP_DB_PATH", "data/dev.db")
    with sqlite3.connect(db_path) as conn:
        row = conn.execute("SELECT id FROM users WHERE student_no = ?", (student_no,)).fetchone()
    if row is None:
        raise SystemExit(f"数据库里没有 {student_no}")
    return int(row[0])


def _first_leave_cell(html: str) -> tuple[str, str] | None:
    """页面上第一个可请假的格子（请假按钮只出现在课余时间格子上）。"""
    import re

    m = re.search(r'name="day" value="(周[一二三四五六日])">\s*<input type="hidden" name="slot" value="([\d\-]+)"', html)
    if m:
        return m.group(1), m.group(2)
    return None


if __name__ == "__main__":
    raise SystemExit(main())
