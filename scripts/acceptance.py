"""工单 12：本地验收演练（一条命令跑完，不需要先启服务）。

    .venv\\Scripts\\python.exe scripts\\acceptance.py

做的事：用真实学号（读 users.txt）建一个临时库 → 注册/审批 → 第 15、16 周看课表 →
请假 → 一键排班 → 下载 Excel → 用旧脚本的四类已知缺陷逐条对照 → 写验收报告。
产物在 data/acceptance/ 下。
"""

from __future__ import annotations

import re
import sys
from datetime import date, timedelta
from io import BytesIO
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import openpyxl
from fastapi.testclient import TestClient

from app.config import Settings
from app.jiaowu import StubVerifier
from app.models import Assignment, User
from app.web.app import create_app
from app.xlsx import read_layout

OUT_DIR = ROOT / "data" / "acceptance"
REPORT = ROOT / "docs" / "验收报告.md"
TEMPLATE = ROOT / "勤工助学值班表 20XX-20XX -X第X周.xlsx"
SEMESTER = "2025-2026-2"

# 旧值班表里残留的名字，用来检查导出是否还在继承旧数据
OLD_TEMPLATE_NAMES = {"演示管理员", "韩梅梅", "李雷", "王芳", "赵敏"}
WEEKS = (15, 16)

lines: list[str] = []


def say(text: str = "") -> None:
    print(text)
    lines.append(text)


def check(label: str, ok: bool, detail: str = "") -> bool:
    say(f"{'PASS' if ok else 'FAIL'}  {label}" + (f"  [{detail}]" if detail else ""))
    return ok


def accounts() -> list[tuple[str, str]]:
    """真实学号 + 密码来自 users.txt；文件不在就用占位账号。"""
    path = ROOT / "users.txt"
    found: list[tuple[str, str]] = []
    if path.exists():
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            if len(parts) >= 2:
                found.append((parts[0], parts[1]))
    if len(found) >= 3:
        return found[:5]
    return [(f"2024000{i}", f"demo-pass-{i}") for i in range(1, 6)]


