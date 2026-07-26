# -*- coding: utf-8 -*-
"""사람이 쓰듯 적은 한 줄을 일정으로 바꿉니다.

    "8월 8일 결전의날, 시작 13시, 두시간 동안"
      -> 8/8(토) 13:00~15:00  결전의날

사용법:
    python nlp_date.py --selftest        # 해석 규칙 자가검증
    python nlp_date.py "내일 3시 면담"     # 한 줄 해석해 보기

왜 Claude 를 부르지 않는가
--------------------------
이 해석은 Alt+Space 를 누른 직후에 눈앞에서 일어나야 합니다. Claude 를 부르면
2~5초가 걸리는데, 그 시간이면 그냥 캘린더 앱을 여는 게 빠릅니다. 그래서 흔한
표현은 규칙으로 즉시 처리하고, 규칙이 날짜를 못 찾은 경우에만 부르는 구조로
둡니다(부르는 쪽은 quickadd.py).

애매한 것은 추측하지 않고 그대로 드러냅니다
------------------------------------------
"1시"처럼 오전·오후가 없는 표현은 어느 쪽인지 알 수 없습니다. 여기서는 한국말
습관대로 1~7시는 오후, 8~12시는 오전으로 봅니다. 대신 **결과를 반드시 사람에게
보여주고 확인받는다**는 전제가 붙습니다. 확인 없이 캘린더에 넣으면 안 됩니다.
"""

import argparse
import re
import sys
from datetime import date, datetime, timedelta

import store

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

KST = store.KST

WEEKDAY_KO = ["월", "화", "수", "목", "금", "토", "일"]

# 한 글자 수사. "두시간", "세시간 반" 같은 표현에 씁니다.
NUM_WORDS = {
    "한": 1, "두": 2, "세": 3, "네": 4, "다섯": 5,
    "여섯": 6, "일곱": 7, "여덟": 8, "아홉": 9, "열": 10,
}

# 해석이 끝난 뒤 제목에서 털어낼 말들. 날짜·시각을 걷어내고 나면
# "시작", "부터" 같은 이음말만 남는데, 제목에 들어가면 지저분합니다.
STOPWORDS = [
    "시작", "부터", "까지", "동안", "예정", "그리고", "에서", "에는",
]

# 하나도 못 알아들었을 때 쓸 제목
DEFAULT_TITLE = "일정"

# 시각만 있고 얼마나 걸리는지 안 적었을 때
DEFAULT_MINUTES = 60


class ParseError(Exception):
    pass


# ---------------------------------------------------------------------------
# 조각 뜯어내기
# ---------------------------------------------------------------------------
# 뜯어낸 자리는 이 글자로 덮어 둡니다. 나중에 제목을 만들 때 지웁니다.
BLANK = "\x00"


def _blank(text, start, end):
    return text[:start] + BLANK * (end - start) + text[end:]


def _num(token):
    """'3' 또는 '세' 를 3 으로."""
    token = (token or "").strip()
    if not token:
        return None
    if token.isdigit():
        return int(token)
    return NUM_WORDS.get(token)


NUM_PATTERN = r"\d{1,2}|" + "|".join(NUM_WORDS)

# --- 날짜 ---
REL_DAYS = {"그저께": -2, "어제": -1, "오늘": 0, "내일": 1, "모레": 2, "글피": 3}

RE_YMD = re.compile(r"(20\d{2})\s*[-./년]\s*(\d{1,2})\s*[-./월]\s*(\d{1,2})\s*일?")
RE_MD_KO = re.compile(r"(\d{1,2})\s*월\s*(\d{1,2})\s*일")
RE_MD_SLASH = re.compile(r"(?<![\d:])(\d{1,2})/(\d{1,2})(?![\d:])")
RE_WEEKDAY = re.compile(r"(이번\s*주|다음\s*주|담주|次주)?\s*([월화수목금토일])요일")
RE_PAREN_DOW = re.compile(r"\(\s*[월화수목금토일]\s*\)")

