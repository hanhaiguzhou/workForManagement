"""探针：真连一次教务，确认线上模式能登录 + 抓课表 + 爬姓名。

用法：python scripts/probe_real.py [学号]
不打印密码，只打印结果与耗时。
"""

from __future__ import annotations

import re
import socket
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.jiaowu import (  # noqa: E402
    SERVER_URLS,
    JiaowuClient,
    JiaowuError,
    parse_student_name,
    parse_timetable_html,
    select_server,
)


def load_accounts() -> dict[str, str]:
    accounts: dict[str, str] = {}
    path = Path("users.txt")
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


def probe_tcp(host: str, port: int, timeout: float = 5.0) -> str:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return "通"
    except OSError as exc:
        return f"不通（{type(exc).__name__}: {exc}）"


def main() -> int:
    accounts = load_accounts()
    student_no = sys.argv[1] if len(sys.argv) > 1 else (next(iter(accounts), ""))
    if not student_no:
        print("users.txt 里没有账号")
        return 2
    password = accounts.get(student_no)
    if not password:
        print(f"users.txt 里没有 {student_no} 的密码")
        return 2

    print("== 服务器连通性 ==")
    for url in SERVER_URLS:
        host = url.split("//", 1)[1].split(":", 1)[0]
        print(f"  {host}: {probe_tcp(host, 81)}")

    server = select_server(student_no)
    print(f"\n== 选中服务器: {server} ==")
    print(f"学号: {student_no}")

    client = JiaowuClient(student_no)
    started = time.time()
    try:
        name = client.login(password)
        print(f"登录: 成功（{time.time() - started:.1f}s）姓名={name or '(页面没给姓名)'}")
    except JiaowuError as exc:
        print(f"登录: 失败 —— {exc}（{time.time() - started:.1f}s）")
        return 1

    started = time.time()
    try:
        html = client.fetch_timetable()
    except JiaowuError as exc:
        print(f"抓课表: 失败 —— {exc}（{time.time() - started:.1f}s）")
        return 1
    print(f"抓课表: 成功（{time.time() - started:.1f}s，{len(html)} 字符）")

    if name != parse_student_name(html):
        print(f"课表页姓名: {parse_student_name(html) or '(无)'}")

    result = parse_timetable_html(html)
    print(f"解析: 课程格 {result.cells_seen} 个，解析出 {result.cells_parsed} 个，"
          f"课程 {len(result.courses)} 门")
    for warning in result.warnings[:5]:
        print(f"  ⚠ {warning}")

    for course in result.courses[:15]:
        print(f"  {course.day} {course.slot} | {course.name} | {course.weeks} | {course.room or '(无)'}")
    if len(result.courses) > 15:
        print(f"  …共 {len(result.courses)} 门")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
