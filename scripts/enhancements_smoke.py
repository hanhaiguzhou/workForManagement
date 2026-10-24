"""排班页增强（zip/周次校正/卡片/十字对位）HTTP 冒烟测试。spec 见 deploy/SPEC-schedule-enhancements.md。

seam：HTTP 层（create_app + TestClient + 临时 SQLite + stub 教务）。
周次锚点纯函数用字面量日期直测；其余走 HTTP 全流程。

运行：docker exec -e TEACHER_SMOKE_PW=... -i duty-scheduler python - < scripts/enhancements_smoke.py
"""
import io
import os
import re
import tempfile
import zipfile
from datetime import date
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy import select as sa_select

from app.config import Settings
from app.models import User
from app.services import weeknumber
from app.web.app import create_app

SUPER_NO = "2025000001"
TEACHER_NO = "20259990001"
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
    # 冒烟需要一个值班表模板：容器里挂载你自己的 Excel（约定见 app/xlsx.py），
    # 仓库不附带模板文件。
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


def csrf_of(client_, path):
    html = client_.get(path).text
    m = re.search(r'name="csrf-token" content="([^"]+)"', html)
    return m.group(1) if m else ""


def run_schedule(client_, week, max_pp="2"):
    return client_.post("/admin/schedule/run", data={
        "week": str(week), "max_per_person": max_pp, "special_person_id": "",
        "blacklist": "", "csrf": csrf_of(client_, "/admin/schedule"),
    }, follow_redirects=False)


# ================= 周次锚点纯函数 =================
print("== 周次锚点纯函数 ==")
check("周一计算：2026-10-28（三）的周一是 10-26",
      weeknumber.monday_of(date(2026, 10, 28)) == date(2026, 10, 26),
      str(weeknumber.monday_of(date(2026, 10, 28))))
check("锚点周次：锚 10-26=第3周 时 11-02 是第 4 周",
      weeknumber.week_for(date(2026, 11, 2), date(2026, 10, 26), 3) == 4,
      str(weeknumber.week_for(date(2026, 11, 2), date(2026, 10, 26), 3)))
check("锚点周次：锚 10-26=第3周 时 10-19（上上周）是第 2 周",
      weeknumber.week_for(date(2026, 10, 19), date(2026, 10, 26), 3) == 2,
      str(weeknumber.week_for(date(2026, 10, 19), date(2026, 10, 26), 3)))
check("锚点即锚周当天 10-26 = 第 3 周",
      weeknumber.week_for(date(2026, 10, 26), date(2026, 10, 26), 3) == 3,
      str(weeknumber.week_for(date(2026, 10, 26), date(2026, 10, 26), 3)))

# ================= 登录与基线 =================
print("== 登录与基线 ==")
login(s_client, SUPER_NO, "super-pw")
login(u_client, STU_NO, "stu-pw")
# 学生先走完审批流程（选超管部门 + 超管通过），否则 /me 会 303 到 /pending
r = u_client.post("/pending/choose", data={
    "admin_id": str(1), "csrf": csrf_of(u_client, "/pending"),
}, follow_redirects=False)
check("学生选部门 303", r.status_code == 303, f"got {r.status_code}")
from app.models import User as UserModel
engine = create_engine(f"sqlite:///{settings.db_path}")
with engine.connect() as conn:
    stu_id = conn.execute(sa_select(UserModel.__table__.c.id).where(
        UserModel.__table__.c.student_no == STU_NO)).scalar()
engine.dispose()
r = s_client.post("/admin/decide", data={
    "user_id": str(stu_id), "decision": "approve",
    "csrf": csrf_of(s_client, "/admin"),
}, follow_redirects=False)
check("超管审批学生 303", r.status_code == 303, f"got {r.status_code}")
r = u_client.get("/me")
m = re.search(r"我的课表 · 第 (\d+) 周", r.text)
baseline_week = int(m.group(1)) if m else -1
check(f"学生页基线周次可读出（{baseline_week}）", baseline_week > 0, r.text[:200])

# ================= 周次校正（全局生效） =================
print("== 周次校正 ==")
target = 3
r = s_client.post("/admin/schedule/week-calibrate", data={
    "week": str(target), "csrf": csrf_of(s_client, "/admin/schedule"),
}, follow_redirects=False)
check("校正保存 303", r.status_code == 303, f"got {r.status_code}")
r = u_client.get("/me")
m = re.search(r"我的课表 · 第 (\d+) 周", r.text)
check(f"学生页周次变为 {target}（全局生效）",
      m and int(m.group(1)) == target, r.text[:200])
r = s_client.get("/admin/schedule")
check(f"排班页周次变为 {target}",
      f"一键排班 · 第 {target} 周" in r.text, "")
r = s_client.get("/admin/overview")
check(f"总览页周次变为 {target}",
      f"全员总览 · 第 {target} 周" in r.text, "")