# --- 시각 ---
MERIDIEM = r"(오전|오후|아침|저녁|밤|새벽)"
RE_RANGE = re.compile(
    MERIDIEM + r"?\s*(\d{1,2})\s*시(?!간)\s*(?:(\d{1,2})\s*분|(반))?"
    r"\s*(?:~|-|–|—|부터)\s*"
    + MERIDIEM + r"?\s*(\d{1,2})\s*시(?!간)\s*(?:(\d{1,2})\s*분|(반))?"
)
RE_RANGE_COLON = re.compile(
    r"(?<!\d)(\d{1,2}):([0-5]\d)\s*(?:~|-|–|—|부터)\s*(\d{1,2}):([0-5]\d)(?!\d)"
)
RE_TIME_KO = re.compile(
    MERIDIEM + r"?\s*(\d{1,2})\s*시(?!간)\s*(?:(\d{1,2})\s*분|(반))?"
)
RE_TIME_COLON = re.compile(r"(?<!\d)(\d{1,2}):([0-5]\d)(?!\d)")

# --- 걸리는 시간 ---
RE_DUR_HOUR = re.compile(rf"({NUM_PATTERN})\s*시간\s*(반)?")
# "30분간"은 기간이지만 "30분 간단회의"의 '간'은 제목입니다. 뒤에 한글이
# 이어지면 기간 표시가 아니라고 봅니다.
RE_DUR_MIN = re.compile(r"(\d{1,3})\s*분\s*(?:동안|간(?![가-힣]))?")
RE_ALLDAY = re.compile(r"하루\s*종일|종일")


def _apply_meridiem(hour, marker):
    """오전·오후 표시를 24시간제로 바꿉니다."""
    if marker in ("오후", "저녁", "밤"):
        return hour if hour >= 12 else hour + 12
    if marker in ("오전", "아침"):
        return 0 if hour == 12 else hour
    if marker == "새벽":
        return 0 if hour == 12 else hour
    # 표시가 없을 때. 한국말에서 "1시 보자"는 거의 오후입니다.
    if 1 <= hour <= 7:
        return hour + 12
    return hour


def _find_date(text, today):
    """날짜를 찾아 (날짜, 남은 글) 로 돌려줍니다. 못 찾으면 (None, 글)."""
    m = RE_PAREN_DOW.search(text)
    if m:                       # "8. 7.(금)" 의 요일 부분은 검산에만 쓰고 지웁니다
        text = _blank(text, m.start(), m.end())

    m = RE_YMD.search(text)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            return date(y, mo, d), _blank(text, m.start(), m.end())
        except ValueError as exc:
            raise ParseError(f"{y}년 {mo}월 {d}일은 없는 날짜입니다") from exc

    for word, offset in REL_DAYS.items():
        idx = text.find(word)
        if idx >= 0:
            return today + timedelta(days=offset), _blank(text, idx, idx + len(word))

    m = RE_WEEKDAY.search(text)
    if m:
        target = WEEKDAY_KO.index(m.group(2))
        ahead = (target - today.weekday()) % 7
        if ahead == 0:
            ahead = 7                       # "월요일"이면 오늘이 아니라 다음 월요일
        if m.group(1) and ("다음" in m.group(1) or "담주" in m.group(1)):
            ahead += 7 if ahead <= 6 else 0
        return today + timedelta(days=ahead), _blank(text, m.start(), m.end())

    for pattern in (RE_MD_KO, RE_MD_SLASH):
        m = pattern.search(text)
        if not m:
            continue
        mo, d = int(m.group(1)), int(m.group(2))
        if not (1 <= mo <= 12 and 1 <= d <= 31):
            continue
        # 연도를 안 적었을 때. 이미 두 달 넘게 지난 날짜면 내년으로 봅니다.
        # (7월에 "1월 5일"이라고 적으면 지난 1월이 아니라 내년 1월입니다)
        for year in (today.year, today.year + 1):
            try:
                found = date(year, mo, d)
            except ValueError as exc:
                raise ParseError(f"{mo}월 {d}일은 없는 날짜입니다") from exc
            if (today - found).days <= 60:
                return found, _blank(text, m.start(), m.end())
        return date(today.year + 1, mo, d), _blank(text, m.start(), m.end())

    return None, text


