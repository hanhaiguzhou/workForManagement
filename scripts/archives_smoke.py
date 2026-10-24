"""总体导出（周期存档）HTTP 冒烟测试。spec 见 deploy/SPEC-cycle-exports.md。

seam：HTTP 层（create_app 直接构造 Settings + TestClient，stub 教务 + 临时库）。
周期纯函数的边界用字面量日期直测；存档行为走排班→卡片→下载全流程。

运行：docker exec -i duty-scheduler python - < scripts/archives_smoke.py
"""
import os
import re
import tempfile
from datetime import date
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from app.config import Settings
from app.models import Assignment, ExportArchive, User
from app.services import archives
from app.web.app import create_app
from app.xlsx import schedule_filename

SUPER_NO = "2025000001"
TEACHER_NO = "20259990001"
# 老师密码不写在本文件里（与部署配置同一来源）：环境变量注入，缺省占位
TEACHER_PW = os.environ.get("TEACHER_SMOKE_PW", "x-test-only-placeholder")
STU_NO = "2025000002"

ok = 0
failed: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    global ok
    if cond:
        ok += 1
        print(f"  PASS  {name}")
    else:
        failed.append(name)
        print(f"  FAIL  {name}   {detail}")


tmp = Path(tempfile.mkdtemp())
settings = Settings(
    db_path=tmp / "app.db",
    jiaowu_mode="stub",
    super_admin_student_no=SUPER_NO,
    stub_accounts={SUPER_NO: "super-pw", STU_NO: "stu-pw"},
    teacher_accounts={TEACHER_NO: {"password": TEACHER_PW, "name": "演示老师"}},
    semester="2026-2027-1",
    semester_start=date(2026, 9, 7),
    template_path=Path("/app/template.xlsx"),
)


def fresh_client() -> TestClient:
    return TestClient(create_app(settings))


s_client = fresh_client()
t_client = fresh_client()
u_client = fresh_client()


def login(client_, no, pw):
    return client_.post("/login", data={"student_no": no, "password": pw},
                        follow_redirects=False)


def user_row(no):
    engine = create_engine(f"sqlite:///{settings.db_path}")
    with engine.connect() as conn:
        row = conn.execute(
            User.__table__.select().where(User.__table__.c.student_no == no)).first()
    engine.dispose()
    return row


def uid(no):
    return user_row(no).id


def csrf_of(client_, path):
    html = client_.get(path).text
    m = re.search(r'name="csrf-token" content="([^"]+)"', html)
    return m.group(1) if m else ""


def run_schedule(client_, week):
    return client_.post("/admin/schedule/run", data={
        "week": str(week), "max_per_person": "2", "special_person_id": "",
        "blacklist": "", "csrf": csrf_of(client_, "/admin/schedule"),
    }, follow_redirects=False)


def archive_rows():
    engine = create_engine(f"sqlite:///{settings.db_path}")
    with engine.connect() as conn:
        rows = conn.execute(ExportArchive.__table__.select()
                            .order_by(ExportArchive.__table__.c.week_no)).all()
    engine.dispose()
    return rows


# ================= 周期纯函数（字面量边界） =================
print("== 周期函数边界 ==")
check("10月24日 → 10月周期，起点9月25日",
      archives.cycle_for(date(2026, 10, 24)) == (date(2026, 9, 25), "2026年10月"),
      str(archives.cycle_for(date(2026, 10, 24))))
check("10月25日 → 11月周期，起点10月25日",
      archives.cycle_for(date(2026, 10, 25)) == (date(2026, 10, 25), "2026年11月"),
      str(archives.cycle_for(date(2026, 10, 25))))
check("11月24日 → 11月周期",
      archives.cycle_for(date(2026, 11, 24)) == (date(2026, 10, 25), "2026年11月"),
      str(archives.cycle_for(date(2026, 11, 24))))
check("11月25日 → 12月周期",
      archives.cycle_for(date(2026, 11, 25)) == (date(2026, 11, 25), "2026年12月"),
      str(archives.cycle_for(date(2026, 11, 25))))
check("跨年：12月25日 → 次年1月周期",
      archives.cycle_for(date(2026, 12, 25)) == (date(2026, 12, 25), "2027年1月"),
      str(archives.cycle_for(date(2026, 12, 25))))
check("跨年：1月10日 → 1月周期，起点上年12月25日",
      archives.cycle_for(date(2027, 1, 10)) == (date(2026, 12, 25), "2027年1月"),
      str(archives.cycle_for(date(2027, 1, 10))))
check("周期区间：2026-10-25 起点的终点是 11-25",
      archives.cycle_range(date(2026, 10, 25)) == (date(2026, 10, 25), date(2026, 11, 25)),
      str(archives.cycle_range(date(2026, 10, 25))))