# 日期标签与编号同口径：校正"本周=第3周"后，看第 3 周，
# 表头日期应显示当前自然周（今天 10-24 所在周，周一 10-19）——锚点平移了学期起点
r = u_client.get("/me?week=3")
check("第3周日期标签跟随校正（当前自然周，19 日）",
      "19</span>" in r.text, "day-19 label missing")
# 改回
r = s_client.post("/admin/schedule/week-calibrate", data={
    "week": str(baseline_week), "csrf": csrf_of(s_client, "/admin/schedule"),
}, follow_redirects=False)
r = u_client.get("/me")
m = re.search(r"我的课表 · 第 (\d+) 周", r.text)
check("改回后学生页周次恢复", m and int(m.group(1)) == baseline_week, "")
r = s_client.post("/admin/schedule/week-calibrate", data={
    "week": "0", "csrf": csrf_of(s_client, "/admin/schedule"),
}, follow_redirects=False)
check("非法周次 0 被拒 400", r.status_code == 400, f"got {r.status_code}")
r = u_client.post("/admin/schedule/week-calibrate", data={
    "week": "5", "csrf": csrf_of(u_client, "/me"),
}, follow_redirects=False)
check("学生无权校正 403/303", r.status_code in (403, 303), f"got {r.status_code}")

# ================= 历史周期 zip =================
print("== 历史周期 zip ==")
# 当前周期排两周 → 造一个历史周期：直接把一条存档的 cycle_start 改早（用服务层）
from app.models import ExportArchive
from app.services import archives as archives_svc

login(s_client, SUPER_NO, "super-pw")
run_schedule(s_client, 7)
run_schedule(s_client, 8)
current_start = archives_svc.current_cycle_start()
prev_start = archives_svc.shift_cycle(current_start, -1)
engine = create_engine(f"sqlite:///{settings.db_path}")
with engine.begin() as conn:
    conn.execute(
        ExportArchive.__table__.update()
        .where(ExportArchive.__table__.c.week_no == 7)
        .values(cycle_start=prev_start)
    )
engine.dispose()

r = s_client.get("/admin/schedule")
check("页面出现历史周期折叠块", "历史周期" in r.text and "1 份" in r.text, "")
check("历史周期为独立折叠块（details）", r.text.count("<details") >= 1, "")
check("页面出现本周一键下载按钮", "本周一键下载" in r.text, "")

from urllib.parse import quote
r = s_client.get(f"/admin/schedule/archives/zip?cycle={prev_start.isoformat()}")
check("历史周期 zip 200", r.status_code == 200, f"got {r.status_code}")
check("Content-Type 是 zip", "zip" in r.headers.get("content-type", ""),
      r.headers.get("content-type", ""))
zf = zipfile.ZipFile(io.BytesIO(r.content))
names = zf.namelist()
check("zip 内 1 个条目（被搬到历史周期的第7周）", len(names) == 1, f"names={names}")
check("zip 条目名 = 存档文件名", names and names[0].startswith("勤工助学值班表"),
      f"names={names}")
engine = create_engine(f"sqlite:///{settings.db_path}")
with engine.connect() as conn:
    from sqlalchemy import select as sa_select
    row = conn.execute(sa_select(ExportArchive.__table__).where(
        ExportArchive.__table__.c.week_no == 7)).first()
engine.dispose()
check("zip 内字节与存档一致", names and zf.read(names[0]) == row.xlsx, "bytes differ")
r = s_client.get(f"/admin/schedule/archives/zip?cycle={archives_svc.shift_cycle(prev_start, -3).isoformat()}")
check("空周期 zip 404", r.status_code == 404, f"got {r.status_code}")
r = u_client.get(f"/admin/schedule/archives/zip?cycle={prev_start.isoformat()}", follow_redirects=False)
check("学生请求 zip 被拒", r.status_code in (403, 303), f"got {r.status_code}")
login(t_client, TEACHER_NO, TEACHER_PW)
r = t_client.get(f"/admin/schedule/archives/zip?cycle={prev_start.isoformat()}")
check("老师可下载同周期 zip 200", r.status_code == 200, f"got {r.status_code}")

# ================= 表格自适应与十字对位（资源断言） =================
print("== 前端资源 ==")
css = s_client.get("/static/style.css?v=2.14").text
check("总体导出文件名列换行样式存在", "archives-table" in css and "break-all" in css, "")
check("十字对位样式存在", "hl-up" in css and "hl-head" in css, "")
js = s_client.get("/static/ui.js?v=2.15").text
check("十字对位 JS 存在", "hl-self" in js, "")
check("十字对位覆盖课表与总览（table.grid）",
      "querySelectorAll('table.grid')" in js, "")
html = s_client.get("/me").text
check("学生页格子带 data 属性（对位用）", "grid-desktop" in html, "")

print()
if failed:
    print(f"FAILED {len(failed)}/{ok + len(failed)}: {'; '.join(failed)}")
    raise SystemExit(1)
print(f"ALL PASSED ({ok} checks)")