def _find_times(text):
    """시각을 찾아 (시작, 끝, 남은 글) 로. 시:분은 (시, 분) 짝입니다."""
    m = RE_RANGE_COLON.search(text)
    if m:
        start = (int(m.group(1)), int(m.group(2)))
        end = (int(m.group(3)), int(m.group(4)))
        return start, end, _blank(text, m.start(), m.end())

    m = RE_RANGE.search(text)
    if m:
        h1 = _apply_meridiem(int(m.group(2)), m.group(1))
        min1 = 30 if m.group(4) else int(m.group(3) or 0)
        # 끝시각에 오전·오후 표시가 없으면 시작과 같은 쪽으로 봅니다.
        # "1시~3시"는 오후 1시~오후 3시이지 오후 1시~새벽 3시가 아닙니다.
        raw2 = int(m.group(6))
        if m.group(5):
            h2 = _apply_meridiem(raw2, m.group(5))
        elif h1 >= 12 and raw2 < 12:
            h2 = raw2 + 12
        else:
            h2 = raw2
        min2 = 30 if m.group(8) else int(m.group(7) or 0)
        return (h1, min1), (h2, min2), _blank(text, m.start(), m.end())

    m = RE_TIME_KO.search(text)
    if m:
        hour = _apply_meridiem(int(m.group(2)), m.group(1))
        minute = 30 if m.group(4) else int(m.group(3) or 0)
        return (hour, minute), None, _blank(text, m.start(), m.end())

    m = RE_TIME_COLON.search(text)
    if m:
        return (int(m.group(1)), int(m.group(2))), None, _blank(text, m.start(), m.end())

    return None, None, text


def _find_duration(text):
    """걸리는 시간을 분 단위로. 못 찾으면 (None, 글)."""
    minutes = 0
    found = False

    m = RE_DUR_HOUR.search(text)
    if m:
        hours = _num(m.group(1))
        if hours is not None:
            minutes += hours * 60
            if m.group(2):                  # "두 시간 반"
                minutes += 30
            found = True
            text = _blank(text, m.start(), m.end())

    m = RE_DUR_MIN.search(text)
    if m:
        minutes += int(m.group(1))
        found = True
        text = _blank(text, m.start(), m.end())

    return (minutes if found else None), text


