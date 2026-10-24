"""老师角色（teacher）HTTP 冒烟测试。spec 见 deploy/SPEC-teacher-role.md。

seam：HTTP 层（create_app 直接构造 Settings + TestClient）。
学生流程走 stub 教务（JIAOWU_MODE=stub），老师流程走本地凭据配置，
数据库用临时文件，不碰生产库。

老师密码不写在本文件里（spec：密码只存在于部署配置）：
运行时用环境变量 TEACHER_SMOKE_PW 注入真实密码，缺省用占位密码。
运行：docker exec -e TEACHER_SMOKE_PW=... -i duty-scheduler python - < scripts/teacher_role_smoke.py
（依赖 httpx：容器内 pip install --no-cache-dir httpx）
"""
import os
import re
import tempfile
from datetime import date
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from app.config import Settings
from app.models import Assignment, ScheduleSettings, User
from app.web.app import create_app

SUPER_NO = "2025000001"
TEACHER_NO = "20259990001"
TEACHER_PW = os.environ.get("TEACHER_SMOKE_PW", "x-test-only-placeholder")
STU_NO = "2025000002"
STU2_NO = "2025000003"

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
    stub_accounts={SUPER_NO: "super-pw", STU_NO: "stu-pw", STU2_NO: "stu2-pw"},
    # 防冒充用例：故意把 STU_NO 也放进老师凭据，验证已有学生账号不会被本地密码登进去
    teacher_accounts={
        TEACHER_NO: {"password": TEACHER_PW, "name": "演示老师"},
        STU_NO: {"password": "teacher-pw-should-not-work", "name": "冒充者"},
    },
    semester="2026-2027-1",
    semester_start=date(2026, 9, 7),
    # 默认模板缺周六11-12 格，用部门上传过的完整模板跑排班
    template_path=Path("/app/template.xlsx"),
)


def fresh_client() -> TestClient:
    """独立会话（cookie 互不干扰），共用同一个数据库。"""
    return TestClient(create_app(settings))


t_client = fresh_client()   # 老师
s_client = fresh_client()   # 超管
u_client = fresh_client()   # 学生 STU
stu2_client = fresh_client()  # 学生 STU2


def login(client_: TestClient, no: str, pw: str):
    return client_.post("/login", data={"student_no": no, "password": pw},
                        follow_redirects=False)


def user_row(no: str):
    engine = create_engine(f"sqlite:///{settings.db_path}")
    with engine.connect() as conn:
        row = conn.execute(
            User.__table__.select().where(User.__table__.c.student_no == no)
        ).first()
    engine.dispose()
    return row


def uid(no: str) -> int:
    return user_row(no).id


def csrf_of(client_: TestClient, path: str) -> str:
    html = client_.get(path).text
    m = re.search(r'name="csrf-token" content="([^"]+)"', html)
    return m.group(1) if m else ""


def assignments_of(admin_id: int):
    engine = create_engine(f"sqlite:///{settings.db_path}")
    with engine.connect() as conn:
        rows = conn.execute(
            Assignment.__table__.select().where(
                Assignment.__table__.c.admin_id == admin_id)
        ).all()
    engine.dispose()
    return rows


# ================= T2 登录 =================
print("== T2 本地老师登录 ==")
# 超管先存在（生产里必然如此：env 引导账号早就建好），老师代管才能找到对象
r = login(s_client, SUPER_NO, "super-pw")
check("超管引导登录 303", r.status_code == 303, f"got {r.status_code}")
r = login(t_client, TEACHER_NO, TEACHER_PW)
check("老师首登 303", r.status_code == 303, f"got {r.status_code}")
check("老师落地页 = /admin/users", r.headers.get("location") == "/admin/users",
      f"got {r.headers.get('location')}")
row = user_row(TEACHER_NO)
check("老师账号自动建成 teacher/已审批",
      row is not None and row.role == "teacher" and row.status == "approved",
      f"role={getattr(row, 'role', None)} status={getattr(row, 'status', None)}")
