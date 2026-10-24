"""在线改模板文案的端到端验证：只换文字，不重传 Excel。

跑法：.venv\\Scripts\\python.exe scripts\\verify_template_texts.py
"""

from __future__ import annotations

import io
import re
import sys
import urllib.parse
from pathlib import Path

import openpyxl
import requests

BASE = "http://127.0.0.1:8000"
STUDENT_NO = "2025000001"


def password_for(student_no: str) -> str:
    for line in Path("users.txt").read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] == student_no:
            return parts[1]
    raise SystemExit(f"users.txt 里没有 {student_no} 的密码")


def csrf_of(html: str) -> str:
    match = re.search(r'name="csrf" value="([^"]+)"', html)
    if not match:
        raise SystemExit("页面上找不到 csrf token")
    return match.group(1)


def main() -> int:
    session = requests.Session()
    login = session.post(
        f"{BASE}/login",
        data={"student_no": STUDENT_NO, "password": password_for(STUDENT_NO)},
        allow_redirects=True,
        timeout=30,
    )
    if login.status_code != 200:
        print("登录失败:", login.status_code)
        return 1
    print("登录成功")

    page = session.get(f"{BASE}/admin/schedule?week=7", timeout=20)
    html = page.text
    print("排班页:", page.status_code)

    checks = {
        "有周历条": 'class="week-note"' in html,
        "显示日期范围": "calendar" not in html and re.search(r"\d{4}-\d{2}-\d{2} ~ \d{2}-\d{2}", html) is not None,
        "有模板卡片": "值班表模板" in html,
        "有文案表单": 'action="/admin/schedule/template/texts"' in html,
        "无学期起始日期输入": 'name="semester_start"' not in html,
        "有上传入口": 'action="/admin/schedule/template/upload"' in html,
    }
    for name, ok in checks.items():
        print(f"  {'OK  ' if ok else 'FAIL'} {name}")

    token = csrf_of(html)

    # 改文案
    texts = {
        "csrf": token,
        "title": "部门勤工助学值班表（2026-2027 学年）",
        "place": "图书馆 B1、B2-3楼",
        "teacher_name": "演示老师",
        "teacher_phone": "138-0000-0000",
        "note": "备注：\n    1. 值班期间不得擅自离岗。\n    2. 请假请提前一天报备。",
    }
    saved = session.post(f"{BASE}/admin/schedule/template/texts", data=texts,
                         allow_redirects=True, timeout=20)
    print("\n保存文案:", saved.status_code, "|", "已保存" in saved.text)

    # 导出并检查
    xlsx = session.get(f"{BASE}/admin/schedule/download?week=7", timeout=30)
    print("下载:", xlsx.status_code, len(xlsx.content), "字节")
    if xlsx.status_code != 200:
        print(xlsx.text[:400])
        return 1

    workbook = openpyxl.load_workbook(io.BytesIO(xlsx.content))
    sheet = workbook.active
    got = {
        "标题 A1": str(sheet["A1"].value or ""),
        "时间地点 A3": str(sheet["A3"].value or ""),
        "老师 E20": str(sheet["E20"].value or ""),
        "电话 N20": str(sheet["N20"].value or ""),
        "备注 A30": str(sheet["A30"].value or "")[:40],
    }
    workbook.close()
    for name, value in got.items():
        print(f"  {name}: {value!r}")

    expect = {
        "标题 A1": "2026-2027 学年",
        "时间地点 A3": "图书馆 B1、B2-3楼",
        "老师 E20": "演示老师",
        "电话 N20": "138-0000-0000",
        "备注 A30": "值班期间不得擅自离岗",
    }
    failures = [k for k, v in expect.items() if v not in got[k]]
    if failures:
        print("\n没写进去的字段:", failures)
        return 1
    print("\n全部文案已生效")

    # 恢复默认，避免留下测试痕迹
    reset = session.post(f"{BASE}/admin/schedule/template/reset",
                         data={"csrf": token}, allow_redirects=True, timeout=20)
    print("恢复默认:", reset.status_code, "|", "已恢复" in reset.text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
