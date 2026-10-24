"""强智科技教务系统适配器：登录、抓课表、解析。

与旧脚本的关键差异：

1. **显式映射**：星期由表头单元格决定，节次由行文本判定，不再"第几个格子算周几"。
2. **6 段节次**：1-2 / 3-4 / 5-6 / 7-8 / 9-10 / 11-12，`11-12` 不再被丢掉。
3. **自检**：解析不出的课程格进入 ``warnings``，并由 ``cells_seen`` / ``cells_parsed``
   让调用方断言"没有静默漏课"——这是旧版"排到非空闲时间"的根因。

密码只在本模块的调用栈里出现，不被记录。
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Optional, Protocol, Tuple

import requests
from bs4 import BeautifulSoup

PORTAL_URL = "http://jw.example.edu.cn/jsxsd"
SERVER_URLS = [
    "http://jw1.example.com:81/jsxsd",
    "http://jw2.example.com:81/jsxsd",
]
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

MAX_LOGIN_RETRY = 3
LOGIN_RETRY_DELAY = 1.0

SAMPLE_HTML_PATH = Path(__file__).resolve().parent / "samples" / "kbtable_sample.html"

DAYS: Tuple[str, ...] = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")
SLOTS: Tuple[str, ...] = ("1-2", "3-4", "5-6", "7-8", "9-10", "11-12")

_DAY_CHAR = {"一": "周一", "二": "周二", "三": "周三", "四": "周四",
             "五": "周五", "六": "周六", "日": "周日", "天": "周日"}


class JiaowuError(RuntimeError):
    """登录或抓取失败。消息可直接展示给学生，不含凭据。"""


# --------------------------------------------------------------- 结果对象


@dataclass(frozen=True)
class RawCourse:
    name: str
    day: str
    slot: str
    teacher: str = ""
    room: str = ""
    weeks: str = ""


@dataclass
class ParseResult:
    courses: List[RawCourse] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    cells_seen: int = 0
    cells_parsed: int = 0

    @property
    def consistent(self) -> bool:
        """每个非空课程格都必须解析出至少一门课，否则视为漏课。"""
        return self.cells_parsed == self.cells_seen

    def occupied(self, week: int) -> set[Tuple[str, str]]:
        return occupied_slots(self.courses, week)


@dataclass(frozen=True)
class VerifyResult:
    ok: bool
    name: str = ""
    reason: str = ""

    def __bool__(self) -> bool:  # 便于 `if result:`
        return self.ok


@dataclass(frozen=True)
class LoginBundle:
    """一次登录的完整结果：身份成立与否 + 顺手抓到的课表 + 教务会话 cookie。"""

    ok: bool
    name: str = ""
    reason: str = ""
    parse: Optional[ParseResult] = None
    # 可以是 dict（老格式）或 [{"name","value","domain","path"}]（带 domain 才能回放）
    cookies: object = field(default_factory=dict)


class IdentityVerifier(Protocol):
    """身份验证缝：能不能登上强智教务系统，就是身份凭证。"""

    def verify(self, student_no: str, password: str) -> VerifyResult: ...

    def login(self, student_no: str, password: str) -> LoginBundle: ...

    def refresh(self, student_no: str, cookies: object) -> LoginBundle: ...


def cookie_jar_list(cookies) -> List[dict]:
    """把 requests 的 CookieJar 转成可 JSON 化的列表。

    **必须带 domain/path**：只存 name→value 的话，回放时 requests 不知道发给谁，
    请求里根本不会带上这些 cookie，教务就把人当未登录踢回登录页。
    """
    return [
        {
            "name": cookie.name,
            "value": cookie.value or "",
            "domain": cookie.domain or "",
            "path": cookie.path or "/",
        }
        for cookie in cookies
    ]


def cookie_map(cookies) -> dict:
    """兼容 dict 与列表两种存法，取 name→value。"""
    if isinstance(cookies, dict):
        return dict(cookies)
    return {
        item.get("name"): item.get("value", "")
        for item in (cookies or [])
        if isinstance(item, dict) and item.get("name")
    }


# --------------------------------------------------------------- 周次


def _normalize(expr: str) -> str:
    return expr.replace("（", "(").replace("）", ")").replace("周", "").strip()


def parse_week_expr(expr: str) -> Optional[set[int]]:
    """把 ``1-5,7-9,11-17(全部)`` / ``3-16(单)`` 解析成周次集合。

    返回 None 表示"没有可解析的周次"（调用方应按"全部周"处理并告警）。
    """
    if not expr or not expr.strip():
        return None
    clean = _normalize(expr)
    parity = None
    if "(单)" in clean:
        parity, clean = "odd", clean.replace("(单)", "")
    elif "(双)" in clean:
        parity, clean = "even", clean.replace("(双)", "")
    clean = re.sub(r"\([^)]*\)", "", clean).strip()
    if not clean:
        return None

    weeks: set[int] = set()
    for chunk in re.split(r"[,，、\s]+", clean):
        if not chunk:
            continue
        m = re.fullmatch(r"(\d{1,2})\s*[-~]\s*(\d{1,2})", chunk)
        if m:
            start, end = int(m.group(1)), int(m.group(2))
            weeks.update(w for w in range(min(start, end), max(start, end) + 1))
        elif chunk.isdigit():
            weeks.add(int(chunk))
    if not weeks:
        return None
    if parity == "odd":
        weeks = {w for w in weeks if w % 2 == 1}
    elif parity == "even":
        weeks = {w for w in weeks if w % 2 == 0}
    return weeks


def week_matches(week: int, expr: str) -> bool:
    weeks = parse_week_expr(expr)
    return True if weeks is None else week in weeks


def occupied_slots(courses: Iterable[RawCourse], week: int) -> set[Tuple[str, str]]:
    return {(c.day, c.slot) for c in courses if week_matches(week, c.weeks)}


# --------------------------------------------------------------- 单元格解析


def _normalize_slot(a: str | int, b: str | int) -> Optional[str]:
    try:
        label = f"{int(a)}-{int(b)}"
    except (TypeError, ValueError):
        return None
    return label if label in SLOTS else None


# 节次判定：原项目 _detect_period 的关键词表（覆盖 [01-02节] 这类补零写法），
# 再加一层更宽的正则兜底。
_PERIOD_KEYWORDS = (
    (("01-02", "1-2节", "[01-02节]", "第1-2节"), "1-2"),
    (("03-04", "3-4节", "[03-04节]", "第3-4节"), "3-4"),
    (("05-06", "5-6节", "[05-06节]", "第5-6节"), "5-6"),
    (("07-08", "7-8节", "[07-08节]", "第7-8节"), "7-8"),
    (("09-10", "9-10节", "[09-10节]", "第9-10节"), "9-10"),
    (("11-12", "11-12节", "[11-12节]", "第11-12节"), "11-12"),
)
_PAT_JIE = re.compile(r"(\d{1,2})\s*[-~至]\s*(\d{1,2})\s*节")
_PAT_BRACKET = re.compile(r"\[\s*(\d{1,2})\s*[-~]\s*(\d{1,2})\s*节?\s*\]")
_PAT_LOOSE = re.compile(r"(\d{1,2})\s*[-~]\s*(\d{1,2})")


def detect_slot(row_text: str) -> Optional[str]:
    """从行文本判定节次。先查关键词表，再用正则兜底（跳过 08:20-10:00 这类时间）。"""
    for keywords, slot in _PERIOD_KEYWORDS:
        if any(kw in row_text for kw in keywords):
            return slot
    for pattern in (_PAT_BRACKET, _PAT_JIE, _PAT_LOOSE):
        for a, b in pattern.findall(row_text):
            slot = _normalize_slot(a, b)
            if slot:
                return slot
    return None


# 以下单元格解析函数逐字移植自原项目 course_crawler.py（真实教务页面验证过的逻辑），
# 只把输出结构适配成 (课程名, 教室, 周次) 三元组；解析规则本身不要随意改写。

_PAT_DETAIL = re.compile(
    r'([\u4e00-\u9fa5A-Za-z][\u4e00-\u9fa5A-Za-z0-9]*)\[\d+(?:\|\d+)?\]\[[\u4e00-\u9fa5]+\]'
)
_BUILDING = r'[\u4e00-\u9fa5]+楼[\u4e00-\u9fa5]*(?:馆|厅|场|室)?\d*'


@dataclass
class _CellCourse:
    """与原项目 Course 对齐的最小结构（course_name / week / place）。"""

    course_name: str = ""
    week: str = ""
    place: str = ""


def _strip_building_prefix(name: str) -> str:
    name = name.strip()
    m = re.match(r'^[\u4e00-\u9fa5]+楼[\u4e00-\u9fa5]*(?:馆|厅|场|室)?\d*', name)
    if m and len(m.group(0)) < len(name):
        rest = name[m.end():]
        if rest and (rest[0].isalpha() or '\u4e00' <= rest[0] <= '\u9fa5'):
            return rest.strip()
    m2 = re.match(r'^在线课程?[\u4e00-\u9fa5]*测评?考试?平台?', name)
    if m2 and len(m2.group(0)) < len(name):
        rest = name[m2.end():]
        if rest and '\u4e00' <= rest[0] <= '\u9fa5':
            return rest.strip()
    return name


def _looks_like_course_name(name: str) -> bool:
    """课程名必须像课程名：至少两个字符且含汉字/字母。

    原项目没有这道闸门，杂格（``???``、分隔线残渣）会被当成课程名混进课表；
    宁可让它进 warnings 让人看见，也不要假装解析成功。
    """
    name = (name or "").strip()
    return len(name) >= 2 and bool(re.search(r"[\u4e00-\u9fa5A-Za-z]", name))


def parse_course_cell(text: str) -> List[_CellCourse]:
    if not text or not text.strip():
        return []
    text = text.strip()
    courses: List[_CellCourse] = []

    semicolon_courses = _parse_semicolon_format(text)
    if semicolon_courses:
        return [c for c in semicolon_courses if _looks_like_course_name(c.course_name)]

    segments = re.split(r'-{5,}', text)
    for segment in segments:
        segment = segment.strip()
        if not segment:
            continue
        detail_parts = _split_by_detail_pattern(segment)
        if detail_parts:
            for dp in detail_parts:
                course = _parse_detail_segment(dp)
                if course and _looks_like_course_name(course.course_name):
                    course.course_name = _strip_building_prefix(course.course_name)
                    courses.append(course)
        else:
            course = _parse_brief_segment(segment)
            if course and _looks_like_course_name(course.course_name):
                courses.append(course)

    seen = set()
    unique: List[_CellCourse] = []
    for c in courses:
        key = (c.course_name, c.week, c.place)
        if key not in seen:
            seen.add(key)
            unique.append(c)
    return unique


def _parse_semicolon_format(text: str) -> Optional[List[_CellCourse]]:
    if '周;' not in text and '周，' not in text:
        return None
    courses: List[_CellCourse] = []
    parts = re.split(r'[；;]', text)
    for part in parts:
        part = part.strip()
        if not part:
            continue
        m = re.match(r'([\u4e00-\u9fa5A-Za-z0-9（）()]+?)\s+([\u4e00-\u9fa5,，\s]+)[\s]*(\d+[\d,\-]*周)', part)
        if m:
            course = _CellCourse()
            course.course_name = m.group(1).strip()
            course.week = m.group(3).replace("周", "")
            courses.append(course)
        else:
            m2 = re.match(r'([\u4e00-\u9fa5A-Za-z0-9（）()]+)\s+(\d+[\d,\-]*周)', part)
            if m2:
                course = _CellCourse()
                course.course_name = re.sub(r'\s+[\u4e00-\u9fa5]{2,4}$', '', m2.group(1).strip())
                course.week = m2.group(2).replace("周", "")
                courses.append(course)
    return courses if courses else None


def _split_by_detail_pattern(text: str) -> List[str]:
    matches = list(_PAT_DETAIL.finditer(text))
    if not matches:
        return []
    parts = []
    for match in matches:
        search_back = text[:match.start()]
        building_m = list(re.finditer(_BUILDING, search_back))
        if building_m:
            # 教室通常写在课程名前面：把起点前移到教室之后，地点才不会被丢掉
            actual_start = building_m[-1].end()
        else:
            actual_start = match.start()
        parts.append(text[actual_start:])
    # 原项目只取 parts[-1]；这里返回全部片段，避免一格多课时漏掉前面的课
    # （漏一门课 = 排班时把那节课当空闲，正是"排到非空闲时间"的根因）
    return parts


def _extract_course_name(raw_name: str) -> str:
    raw_name = raw_name.strip()
    if not raw_name:
        return ""
    raw_name = re.sub(r'^\d+[\d,\-]*\([\u4e00-\u9fa5]+\)', '', raw_name)
    raw_name = re.sub(r'\[[^\]]*\]', '', raw_name)
    raw_name = re.sub(r'[\(（][^\)）]*[\d班][^\)）]*[\)）]', '', raw_name)
    raw_name = re.sub(r'\d{2,}[\u4e00-\u9fa5]{1,4}[\u4e00-\u9fa5\d]*班[，,\s]*[\u4e00-\u9fa5\d]*班?', '', raw_name)
    raw_name = re.sub(r'\s+[\u4e00-\u9fa5]{2,3}[,，][\u4e00-\u9fa5]{2,3}(?:[,，][\u4e00-\u9fa5]{2,3})*', '', raw_name)
    raw_name = re.sub(r'[\u4e00-\u9fa5]*在线[\u4e00-\u9fa5]*', '', raw_name)
    raw_name = re.sub(r'^[\s，,、]+|[\s，,、]+$', '', raw_name)
    raw_name = _strip_building_prefix(raw_name)
    return raw_name.strip()


_VENUE = (r'[\u4e00-\u9fa5]{0,6}'
          r'(?:体育馆|田径场|球场|操场|礼堂|报告厅|图书馆|实训楼|机房|实验室|食堂)\d*')


def _place_in(text: str) -> str:
    m = re.search(r'([\u4e00-\u9fa5]+楼[\u4e00-\u9fa5]*(?:馆|厅|场|室)\d*|[\u4e00-\u9fa5]+楼\d+)', text)
    if m:
        return m.group(1)
    # 不带「楼」的场馆名（体育馆、报告厅…）；只在整格没有楼名时才兜底
    venue = re.search(_VENUE, text)
    if venue:
        return venue.group(0)
    if re.search(r'在线课程?[\u4e00-\u9fa5]*测评?考试?平台?', text) or "线上" in text:
        return "线上"
    return ""


def _week_in(text: str) -> str:
    m = re.search(r'([\d,\-]+\([\u4e00-\u9fa5]+\))', text)
    if m:
        return m.group(1).strip().replace("（", "(").replace("）", ")")
    m2 = re.search(r'(\d{1,2}(?:[\-,]\d{1,2})*)\s*周', text)
    if m2:
        return m2.group(1)
    return ""


def _parse_detail_segment(text: str) -> Optional[_CellCourse]:
    text = text.strip()
    if not text:
        return None
    course = _CellCourse()

    bracket_match = _PAT_DETAIL.search(text)
    if bracket_match:
        course.course_name = bracket_match.group(1)

    course.week = _week_in(text)
    course.place = _place_in(text)
    return course if course.course_name else None


def _parse_brief_segment(text: str) -> Optional[_CellCourse]:
    text = text.strip()
    if not text:
        return None
    course = _CellCourse()

    week_value = _week_in(text)
    if week_value:
        course.week = week_value

    place_value = _place_in(text)
    if place_value:
        course.place = place_value

    if week_value:
        name_area = text[:text.find(week_value)]
    else:
        name_area = text

    if place_value and place_value in name_area:
        idx = name_area.rfind(place_value)
        before = name_area[:idx]
        after = name_area[idx + len(place_value):]
        if not before.strip():
            name_area = after
        elif not after.strip():
            name_area = before
        else:
            name_area = before + after

    course.course_name = _extract_course_name(name_area)

    if not course.course_name:
        name_match = re.match(r'([\u4e00-\u9fa5A-Za-z][\u4e00-\u9fa5A-Za-z0-9 ]*)', text)
        if name_match:
            course.course_name = name_match.group(1).strip()

    return course if course.course_name else None


def parse_cell(text: str) -> List[Tuple[str, str, str]]:
    """把一个课程格解析成 ``[(课程名, 教室, 周次), ...]``。"""
    return [(c.course_name, c.place, c.week) for c in parse_course_cell(text)]


# --------------------------------------------------------------- 课表解析


def _header_day_map(header_cells) -> dict[int, str]:
    """表头单元格 → 星期。显式映射，避免列偏移导致整体错位。"""
    mapping: dict[int, str] = {}
    for idx, cell in enumerate(header_cells):
        text = cell.get_text(strip=True)
        m = re.search(r"(?:周|星期)([一二三四五六日天])", text)
        if m:
            mapping[idx] = _DAY_CHAR[m.group(1)]
    return mapping


_PLACEHOLDERS = {"", "-", "—", "/", "无", "0", "?", "？"}


def _is_meaningful(text: str) -> bool:
    """课程格里可能有空白占位符；但除明确占位符外，任何内容都必须被当成"该解析"的格。"""
    stripped = text.strip()
    return len(stripped) >= 2 and stripped not in _PLACEHOLDERS


_CN_PERIOD = {
    "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6,
    "七": 7, "八": 8, "九": 9, "十": 10, "十一": 11, "十二": 12,
}


def parse_row_period(label: str) -> Optional[int]:
    """行首「第一节 / 第1节 / 1-2节」→ 节次序号（第几节）。

    真实课表页一行一节，写的是「第一节…第十二节」；老式页面写「1-2节」两节一段。
    """
    label = (label or "").strip()
    m = re.match(r"^第\s*([一二三四五六七八九十]+)\s*节", label)
    if m:
        return _CN_PERIOD.get(m.group(1))
    m = re.match(r"^第?\s*(\d{1,2})\s*节", label)
    if m:
        return int(m.group(1))
    return None


def _band_of(period: Optional[int]) -> Optional[str]:
    if period and 1 <= period <= 12:
        return SLOTS[(period - 1) // 2]
    return None


_PERIOD_MARKER = re.compile(r"\[\s*(\d{2}(?:-\d{2})+)\s*节\s*\]")


def _bands_in(chunk: str) -> List[str]:
    """详情块里的 ``[01-02节]`` / ``[01-02-03-04节]`` 才是课程真正占的节次。"""
    marker = _PERIOD_MARKER.search(chunk)
    if not marker:
        return []
    bands: List[str] = []
    for raw in marker.group(1).split("-"):
        band = _band_of(int(raw))
        if band and band not in bands:
            bands.append(band)
    return bands


def _course_blocks(text: str) -> List[dict]:
    """按 ``课程名[课程号][类型]`` 把一格切成若干门课。

    真实页面的一格是这样（多门课之间用一长串短横线隔开）::

        Web开发技术课程设计
        24计科2班，24计科01班（卓越班）1-2(全部)
        厚德楼201
        -----------------
        Web开发技术课程设计[32][必修]
        秦安碧
        24计科2班，24计科01班（卓越班）
        (69)
        1-2(全部)[01-02-03-04节]
        厚德楼201

    只有详情块带课程号和 ``[xx-yy节]``，所以以它为锚点切块最稳；块尾切到
    下一个短横线分隔符，避免把下一门课的概要行算进本块。
    """
    marks = list(_PAT_DETAIL.finditer(text))
    blocks: List[dict] = []
    for index, mark in enumerate(marks):
        next_mark = marks[index + 1].start() if index + 1 < len(marks) else len(text)
        sep = re.search(r"-{5,}", text[mark.start():])
        end = mark.start() + sep.start() if sep else len(text)
        chunk = text[mark.start():min(end, next_mark)]

        name = _strip_building_prefix(mark.group(1))
        if not _looks_like_course_name(name):
            continue
        blocks.append(
            {
                "name": name,
                "weeks": _week_in(chunk),
                "room": _place_in(chunk),
                "bands": _bands_in(chunk),
            }
        )
    return blocks


def parse_timetable_html(html: str) -> ParseResult:
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find(id="kbtable")
    if table is None:
        tables = soup.find_all("table")
        table = max(tables, key=lambda t: len(t.get_text())) if tables else None
    if table is None:
        return ParseResult(warnings=["页面里找不到课表表格（kbtable），可能未登录或页面结构变化"])

    rows = table.find_all("tr")
    if not rows:
        return ParseResult(warnings=["课表表格没有任何行"])

    result = ParseResult()
    day_map = _header_day_map(rows[0].find_all(["th", "td"]))
    if not day_map:
        # 无表头时按惯例假设第 1..7 列是周一到周日，并明确告警
        day_map = {i: DAYS[i - 1] for i in range(1, 8)}
        result.warnings.append("表头没有识别出星期，按第 1~7 列=周一到周日处理，请核对")

    for row_index, row in enumerate(rows[1:], start=1):
        cells = row.find_all(["th", "td"])
        if len(cells) < 2:
            continue  # 表头残行
        label = cells[0].get_text(" ", strip=True)
        if label.startswith("备注"):
            continue

        row_bands: List[str] = []
        band = _band_of(parse_row_period(label))
        if band:
            row_bands.append(band)
        if not row_bands:
            slot = detect_slot(label)
            if slot:
                row_bands.append(slot)
        if not row_bands:
            result.warnings.append(f"第 {row_index} 行认不出节次（行首={label[:12]!r}），已跳过")
            continue

        for col_index, cell in enumerate(cells):
            day = day_map.get(col_index)
            if day is None:
                continue
            text = cell.get_text("\n", strip=True)
            if not _is_meaningful(text):
                continue
            result.cells_seen += 1

            blocks = _course_blocks(text)
            if blocks:
                result.cells_parsed += 1
                for block in blocks:
                    for block_band in (block["bands"] or row_bands):
                        result.courses.append(
                            RawCourse(
                                name=block["name"],
                                day=day,
                                slot=block_band,
                                room=block["room"],
                                weeks=block["weeks"],
                            )
                        )
                continue

            entries = parse_cell(text)
            if not entries:
                result.warnings.append(
                    f"{day} {row_bands[0]} 节的课程格无法解析：{text[:40]!r}"
                )
                continue
            result.cells_parsed += 1
            for name, room, weeks in entries:
                for row_band in row_bands:
                    result.courses.append(
                        RawCourse(name=name, day=day, slot=row_band, room=room, weeks=weeks)
                    )

    # 同一门课可能被"概要行"和"详情块"各说一次，按全字段去重
    seen = set()
    unique: List[RawCourse] = []
    for course in result.courses:
        key = (course.day, course.slot, course.name, course.room, course.weeks)
        if key in seen:
            continue
        seen.add(key)
        unique.append(course)
    result.courses = unique

    if not result.courses and result.cells_seen == 0:
        result.warnings.append("课表里没有任何课程内容，请确认该周是否已排课")
    return result


def parse_student_name(html: str) -> str:
    """从教务页面顶栏抓学生姓名：`演示管理员(2025000001)` → `演示管理员`。

    登录页和课表页都带这个 div，所以登录和「解析课表」两条路都能顺手把姓名刷对。
    """
    m = re.search(r'id="Top1_divLoginName"[^>]*>([^<]+)', html or "")
    if not m:
        return ""
    return re.sub(r"\s*\([^)]*\)\s*", "", m.group(1).strip()).strip()


# --------------------------------------------------------------- 登录与抓取


def _encode_inp(text: str) -> str:
    """教务前端的自定义 Base64：码表以 '=' 结尾，填充用 64。"""
    key = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/="
    out, i = "", 0
    while i < len(text):
        c1 = ord(text[i]); i += 1
        c2 = ord(text[i]) if i < len(text) else -1; i += 1
        c3 = ord(text[i]) if i < len(text) else -1; i += 1
        e1 = c1 >> 2
        e2 = ((c1 & 3) << 4) | (max(c2, 0) >> 4 if c2 >= 0 else 0)
        e3 = 64 if c2 < 0 else ((c2 & 15) << 2) | (max(c3, 0) >> 6 if c3 >= 0 else 0)
        e4 = 64 if (c2 < 0 or c3 < 0) else (c3 & 63)
        out += key[e1] + key[e2] + key[e3] + key[e4]
    return out


def encode_credentials(student_no: str, password: str) -> str:
    return _encode_inp(student_no) + "%%%" + _encode_inp(password)


def select_server(student_no: str) -> str:
    digits = "".join(ch for ch in student_no if ch.isdigit())
    if not digits:
        return SERVER_URLS[0]
    return SERVER_URLS[int(digits) % len(SERVER_URLS)]


def _is_error_page(html: str) -> bool:
    """教务的课表页出错时会返回「非法请求」之类的一句话页面。"""
    return "非法" in html or "错误" in html[:500]


class JiaowuClient:
    """一次会话内的教务交互。

    登录与抓取流程照搬原项目 ``CourseCrawler``：

    - ``POST {server}/xk/LoginToXk``，表单字段 ``encoded`` 是前端自研 base64
      （``学号%%%密码``）；
    - 必须带 ``Referer``，否则教务会踢回登录页；
    - 失败时**重建 Session** 再试，避免上一次登录残留的坏 cookie；
    - 课表页返回「非法/错误」时，先访问「我的桌面」建立会话上下文再重试。

    实例内部持有 requests.Session，不要把密码存进属性。
    """

    def __init__(self, student_no: str, timeout: int = 15,
                 cookies: object = None, max_retry: int = MAX_LOGIN_RETRY) -> None:
        self.student_no = student_no
        self.timeout = timeout
        self.max_retry = max_retry
        self.base_url = select_server(student_no)
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        self.student_name = ""
        self.last_error = ""
        if cookies:
            self._restore_cookies(cookies)

    def _restore_cookies(self, cookies) -> None:
        """回放登录时存下的 cookie；带 domain/path 才会真的被发出去。"""
        if isinstance(cookies, dict):
            for name, value in cookies.items():
                self.session.cookies.set(name, value)
            return
        for item in cookies:
            if not isinstance(item, dict) or not item.get("name"):
                continue
            self.session.cookies.set(
                item["name"],
                item.get("value", ""),
                domain=item.get("domain") or None,
                path=item.get("path") or "/",
            )

    def _fresh_session(self) -> requests.Session:
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        return self.session

    def login(self, password: str) -> str:
        login_url = self.base_url + "/xk/LoginToXk"

        for attempt in range(1, self.max_retry + 1):
            session = self._fresh_session()
            headers = {
                "Content-Type": "application/x-www-form-urlencoded",
                "Referer": self.base_url + "/",
            }
            try:
                response = session.post(
                    login_url,
                    data={"encoded": encode_credentials(self.student_no, password)},
                    headers=headers,
                    timeout=self.timeout,
                    allow_redirects=True,
                )
            except requests.RequestException as exc:
                self.last_error = f"连不上教务系统：{type(exc).__name__}"
                time.sleep(LOGIN_RETRY_DELAY)
                continue

            # 原项目把 3xx 直接视为登录成功
            if 300 <= response.status_code < 400:
                return self.student_name
            if response.status_code != 200:
                self.last_error = f"教务系统返回异常状态码 {response.status_code}"
                time.sleep(LOGIN_RETRY_DELAY)
                continue

            body = response.text
            # 打回登录页 = 账号密码不对，重试也没用，直接报错
            if _looks_like_login_page(body) and len(body) < 5000:
                raise JiaowuError("学号或密码不正确")
            if "alert" in body.lower() and ("密码" in body or "用户名" in body):
                raise JiaowuError("学号或密码不正确")

            self.student_name = parse_student_name(body)
            return self.student_name

        raise JiaowuError(self.last_error or "登录失败")

    def fetch_timetable(self, week: int | None = None) -> str:
        url = self.base_url + "/xskb/xskb_list.do"
        params = {"zc": week} if week else {}

        try:
            response = self.session.get(url, params=params, timeout=self.timeout)
            response.raise_for_status()
        except requests.RequestException as exc:
            raise JiaowuError(f"抓取课表失败：{type(exc).__name__}") from None

        if _is_error_page(response.text):
            # 原项目经验：先走一次「我的桌面」，让会话上下文完整，再要课表
            try:
                self.session.get(
                    self.base_url + "/jxzl/jxzl_query?Ves632DSdyV=NEW_XSD_WDZM",
                    timeout=self.timeout,
                )
                response = self.session.get(url, params=params, timeout=self.timeout)
                response.raise_for_status()
            except requests.RequestException as exc:
                raise JiaowuError(f"抓取课表失败：{type(exc).__name__}") from None
            if _is_error_page(response.text):
                raise JiaowuError("课表页面返回错误，请稍后重试")

        return response.text


class JiaowuVerifier:
    """真实身份验证：能登上教务系统即通过。密码用完即弃，不写入任何存储。"""

    def verify(self, student_no: str, password: str) -> VerifyResult:
        bundle = self.login(student_no, password)
        return VerifyResult(bundle.ok, name=bundle.name, reason=bundle.reason)

    def login(self, student_no: str, password: str, week: int | None = None) -> LoginBundle:
        if not student_no or not password:
            return LoginBundle(False, reason="学号和密码都不能为空")
        client = JiaowuClient(student_no)
        try:
            name = client.login(password)
        except JiaowuError as exc:
            return LoginBundle(False, reason=str(exc))
        try:
            html = client.fetch_timetable(week)
            parsed = parse_timetable_html(html)
        except JiaowuError as exc:
            # 身份已成立但课表没抓到：不算登录失败，让页面提示"课表抓取失败"
            return LoginBundle(True, name=name, reason=str(exc), parse=None,
                               cookies=cookie_jar_list(client.session.cookies))
        return LoginBundle(True, name=name, parse=parsed,
                           cookies=cookie_jar_list(client.session.cookies))

    def refresh(self, student_no: str, cookies: object) -> LoginBundle:
        """用登录时存下的教务 cookie 直接抓课表，不重新登录、不碰密码。"""
        if not cookies:
            return LoginBundle(False, reason="没有保存的教务会话，请重新登录")
        client = JiaowuClient(student_no, cookies=cookies)
        try:
            html = client.fetch_timetable()
        except JiaowuError as exc:
            return LoginBundle(False, reason=str(exc))
        if _looks_like_login_page(html):
            return LoginBundle(False, reason="教务会话已过期，请退出后重新登录")
        return LoginBundle(True, name=parse_student_name(html),
                           parse=parse_timetable_html(html),
                           cookies=cookie_jar_list(client.session.cookies))


def _looks_like_login_page(html: str) -> bool:
    """教务把过期会话打回登录页；这时 HTML 里只剩登录表单。

    注意：**真实课表页本身也含 ``Logon.do``**（右上角的退出/切换用户链接），
    所以只要页面上有 ``id="kbtable"`` 就必须判定为"已登录"，
    否则会把好好的课表当成过期会话。
    """
    if not html:
        return True
    if 'id="kbtable"' in html:
        return False
    head = html[:5000]
    return "Logon.do" in head or "method=logon" in head


def demo_parse() -> ParseResult:
    """演示模式的课表 + 一句醒目的说明，避免样本被当成真实数据。"""
    result = sample_timetable()
    result.warnings.insert(
        0, "演示模式：课表来自真实抓取的样本页面回放，不是教务系统上的实时数据"
    )
    return result


def sample_timetable() -> ParseResult:
    """演示模式用的课表：回放一份**真实抓取保存**的课表页面，再走同一套解析。

    以前这里是按学号随机编的假课程——演示看着像课表，真跑起来又对不上，
    根本分不清是爬虫坏了还是数据是假的。现在只保留真实样本这一条路。
    样本路径可用 ``APP_STUB_HTML`` 覆盖。
    """
    path = Path(os.environ.get("APP_STUB_HTML") or str(SAMPLE_HTML_PATH))
    if not path.exists():
        return ParseResult(warnings=[f"演示课表样本缺失：{path}"])
    return parse_timetable_html(path.read_text(encoding="utf-8"))


class StubVerifier:
    """本地开发与测试用的假教务。只有列在 accounts 里的学号能登录。"""

    def __init__(self, accounts: dict[str, str], names: dict[str, str] | None = None) -> None:
        self.accounts = dict(accounts)
        self.names = dict(names or {})

    def verify(self, student_no: str, password: str) -> VerifyResult:
        bundle = self.login(student_no, password)
        return VerifyResult(bundle.ok, name=bundle.name, reason=bundle.reason)

    def login(self, student_no: str, password: str) -> LoginBundle:
        if not student_no or not password:
            return LoginBundle(False, reason="学号和密码都不能为空")
        expected = self.accounts.get(student_no)
        if expected is None:
            return LoginBundle(False, reason="教务系统中查不到这个学号")
        if expected != password:
            return LoginBundle(False, reason="学号或密码不正确")
        name = self.names.get(student_no, f"同学{student_no[-4:]}")
        return LoginBundle(True, name=name, parse=demo_parse(),
                           cookies=[{"name": "stub_session", "value": student_no, "domain": "", "path": "/"}])

    def refresh(self, student_no: str, cookies: object) -> LoginBundle:
        """演示模式下的"用 cookie 抓课表"：cookie 对上就重放示例课表。"""
        if cookie_map(cookies).get("stub_session") != student_no:
            return LoginBundle(False, reason="教务会话已过期，请退出后重新登录")
        return LoginBundle(True, parse=demo_parse(),
                           cookies=cookies)


def build_verifier(mode: str, stub_accounts: dict[str, str] | None = None) -> IdentityVerifier:
    if mode == "real":
        return JiaowuVerifier()
    return StubVerifier(stub_accounts or {})