check("老师显示名 = 演示老师", row is not None and row.name == "演示老师",
      f"name={getattr(row, 'name', None)!r}")

r = login(fresh_client(), TEACHER_NO, "wrong-password")
check("老师错密码 401", r.status_code == 401, f"got {r.status_code}")

# 防冒充：STU_NO 在老师凭据里，但先建成学生账号 → 本地密码无效、角色不变
login(u_client, STU_NO, "stu-pw")
stu_before = user_row(STU_NO)
r = login(fresh_client(), STU_NO, "teacher-pw-should-not-work")
check("本地密码不能登录非老师账号 401", r.status_code == 401, f"got {r.status_code}")
check("被冒充账号角色未被改动",
      user_row(STU_NO).role == stu_before.role, "role changed!")

# ================= T3 权限 =================
print("== T3 超管同权 ==")
r = t_client.get("/admin/users", follow_redirects=False)
check("老师可访问用户管理 200", r.status_code == 200, f"got {r.status_code}")
check("用户管理页有老师自己", TEACHER_NO in r.text, "teacher no not in page")
check("导航含用户管理入口", ">用户管理</a>" in r.text, "nav link missing")
check("导航含管理员台/全员总览/一键排班",
      all(x in r.text for x in (">管理员台</a>", ">全员总览</a>", ">一键排班</a>")),
      "nav links missing")
r = t_client.get("/admin/schedule", follow_redirects=False)
check("老师可访问一键排班 200", r.status_code == 200, f"got {r.status_code}")
r = t_client.get("/admin/overview", follow_redirects=False)
check("老师可访问全员总览 200", r.status_code == 200, f"got {r.status_code}")
r = t_client.get("/me", follow_redirects=False)
check("老师的我的课表 200", r.status_code == 200, f"got {r.status_code}")

# ================= T4 代管 =================
print("== T4 代管超管部门 ==")
super_row = user_row(SUPER_NO)
teacher_id = uid(TEACHER_NO)

r = t_client.post("/admin/schedule/settings", data={
    "week": "7", "max_per_person": "2", "special_person_id": "",
    "blacklist": "", "csrf": csrf_of(t_client, "/admin/schedule"),
}, follow_redirects=False)
check("老师保存排班设置 303 且不踢回登录",
      r.status_code == 303 and r.headers.get("location", "").startswith("/admin/schedule"),
      f"got {r.status_code} -> {r.headers.get('location')}")
engine = create_engine(f"sqlite:///{settings.db_path}")
with engine.connect() as conn:
    srow = conn.execute(
        ScheduleSettings.__table__.select()
        .where(ScheduleSettings.__table__.c.admin_id == super_row.id)
    ).first()
    trow = conn.execute(
        ScheduleSettings.__table__.select()
        .where(ScheduleSettings.__table__.c.admin_id == teacher_id)
    ).first()
engine.dispose()
check("设置落在超管 admin_id 下", srow is not None and srow.max_per_person == 2,
      "no settings row for super")
check("没有为老师另立 settings", trow is None, "teacher has own settings row!")

r = t_client.post("/admin/schedule/run", data={
    "week": "7", "max_per_person": "2", "special_person_id": "",
    "blacklist": "", "csrf": csrf_of(t_client, "/admin/schedule"),
}, follow_redirects=False)
check("老师一键排班 303 且不踢回登录",
      r.status_code == 303 and r.headers.get("location", "").startswith("/admin/schedule"),
      f"got {r.status_code} -> {r.headers.get('location')}")
rows = assignments_of(super_row.id)
check("排班结果落在超管 admin_id 下", len(rows) > 0, "no assignments for super")
aids = {row.user_id for row in rows}
check("排班名单不含老师", teacher_id not in aids, "teacher was scheduled!")
check("超管本人作为成员参与排班", uid(SUPER_NO) in aids, "super not scheduled")
check("老师名下没有任何排班", len(assignments_of(teacher_id)) == 0,
      f"{len(assignments_of(teacher_id))} rows")