check("周期平移 -1/+1",
      archives.shift_cycle(date(2026, 10, 25), -1) == date(2026, 9, 25)
      and archives.shift_cycle(date(2026, 10, 25), 1) == date(2026, 11, 25)
      and archives.shift_cycle(date(2026, 12, 25), 1) == date(2027, 1, 25),
      "shift broken")

# ================= HTTP 全流程 =================
print("== 排班自动存档 ==")
login(s_client, SUPER_NO, "super-pw")
r = run_schedule(s_client, 7)
check("排第7周 303", r.status_code == 303, f"got {r.status_code}")
rows = archive_rows()
check("第7周自动存档 1 条", len(rows) == 1 and rows[0].week_no == 7,
      f"rows={[(r.week_no, r.filename) for r in rows]}")
expected_name = schedule_filename(7, settings.semester)
check("文件名与下载一致", rows and rows[0].filename == expected_name,
      f"{getattr(rows[0], 'filename', None)!r} != {expected_name!r}")
check("周期起点 = 当前周期", rows and rows[0].cycle_start == archives.current_cycle_start(),
      f"{getattr(rows[0], 'cycle_start', None)}")

r = run_schedule(s_client, 8)
check("排第8周 303", r.status_code == 303, f"got {r.status_code}")
rows = archive_rows()
check("两周两条存档", len(rows) == 2 and {r.week_no for r in rows} == {7, 8},
      f"rows={[(r.week_no) for r in rows]}")

print("== 同周重排只留最新 ==")
old_created = [r for r in archive_rows() if r.week_no == 7][0].created_at
r = run_schedule(s_client, 7)
check("重排第7周 303", r.status_code == 303, f"got {r.status_code}")
rows = archive_rows()
week7 = [r for r in rows if r.week_no == 7]
check("仍只有一条第7周存档（旧的已删）", len(rows) == 2 and len(week7) == 1,
      f"total={len(rows)} week7={len(week7)}")
check("第7周存档时间已更新", week7 and week7[0].created_at > old_created,
      f"{old_created} -> {week7[0].created_at if week7 else None}")

print("== 卡片展示 ==")
r = s_client.get("/admin/schedule")
check("排班页 200", r.status_code == 200, f"got {r.status_code}")
check("页面含总体导出卡片", "总体导出" in r.text, "card missing")
check("页面列出存档文件名", expected_name in r.text, "filename missing")
check("页面含周期标签", "年" in r.text and "月" in r.text and "周期" in r.text,
      "cycle label missing")
check("上个周期入口存在", "上个周期" in r.text, "prev cycle link missing")

print("== 存档下载 ==")
target = [r for r in archive_rows() if r.week_no == 7][0]
r = s_client.get(f"/admin/schedule/archives/{target.id}/download")
check("超管下载存档 200", r.status_code == 200, f"got {r.status_code}")
check("下载内容与存档字节一致", r.content == target.xlsx, "bytes differ")
check("下载头带原文件名", "filename*" in r.headers.get("content-disposition", ""),
      "no content-disposition")
r = s_client.get("/admin/schedule/download?week=7")
check("原有当周下载回归 200", r.status_code == 200, f"got {r.status_code}")

print("== 老师代管同集合 ==")
login(t_client, TEACHER_NO, TEACHER_PW)
r = t_client.get("/admin/schedule")
check("老师排班页 200 且看到存档", r.status_code == 200 and expected_name in r.text,
      f"got {r.status_code}")
r = t_client.get(f"/admin/schedule/archives/{target.id}/download")
check("老师可下载同一存档 200", r.status_code == 200, f"got {r.status_code}")

print("== 权限隔离 ==")
login(u_client, STU_NO, "stu-pw")
r = u_client.get(f"/admin/schedule/archives/{target.id}/download", follow_redirects=False)
check("学生下载存档被拒 403/303", r.status_code in (403, 303), f"got {r.status_code}")
other = s_client.get("/admin/schedule/archives/999999/download", follow_redirects=False)
check("不存在的存档 404", other.status_code == 404, f"got {other.status_code}")

print("== 空排班周不谎报存档 ==")
before_count = len(archive_rows())
r = s_client.post("/admin/schedule/run", data={
    "week": "9", "max_per_person": "0", "special_person_id": "",
    "blacklist": "", "csrf": csrf_of(s_client, "/admin/schedule"),
}, follow_redirects=False)
check("零班次排班 303", r.status_code == 303, f"got {r.status_code}")
from urllib.parse import unquote
check("消息如实说明未存档",
      "未生成存档" in unquote(r.headers.get("location", "").split("msg=")[-1]),
      f"location={r.headers.get('location')}")
check("没有为空周建档", len(archive_rows()) == before_count,
      f"{before_count} -> {len(archive_rows())}")

print()
if failed:
    print(f"FAILED {len(failed)}/{ok + len(failed)}: {'; '.join(failed)}")
    raise SystemExit(1)
print(f"ALL PASSED ({ok} checks)")
