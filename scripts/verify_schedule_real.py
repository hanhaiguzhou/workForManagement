"""线上模式：用真实课表跑一次一键排班并下载 Excel。"""

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

admin_no = "2025000001"
jar = http.cookiejar.CookieJar()
op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
op.open(urllib.request.Request(
    BASE + "/login",
    data=urllib.parse.urlencode(
        {"student_no": admin_no, "password": accounts[admin_no]}
    ).encode(),
), timeout=60)

page = op.open(BASE + "/admin/schedule", timeout=60).read().decode("utf-8", "ignore")
token = re.search(r'name="csrf" value="([^"]+)"', page).group(1)

special = re.search(r'name="special_person_id"[^>]*>(.*?)</select>', page, re.S)
options = re.findall(r'<option value="(\d+)"', special.group(1)) if special else []
print("可选固定人员 id:", options)

data = {
    "week": "7",
    "max_per_person": "2",
    "special_person_id": options[0] if options else "",
    "blacklist": "",
    "semester_start": "2026-09-07",
    "csrf": token,
}
response = op.open(urllib.request.Request(
    BASE + "/admin/schedule/run", data=urllib.parse.urlencode(data).encode()
), timeout=120)
final = urllib.parse.unquote(response.geturl())
print("排班结果:", final.split("msg=")[-1] if "msg=" in final else final)

body = op.open(BASE + "/admin/schedule?week=7", timeout=60).read().decode("utf-8", "ignore")
names = re.findall(r'<td class="[^"]*">([\u4e00-\u9fa5]{2,4})</td>', body)
print("计划里出现的姓名（去重）:", sorted(set(names)))
print("页面含'排班'提示:", ("排班完成" in body) or ("共" in body and "格" in body))

xlsx = op.open(BASE + "/admin/schedule/download?week=7", timeout=120)
content = xlsx.read()
print("下载 Excel:", len(content), "字节 | 是 zip/xlsx:", content[:2] == b"PK")
Path("data/verify_real_week7.xlsx").write_bytes(content)
print("已存 data/verify_real_week7.xlsx")