# ================= T5 管理面 =================
print("== T5 用户管理 ==")
r = s_client.post("/admin/users/role", data={
    "user_id": str(teacher_id), "role": "user",
    "csrf": csrf_of(s_client, "/admin/users"),
}, follow_redirects=False)
check("超管可把老师降为普通用户 303", r.status_code == 303, f"got {r.status_code}")
check("降级后角色 = user", user_row(TEACHER_NO).role == "user",
      f"role={user_row(TEACHER_NO).role}")
r = s_client.post("/admin/users/role", data={
    "user_id": str(teacher_id), "role": "teacher",
    "csrf": csrf_of(s_client, "/admin/users"),
}, follow_redirects=False)
check("超管可设老师角色 303", r.status_code == 303, f"got {r.status_code}")
check("角色回 = teacher", user_row(TEACHER_NO).role == "teacher",
      f"role={user_row(TEACHER_NO).role}")
r = s_client.post("/admin/users/disable", data={
    "user_id": str(teacher_id), "disabled": "1",
    "csrf": csrf_of(s_client, "/admin/users"),
}, follow_redirects=False)
check("老师不可被页面停用 400", r.status_code == 400, f"got {r.status_code}")
check("老师账号未被停用", user_row(TEACHER_NO).disabled_at is None, "disabled!")
r = s_client.post("/admin/users/role", data={
    "user_id": str(super_row.id), "role": "user",
    "csrf": csrf_of(s_client, "/admin/users"),
}, follow_redirects=False)
check("超管账号角色不可被改 400", r.status_code == 400, f"got {r.status_code}")
r = s_client.post("/admin/users/disable", data={
    "user_id": str(super_row.id), "disabled": "1",
    "csrf": csrf_of(s_client, "/admin/users"),
}, follow_redirects=False)
check("超管账号不可被停用 400", r.status_code == 400, f"got {r.status_code}")

# ================= 注册不可见 =================
print("== 注册不可见 ==")
login(stu2_client, STU2_NO, "stu2-pw")    # STU2 成为 pending 学生
r = stu2_client.get("/pending")
check("pending 页不出现老师", "演示老师" not in r.text and TEACHER_NO not in r.text,
      "teacher visible on pending page!")
check("pending 页出现超管（现状保持）", SUPER_NO in r.text or "演示管理员" in r.text,
      "super missing from picker?!")
r = stu2_client.post("/pending/choose", data={
    "admin_id": str(teacher_id), "csrf": csrf_of(stu2_client, "/pending"),
}, follow_redirects=False)
check("直接提交选老师被拒 400", r.status_code == 400, f"got {r.status_code}")

# ================= 学生回归 =================
print("== 学生流程回归 ==")
r = stu2_client.post("/pending/choose", data={
    "admin_id": str(super_row.id), "csrf": csrf_of(stu2_client, "/pending"),
}, follow_redirects=False)
check("学生选超管部门 303", r.status_code == 303, f"got {r.status_code}")
r = s_client.post("/admin/decide", data={
    "user_id": str(uid(STU2_NO)), "decision": "approve",
    "csrf": csrf_of(s_client, "/admin"),
}, follow_redirects=False)
check("审批成员 303（老师会话也能做，同为代管权限）",
      r.status_code == 303 and r.headers.get("location", "").startswith("/admin?"),
      f"got {r.status_code} -> {r.headers.get('location')}")
check("成员归属在超管名下",
      user_row(STU2_NO).owner_admin_id == super_row.id,
      f"owner={user_row(STU2_NO).owner_admin_id}")
r = stu2_client.get("/me", follow_redirects=False)
check("学生我的课表 200", r.status_code == 200, f"got {r.status_code}")

print()
if failed:
    print(f"FAILED {len(failed)}/{ok + len(failed)}: {'; '.join(failed)}")
    raise SystemExit(1)
print(f"ALL PASSED ({ok} checks)")
