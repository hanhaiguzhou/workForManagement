"""开发辅助：用真实周次 TXT + 现有模板跑一遍新排班器，打印对照结果。

用法：
    .venv\\Scripts\\python.exe scripts\\preview_scheduler.py <txt路径> [--seed N]

这不是产品代码，只是把「旧脚本的空闲数据」喂给 `app.scheduler` 看结果，
用于在 Web 端接好之前先证明算法零冲突。
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import Counter

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from app.scheduler import DAYS, SLOTS, Infeasible, Plan, Request, assign  # noqa: E402

TEMPLATE_PATH = os.path.join(BASE_DIR, "勤工助学值班表 20XX-20XX -X第X周.xlsx")
A2_ROWS = [5, 7, 11, 13, 15, 17]
TIME_NAMES = {5: "1-2", 7: "3-4", 11: "5-6", 13: "7-8", 15: "9-10", 17: "11-12"}
DAY_TO_A2_COL = {"周一": "D", "周二": "F", "周三": "H", "周四": "J", "周五": "L", "周六": "N", "周日": "P"}

SPECIAL_PERSON = "演示管理员"
MAX_PER_PERSON = 2


def fillable_from_template(path: str) -> set[tuple[str, str]]:
    import openpyxl

    wb = openpyxl.load_workbook(path)
    ws = wb.active
    out: set[tuple[str, str]] = set()
    for day, col in DAY_TO_A2_COL.items():
        for row in A2_ROWS:
            if ws[f"{col}{row}"].value == "A2":
                out.add((day, TIME_NAMES[row]))
    wb.close()
    return out


def free_from_txt(path: str) -> dict[str, dict[str, list[str]]]:
    import re

    students: dict[str, dict[str, list[str]]] = {}
    current = None
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            name = re.match(r"=== (.+?)\(\d+\) ===", line)
            if name:
                current = name.group(1)
                students[current] = {}
                continue
            free = re.match(r"(周[一二三四五六日]): 空闲 (.+)", line)
            if current and free:
                students[current][free.group(1)] = re.findall(r"第(\d+-\d+)节", free.group(2))
    return students


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("txt")
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()

    fillable = fillable_from_template(TEMPLATE_PATH)
    free = free_from_txt(args.txt)
    people = list(free)
    print(f"学生 {len(people)} 人：{', '.join(people)}")
    print(f"模板可填格 {len(fillable)} 个（含周六 {sum(1 for d,_ in fillable if d=='周六')}、"
          f"周日 {sum(1 for d,_ in fillable if d=='周日')}）")

    # busy = 全部可能时段 - 该生本周空闲
    universe = {(d, s) for d in DAYS for s in SLOTS}
    busy = {}
    for person in people:
        free_slots = {(d, s) for d, slots in free[person].items() for s in slots}
        busy[person] = frozenset(universe - free_slots)

    demand = {p: (MAX_PER_PERSON if p != SPECIAL_PERSON else 0) for p in people}
    demand[SPECIAL_PERSON] = 0
    request = Request(
        fillable=frozenset(fillable),
        busy=busy,
        demand=demand,
        special=SPECIAL_PERSON if SPECIAL_PERSON in people else None,
        blacklist_1_2=frozenset(),
        seed=args.seed,
    )

    result = assign(request)
    if isinstance(result, Infeasible):
        print("\n[不可行]")
        print(" ", result.hint)
        for reason in result.reasons:
            print("  -", reason)
        return 1

    assert isinstance(result, Plan)
    counts = Counter(result.assignments.values())
    print("\n排班结果：")
    for day in DAYS:
        cells = [(s, who) for (d, s), who in result.assignments.items() if d == day]
        if not cells:
            continue
        cells.sort(key=lambda x: SLOTS.index(x[0]))
        print(f"  {day}: " + "  ".join(f"{s}→{who}" for s, who in cells))

    print("\n每人次数：")
    for person in people:
        mark = " [特殊]" if person == SPECIAL_PERSON else ""
        print(f"  {person}: {counts.get(person, 0)} 次{mark}")

    # 硬校验：绝不排到有课/请假时段
    conflicts = [
        (slot, who) for slot, who in result.assignments.items()
        if slot in busy.get(who, frozenset())
    ]
    sunday = [s for s in result.assignments if s[0] == "周日"]
    sat_non_special = [
        (s, w) for s, w in result.assignments.items()
        if s[0] == "周六" and w != SPECIAL_PERSON
    ]
    print(f"\n[校验] 排到有课时段：{len(conflicts)} 处")
    print(f"[校验] 周日被排：{len(sunday)} 处")
    print(f"[校验] 周六非特殊人员：{len(sat_non_special)} 处")
    return 0 if not (conflicts or sunday or sat_non_special) else 2


if __name__ == "__main__":
    raise SystemExit(main())
