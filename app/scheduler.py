"""排班器：纯函数、无 I/O、零随机失败。

输入「可填格子 + 每人的占用时段（有课 ∪ 请假）+ 每人所需次数 + 规则」，
输出满足全部硬约束的排班；无法满足时返回 :class:`Infeasible` 说明原因，
绝不返回"看起来能用但其实错了"的部分结果。

算法：把「人 → 格子」建模成带容量的二部图，
用最大流求「每人恰好 demand 次、每格至多一人」的可行解。
格子的遍历顺序决定了偏好：先上午后下午、先不占特殊人员的格子。
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Dict, FrozenSet, Iterable, List, Optional, Tuple

DAYS: Tuple[str, ...] = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")
SLOTS: Tuple[str, ...] = ("1-2", "3-4", "5-6", "7-8", "9-10", "11-12")

SUNDAY = "周日"
SATURDAY = "周六"
#: 与周六同性质的独占节次：每天的 11-12 节只排常驻人员
RESERVED_SLOT = "11-12"
MORNING_SLOTS: FrozenSet[str] = frozenset({"1-2", "3-4"})

Slot = Tuple[str, str]  # (星期, 节次)


@dataclass(frozen=True)
class Request:
    """一次排班请求。全部为纯数据，便于测试与回放。"""

    fillable: FrozenSet[Slot]
    busy: Dict[str, FrozenSet[Slot]]
    demand: Dict[str, int]
    special: Optional[str] = None
    blacklist_1_2: FrozenSet[str] = frozenset()
    seed: Optional[int] = 0


@dataclass
class Plan:
    assignments: Dict[Slot, str]
    request: Optional[Request] = field(default=None, repr=False)

    ok: bool = field(default=True, init=False)

    def counts(self) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for who in self.assignments.values():
            out[who] = out.get(who, 0) + 1
        return out


@dataclass
class Infeasible:
    shortfalls: Dict[str, int]
    hint: str
    reasons: List[str] = field(default_factory=list)

    ok: bool = field(default=False, init=False)

    @property
    def assignments(self) -> Dict[Slot, str]:
        """不可行时永远没有排班结果，避免调用方误用。"""
        return {}


# ------------------------------------------------------------------ 图与最大流


class _Flow:
    """最小可用的 Dinic 实现（规模很小，够用且无第三方依赖）。"""

    def __init__(self, n: int) -> None:
        self._g: List[List[List[int]]] = [[] for _ in range(n)]

    def add(self, u: int, v: int, cap: int) -> None:
        self._g[u].append([v, len(self._g[v]), cap])
        self._g[v].append([u, len(self._g[u]) - 1, 0])

    def _bfs(self, s: int, t: int) -> bool:
        self._level = [-1] * len(self._g)
        self._level[s] = 0
        queue = [s]
        while queue:
            nxt = []
            for u in queue:
                for v, _rev, cap in self._g[u]:
                    if cap > 0 and self._level[v] < 0:
                        self._level[v] = self._level[u] + 1
                        nxt.append(v)
            queue = nxt
        return self._level[t] >= 0

    def _dfs(self, u: int, t: int, limit: int) -> int:
        if u == t:
            return limit
        while self._it[u] < len(self._g[u]):
            edge = self._g[u][self._it[u]]
            v, rev, cap = edge
            if cap > 0 and self._level[v] == self._level[u] + 1:
                pushed = self._dfs(v, t, min(limit, cap))
                if pushed:
                    edge[2] -= pushed
                    self._g[v][rev][2] += pushed
                    return pushed
            self._it[u] += 1
        return 0

    def maxflow(self, s: int, t: int) -> int:
        total = 0
        while self._bfs(s, t):
            self._it = [0] * len(self._g)
            while True:
                pushed = self._dfs(s, t, 10 ** 9)
                if not pushed:
                    break
                total += pushed
        return total

    def flow_between(self, u: int, v: int) -> int:
        for to, rev, cap in self._g[u]:
            if to == v:
                return self._g[v][rev][2]
        return 0


# ------------------------------------------------------------------ 辅助


def _morning_first(slots: Iterable[Slot]) -> List[Slot]:
    """上午在前，组内保持调用方给定的顺序（已打乱）。"""
    slots = list(slots)
    return [s for s in slots if s[1] in MORNING_SLOTS] + [
        s for s in slots if s[1] not in MORNING_SLOTS
    ]


def _eligible_slots(request: Request) -> List[Slot]:
    """可填格子去掉周日（周日不排班是硬规则，不依赖日历库）。"""
    slots = [s for s in request.fillable if s[0] != SUNDAY]
    return sorted(set(slots), key=lambda s: (DAYS.index(s[0]), SLOTS.index(s[1])))


def _person_can_take(person: str, slot: Slot, request: Request) -> bool:
    if slot in request.busy.get(person, frozenset()):
        return False
    if slot[1] == "1-2" and person in request.blacklist_1_2:
        return False
    # 独占格：周六整列 + 每天的 11-12 节，只给常驻人员。
    # 周六的 11-12 同时命中两条，但格子以 (星期, 节次) 为键，只会被分配一次。
    if (slot[0] == SATURDAY or slot[1] == RESERVED_SLOT) and person != request.special:
        return False
    return True


def _special_free(request: Request, eligible: List[Slot]) -> FrozenSet[Slot]:
    if not request.special:
        return frozenset()
    return frozenset(s for s in eligible if _person_can_take(request.special, s, request))


# ------------------------------------------------------------------ 主入口


def assign(request: Request) -> Plan | Infeasible:
    """求一个满足全部硬约束的排班，或说明为什么做不到。"""
    eligible = _eligible_slots(request)
    if not eligible:
        return Infeasible(
            shortfalls={p: k for p, k in request.demand.items() if k > 0},
            hint="没有任何可填格子：模板里没有可排的位置，或本周全部是节假日/周日。",
            reasons=["fillable 为空"],
        )

    rng = random.Random(request.seed)
    regulars = [p for p in request.demand if p != request.special and request.demand[p] > 0]
    special_free = _special_free(request, eligible)

    # 每个常规人员的候选格：先"特殊人员排不了"的，再"会和特殊人员重合"的；各自上午优先。
    candidates: Dict[str, List[Slot]] = {}
    for person in regulars:
        usable = [s for s in eligible if _person_can_take(person, s, request)]
        preferred = [s for s in usable if s not in special_free]
        shared = [s for s in usable if s in special_free]
        rng.shuffle(preferred)
        rng.shuffle(shared)
        candidates[person] = _morning_first(preferred) + _morning_first(shared)

    # ---- 最大流：source -> 人(容量=需求) -> 格(容量1) -> sink
    n_users = len(regulars)
    user_index = {p: 1 + i for i, p in enumerate(regulars)}
    slot_index = {s: 1 + n_users + i for i, s in enumerate(eligible)}
    slot_node = {v: k for k, v in slot_index.items()}
    sink = 1 + n_users + len(eligible)
    flow = _Flow(sink + 1)

    for person in regulars:
        flow.add(0, user_index[person], request.demand[person])
        for slot in candidates[person]:
            flow.add(user_index[person], slot_index[slot], 1)
    for slot in eligible:
        flow.add(slot_index[slot], sink, 1)

    flow.maxflow(0, sink)

    assignments: Dict[Slot, str] = {}
    for person in regulars:
        for slot in candidates[person]:
            if flow.flow_between(user_index[person], slot_index[slot]) > 0:
                assignments[slot] = person

    shortfalls: Dict[str, int] = {}
    reasons: List[str] = []
    for person in regulars:
        got = sum(1 for who in assignments.values() if who == person)
        if got < request.demand[person]:
            shortfalls[person] = request.demand[person] - got
            usable = len([s for s in eligible if _person_can_take(person, s, request)])
            if usable == 0:
                reasons.append(f"{person}：本周没有任何可排时段（全部有课/请假或受规则限制）")
            else:
                reasons.append(
                    f"{person}：需要 {request.demand[person]} 次，只排到 {got} 次"
                    f"（本人可用 {usable} 个时段，但与其他人的需求争抢同一批格子）"
                )

    if shortfalls:
        total_free = len(eligible)
        total_need = sum(request.demand.values())
        hint = (
            f"可填格子 {total_free} 个，总需求 {total_need} 次，"
            f"缺 {sum(shortfalls.values())} 次。"
            "可以加人、减少每人次数、放宽黑名单/周六/11-12 节限制，或调整请假。"
        )
        return Infeasible(shortfalls=shortfalls, hint=hint, reasons=reasons)

    # ---- 特殊人员填满剩下的所有可排时段
    special = request.special
    if special:
        for slot in _morning_first([s for s in eligible if s not in assignments]):
            if _person_can_take(special, slot, request):
                assignments[slot] = special

    plan = Plan(assignments=assignments, request=request)
    problems = validate_plan(plan, request)
    if problems:  # 自证：算法自身的输出必须过校验，否则是 bug
        raise AssertionError("排班器产出了不合法结果: " + "; ".join(problems))
    return plan


def validate_plan(plan: Plan, request: Request) -> List[str]:
    """独立校验：不信任算法，逐条核对硬约束。返回问题清单（空表示合法）。"""
    problems: List[str] = []
    fillable = set(request.fillable)
    seen: Dict[Slot, str] = {}

    for slot, person in plan.assignments.items():
        day, period = slot
        if slot in seen:
            problems.append(f"{day} {period} 节被重复分配")
        seen[slot] = person

        if slot not in fillable:
            problems.append(f"{day} {period} 节不在可填格子里")
        if day == SUNDAY:
            problems.append(f"{day} {period} 节是周日，不应排班")
        if slot in request.busy.get(person, frozenset()):
            problems.append(f"{person} 在 {day} {period} 节有课/请假，却被排班")
        if period == "1-2" and person in request.blacklist_1_2:
            problems.append(f"{person} 在黑名单里，不应排 1-2 节")
        if (slot[0] == SATURDAY or slot[1] == RESERVED_SLOT) and request.special and person != request.special:
            problems.append(
                f"{day} {period} 节是常驻独占格（周六/每天 11-12 节），应由 {request.special} 排，却排了 {person}"
            )

    counts: Dict[str, int] = {}
    for person in plan.assignments.values():
        counts[person] = counts.get(person, 0) + 1
    for person, need in request.demand.items():
        got = counts.get(person, 0)
        if person == request.special:
            if got and need and got < need:
                problems.append(f"特殊人员 {person} 只排到 {got} 次")
        elif got != need:
            problems.append(f"{person} 排了 {got} 次，应为 {need} 次")

    return problems