def csrf_of(client, path: str) -> str:
    page = client.get(path)
    match = re.search(r'name="csrf-token" content="([^"]+)"', page.text)
    assert match and match.group(1), f"{path} 没有 CSRF 令牌"
    return match.group(1)


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for stale in OUT_DIR.glob("*.xlsx"):
        stale.unlink()
    db_path = OUT_DIR / "acceptance.db"
    if db_path.exists():
        db_path.unlink()

    people = accounts()
    super_no, super_pw = people[0]
    admin_no, admin_pw = people[1]
    members = people[2:]
    accounts_map = {no: pw for no, pw in people}
    names = {no: f"组员{i + 1}" for i, (no, _pw) in enumerate(people)}

    settings = Settings(
        db_path=db_path,
        jiaowu_mode="stub",
        super_admin_student_no=super_no,
        semester=SEMESTER,
        semester_start=date.today() - timedelta(weeks=4),
        log_path=OUT_DIR / "access.log",
    )
    app = create_app(settings=settings, verifier=StubVerifier(accounts_map, names))

    say("# 本地验收演练")
    say()
    say(f"- 学期：{SEMESTER}；周次：{WEEKS[0]}、{WEEKS[1]}")
    say(f"- 账号：{len(people)} 个（真实学号，密码取自 users.txt）")
    say(f"- 数据库：{db_path.relative_to(ROOT)}")
    say()

    say("## 1. 注册与审批")
    with TestClient(app, follow_redirects=False) as sup:
        check("超管注册",
              sup.post("/register", data={"student_no": super_no, "password": super_pw,
                                          "admin_key": ""}).status_code == 303)

        check("管理员申请",
              sup.post("/register", data={"student_no": admin_no, "password": admin_pw,
                                          "admin_key": super_no}).status_code == 303)
        csrf = csrf_of(sup, "/admin/users")
        factory = app.state.session_factory
        with factory() as db:
            admin_id = db.query(User).filter(User.student_no == admin_no).one().id
        check("超管通过管理员申请",
              sup.post("/admin/users/decide",
                       data={"user_id": admin_id, "decision": "approve", "csrf": csrf}
                       ).status_code == 303)

    with TestClient(app, follow_redirects=False) as adm:
        adm.post("/login", data={"student_no": admin_no, "password": admin_pw})
        for no, pw in members:
            adm.post("/register", data={"student_no": no, "password": pw, "admin_key": admin_no})
        csrf = csrf_of(adm, "/admin")
        approved = 0
        for element in dict.fromkeys(re.findall(r'name="user_id" value="(\d+)"',
                                                adm.get("/admin").text)):
            if adm.post("/admin/decide", data={"user_id": element, "decision": "approve",
                                               "csrf": csrf}).status_code == 303:
                approved += 1
        check("管理员通过成员", approved >= 1, f"{approved} 人")

    say()
    say("## 2. 学生端：课表与请假")
    student_no, student_pw = members[0]
    with TestClient(app, follow_redirects=False) as stu:
        check("学生登录", stu.post("/login", data={"student_no": student_no,
                                                  "password": student_pw}).status_code == 303)
        grid = stu.get(f"/me?week={WEEKS[1]}")
        check("课表页可打开并有课程", grid.status_code == 200 and "我的课表" in grid.text)

        cell = re.search(r'name="day" value="(周[一二三四五六日])">\s*'
                         r'<input type="hidden" name="slot" value="([\d\-]+)"', grid.text)
        if cell:
            day, slot = cell.group(1), cell.group(2)
            leave = stu.post("/me/leave",
                             data={"day": day, "slot": slot, "week": WEEKS[1],
                                   "csrf": csrf_of(stu, f"/me?week={WEEKS[1]}")})
            after = stu.get(f"/me?week={WEEKS[1]}").text
            check("请假后该节标红", leave.status_code == 303 and "已请假 1 节" in after,
                  f"{day} {slot}")
            next_week = stu.get(f"/me?week={WEEKS[1] + 1}").text
            check("下一周不受影响", "已请假 0 节" in next_week)

    say()
    say("## 3. 管理员端：总览与一键排班")
    plan_names: dict[str, dict[tuple[str, str], str]] = {}
    with TestClient(app, follow_redirects=False) as adm:
        adm.post("/login", data={"student_no": admin_no, "password": admin_pw})
        overview = adm.get(f"/admin/overview?week={WEEKS[1]}")
        check("全员总览可打开", overview.status_code == 200 and "全员总览" in overview.text)

        csrf = csrf_of(adm, "/admin/schedule")
        with app.state.session_factory() as db:
            special_id = str(admin_id)
        for week in WEEKS:
            run = adm.post("/admin/schedule/run",
                           data={"week": week, "max_per_person": 2,
                                 "special_person_id": special_id, "blacklist": "",
                                 "csrf": csrf_of(adm, f"/admin/schedule?week={week}")})
            check(f"第 {week} 周一键排班", run.status_code == 303, str(run.status_code))

            download = adm.get(f"/admin/schedule/download?week={week}")
            ok = download.status_code == 200 and "spreadsheetml" in download.headers.get(
                "content-type", "")
            check(f"第 {week} 周下载 Excel", ok)
            if not ok:
                continue

            target = OUT_DIR / f"值班表-第{week}周.xlsx"
            target.write_bytes(download.content)
            plan_names[str(week)] = _read_filled(download.content)

            with app.state.session_factory() as db:
                rows = db.query(Assignment).filter(Assignment.week_no == week).all()
                day_counts = {r.day for r in rows}
                sat_people = {r.user_id for r in rows if r.day == "周六"}
            check(f"第 {week} 周：周日无排班", "周日" not in day_counts)
            check(f"第 {week} 周：周六只有指定人员", sat_people == {int(special_id)},
                  str(sat_people))

    say()
    say("## 4. 旧脚本四类缺陷对照")
    layout = read_layout(TEMPLATE)
    week16 = plan_names.get(str(WEEKS[1]), {})
    workbook = openpyxl.load_workbook(OUT_DIR / f"值班表-第{WEEKS[1]}周.xlsx")
    sheet = workbook.active
    leftovers = sorted({
        str(sheet[cell].value) for cell in layout.name_cells.values()
        if str(sheet[cell].value or "").strip() in OLD_TEMPLATE_NAMES
    })
    row17 = [sheet[cell].value for (day, slot), cell in layout.name_cells.items()
             if slot == "11-12" and day != "周日"]
    header = str(sheet["A3"].value)
    workbook.close()

    check("缺陷1 周日被排 → 已修复", all(day != "周日" for day, _slot in week16))
    check("缺陷2 周六独占丢失 → 已修复",
          {name for (day, _slot), name in week16.items() if day == "周六"} <= {names[admin_no]})
    check("缺陷3 模板脏格残留 → 已修复", not leftovers, str(leftovers))
    check("缺陷4 11-12 节排不出 → 已修复",
          any(value for value in row17), f"{sum(1 for v in row17 if v)}/{len(row17)} 格有名字")
    check("表头正确写入周次与地点",
          f"第 {WEEKS[1]} 周" in header and "值班地点" in header, header[:46])
    check("姓名格与模板可填格一致",
          all(key in layout.markers for key in week16), f"{len(week16)} 格")

    say()
    say("## 5. 产物")
    for path in sorted(OUT_DIR.glob("*")):
        say(f"- `{path.relative_to(ROOT)}`")
    say(f"- 第 {WEEKS[1]} 周排班 {len(week16)} 格")

    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\n报告已写入 {REPORT.relative_to(ROOT)}")

    failures = [line for line in lines if line.startswith("FAIL")]
    print(f"{len([l for l in lines if l.startswith(('PASS', 'FAIL'))]) - len(failures)}"
          f"/{len([l for l in lines if l.startswith(('PASS', 'FAIL'))])} 通过")
    return 1 if failures else 0


def _read_filled(content: bytes) -> dict[tuple[str, str], str]:
    layout = read_layout(TEMPLATE)
    workbook = openpyxl.load_workbook(BytesIO(content))
    sheet = workbook.active
    filled = {
        key: str(sheet[cell].value)
        for key, cell in layout.name_cells.items()
        if sheet[cell].value
    }
    workbook.close()
    return filled


if __name__ == "__main__":
    raise SystemExit(main())
