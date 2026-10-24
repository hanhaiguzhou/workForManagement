"""线上模式端到端：真实教务登录 → 抓课表 → 解析课表 → 管理员页。"""

from __future__ import annotations

import http.cookiejar
import re
import urllib.parse
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:8000"

accounts = {}
for line in Path("users.txt").read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if not line or line.startswith("#"):
        continue
    parts = re.split(r"[\s,]+", line)
    if len(parts) >= 2:
        accounts[parts[0]] = parts[1]


def opener() -> urllib.request.OpenerDirector:
    jar = http.cookiejar.CookieJar()
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))


def login(student_no: str):
    op = opener()
    response = op.open(
        urllib.request.Request(
            BASE + "/login",
            data=urllib.parse.urlencode(
                {"student_no": student_no, "password": accounts[student_no]}
            ).encode(),
        ),
        timeout=60,
    )
    return op, urllib.parse.unquote(response.geturl())


def csrf_of(op, path="/me") -> str:
    body = op.open(BASE + path, timeout=60).read().decode("utf-8", "ignore")
    m = re.search(r'name="csrf" value="([^"]+)"', body)
    return m.group(1) if m else ""


student = "2025000001"
op, landing = login(student)
print("登录后落点:", landing)

page = op.open(BASE + "/me?week=7", timeout=60).read().decode("utf-8", "ignore")
week = re.search(r"第\s*(\d+)\s*周", page)
print("周次:", week.group(1) if week else "?")
print("有解析课表按钮:", "解析课表" in page)
print("无密码框:", 'type="password"' not in page)
print("姓名:", (re.search(r"<strong>([^<]+)</strong>", page).group(1).strip()
                if re.search(r"<strong>([^<]+)</strong>", page) else "?"))

courses = re.findall(r'<div class="course">([^<]+)</div>', page)
print(f"第7周课表格子里的课（{len(courses)}）:", courses)

hint = re.search(r"本周共[^<]*节", page)
print("统计:", hint.group(0) if hint else "(无)")
print("有演示说明:", "演示模式" in page)

token = csrf_of(op)
response = op.open(
    urllib.request.Request(
        BASE + "/me/refresh",
        data=urllib.parse.urlencode({"csrf": token}).encode(),
    ),
    timeout=60,
)
print("解析课表:", urllib.parse.unquote(response.geturl().split("msg=")[-1]))

admin_op, admin_landing = login("2025000001")
print("\n超管落点:", admin_landing)
for path in ("/admin", "/admin/overview", "/admin/schedule", "/admin/users"):
    print(f"  {path}: {admin_op.open(BASE + path, timeout=30).status}")