def _clean_title(text):
    text = text.replace(BLANK, " ")
    for word in STOPWORDS:
        text = text.replace(word, " ")
    text = re.sub(r"[,·~\-–—:]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    # 조사만 남은 꼬리를 떼어냅니다 ("결전의날에" -> "결전의날")
    text = re.sub(r"\s*(에|은|는|이|가|을|를)$", "", text).strip()
    return text or DEFAULT_TITLE


# ---------------------------------------------------------------------------
# 바깥에서 쓰는 것
# ---------------------------------------------------------------------------
def parse(text, today=None):
    """한 줄을 일정으로. 날짜를 못 찾으면 ParseError 를 냅니다.

    돌려주는 값:
        {"title": str, "all_day": bool,
         "start": datetime|date, "end": datetime|date|None}
    """
    if not (text or "").strip():
        raise ParseError("빈 줄입니다")

    today = today or datetime.now(KST).date()
    rest = text.strip()

    all_day_asked = bool(RE_ALLDAY.search(rest))
    if all_day_asked:
        m = RE_ALLDAY.search(rest)
        rest = _blank(rest, m.start(), m.end())

    day, rest = _find_date(rest, today)
    if day is None:
        raise ParseError("날짜를 찾지 못했습니다")

    start_hm, end_hm, rest = (None, None, rest) if all_day_asked else _find_times(rest)
    minutes, rest = _find_duration(rest)
    title = _clean_title(rest)

    if start_hm is None:
        # 시각이 없으면 종일 일정. 하루짜리 종일 일정의 끝은 '다음 날 0시'입니다.
        return {"title": title, "all_day": True, "start": day,
                "end": day + timedelta(days=1)}

    start = datetime(day.year, day.month, day.day, start_hm[0] % 24, start_hm[1], tzinfo=KST)

    if end_hm is not None:
        end = datetime(day.year, day.month, day.day, end_hm[0] % 24, end_hm[1], tzinfo=KST)
        if end <= start:                    # "밤 11시~1시" 처럼 자정을 넘긴 경우
            end += timedelta(days=1)
    else:
        end = start + timedelta(minutes=minutes or DEFAULT_MINUTES)

    return {"title": title, "all_day": False, "start": start, "end": end}


def describe(event):
    """사람에게 보여줄 한 줄. 확인받는 화면에 그대로 씁니다."""
    start = event["start"]
    if event["all_day"]:
        return f"{start.month}/{start.day}({WEEKDAY_KO[start.weekday()]}) 종일  {event['title']}"

    end = event["end"]
    head = f"{start.month}/{start.day}({WEEKDAY_KO[start.weekday()]}) {start:%H:%M}"
    if end.date() != start.date():
        return f"{head} ~ {end.month}/{end.day} {end:%H:%M}  {event['title']}"
    return f"{head}~{end:%H:%M}  {event['title']}"


# ---------------------------------------------------------------------------
# 자가검증
# ---------------------------------------------------------------------------
CASES = [
    # (입력, 기준일, 기대하는 describe 결과)
    ("8월 8일 결전의날, 시작 13시, 두시간 동안", "2026-07-26", "8/8(토) 13:00~15:00  결전의날"),
    ("내일 3시 면담", "2026-07-26", "7/27(월) 15:00~16:00  면담"),
    ("오늘 오전 9시 30분 회의", "2026-07-26", "7/26(일) 09:30~10:30  회의"),
    ("8/8 오후 2시~4시 스터디", "2026-07-26", "8/8(토) 14:00~16:00  스터디"),
    ("9월 3일 종일 워크숍", "2026-07-26", "9/3(목) 종일  워크숍"),
    ("모레 19:00 저녁약속", "2026-07-26", "7/28(화) 19:00~20:00  저녁약속"),
    ("금요일 10시 반 상담 한시간", "2026-07-26", "7/31(금) 10:30~11:30  상담"),
    ("2026-12-25 크리스마스", "2026-07-26", "12/25(금) 종일  크리스마스"),
    ("1월 5일 신년모임 7시", "2026-07-26", "1/5(화) 19:00~20:00  신년모임"),
    ("8월 10일 30분 간단회의 14시", "2026-07-26", "8/10(월) 14:00~14:30  간단회의"),
    ("다음주 수요일 오후 3시 세미나", "2026-07-26", "8/5(수) 15:00~16:00  세미나"),
    ("8월 8일 13:00-15:00 모의고사", "2026-07-26", "8/8(토) 13:00~15:00  모의고사"),
    # 자정을 넘기면 끝나는 날짜까지 보여 줍니다. 안 그러면 23:00~01:00 이
    # 22시간짜리 일정처럼 읽힙니다.
    ("내일 밤 11시부터 2시간 야작", "2026-07-26", "7/27(월) 23:00 ~ 7/28 01:00  야작"),
]


def selftest():
    problems = []
    for text, base, expected in CASES:
        today = date.fromisoformat(base)
        try:
            got = describe(parse(text, today))
        except ParseError as exc:
            problems.append(f"  {text!r}\n     오류: {exc}")
            continue
        if got != expected:
            problems.append(f"  {text!r}\n     기대: {expected}\n     실제: {got}")

    # 날짜가 없으면 반드시 실패해야 합니다. 조용히 오늘로 넣으면
    # 엉뚱한 날에 일정이 박힙니다.
    for text in ("회의", "그냥 아무 말", ""):
        try:
            parse(text, date(2026, 7, 26))
        except ParseError:
            pass
        else:
            problems.append(f"  {text!r} 는 날짜가 없으니 거절해야 하는데 통과했습니다")

    if problems:
        print(f"[실패] {len(problems)}건")
        for line in problems:
            print(line)
        return 1
    print(f"[통과] {len(CASES)}개 문장 해석 이상 없음")
    return 0


def main():
    ap = argparse.ArgumentParser(description="한 줄을 일정으로 바꿔 봅니다")
    ap.add_argument("text", nargs="*", help="해석할 문장")
    ap.add_argument("--selftest", action="store_true", help="해석 규칙 자가검증")
    args = ap.parse_args()

    if args.selftest:
        return selftest()

    if not args.text:
        print(__doc__)
        return 1

    try:
        event = parse(" ".join(args.text))
    except ParseError as exc:
        print(f"해석 실패: {exc}")
        return 1
    print(describe(event))
    return 0


if __name__ == "__main__":
    sys.exit(main())
