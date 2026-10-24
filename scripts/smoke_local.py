"""本地冒烟：对着已经跑起来的 dev server 走一遍真实 HTTP 流程。

用法：.venv\\Scripts\\python.exe scripts\\smoke_local.py [base_url]
"""

from __future__ import annotations

import sys

import httpx2 as httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"

checks: list[tuple[str, bool, str]] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    checks.append((label, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {label}" + (f"  [{detail}]" if detail else ""))


def main() -> int:
    with httpx.Client(base_url=BASE, follow_redirects=False, timeout=10) as client:
        # 1) 引导超管
        r = client.post("/register", data={"student_no": "2025000001", "password": "demo-pass-1", "admin_key": ""})
        check("注册超管返回 303 到 /pending", r.status_code == 303 and r.headers.get("location") == "/pending",
              f"{r.status_code} {r.headers.get('location')}")
        set_cookie = r.headers.get("set-cookie", "")
        check("cookie 带 30 天有效期", "Max-Age=2592000" in set_cookie)
        check("cookie 为 HttpOnly", "HttpOnly" in set_cookie)

        r = client.get("/pending")
        check("已审批的超管被送去自己的首页", r.status_code == 303 and r.headers.get("location") == "/admin/users",
              f"{r.status_code} {r.headers.get('location')}")

        r = client.get("/admin/users")
        check("超管能打开用户管理页", r.status_code == 200 and "用户管理" in r.text, str(r.status_code))

        # 2) 错误的管理员标识
        r = client.post("/register", data={"student_no": "2025000002", "password": "demo-pass-2", "admin_key": "99999999"})
        check("管理员标识不存在时拒绝注册", r.status_code == 400 and "管理员标识" in r.text, str(r.status_code))

        # 3) 用超管学号作为标识 -> 申请成为管理员（pending）
        r = client.post("/register", data={"student_no": "2025000002", "password": "demo-pass-2", "admin_key": "2025000001"})
        check("申请管理员进入 /pending", r.status_code == 303 and r.headers.get("location") == "/pending",
              f"{r.status_code} {r.headers.get('location')}")

        # 4) 密码错误
        with httpx.Client(base_url=BASE, follow_redirects=False, timeout=10) as anon:
            r = anon.post("/login", data={"student_no": "2025000001", "password": "wrong"})
            check("错误密码返回 401", r.status_code == 401, str(r.status_code))

            r = anon.get("/me")
            check("未登录访问 /me 跳登录", r.status_code == 303 and r.headers.get("location") == "/login",
                  f"{r.status_code} {r.headers.get('location')}")

        # 5) 越权：管理员候选（pending）不能进管理员台
        r = client.get("/admin")
        check("未审批用户进不了管理员台", r.status_code in (303, 403), str(r.status_code))

    failed = [c for c in checks if not c[1]]
    print(f"\n{len(checks) - len(failed)}/{len(checks)} 通过")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
