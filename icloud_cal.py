# -*- coding: utf-8 -*-
"""아이폰 기본 캘린더(iCloud)에 일정을 직접 써 넣습니다.

    python icloud_cal.py --list                 # 내 캘린더 목록 보기
    python icloud_cal.py --test                 # 연결만 확인
    python icloud_cal.py --add "8월 8일 13시 회의"  # 한 건 넣어 보기

구독 캘린더(docs/law.ics)와 무엇이 다른가
----------------------------------------
로스쿨 공지 캘린더는 '구독'입니다. 우리가 파일을 올려 두면 아이폰이 가끔
읽어 갑니다. 읽기 전용이고, 언제 읽어 갈지는 아이폰 마음입니다.

여기는 반대로 **아이폰이 쓰는 그 캘린더에 우리가 직접 써 넣습니다.** 애플이
CalDAV 라는 표준 방식을 열어 두고 있어서, 앱전용 암호만 있으면 표준
라이브러리만으로 넣을 수 있습니다. 넣는 즉시 폰에 뜨고, 폰에서 고치거나
지울 수도 있습니다.

암호에 대하여
------------
애플 ID 본체 암호가 아니라 **앱전용 암호**를 씁니다. appleid.apple.com 에서
발급하는 16글자짜리로, 캘린더 접근에만 쓰이고 언제든 따로 폐기할 수 있습니다.
config.json 에 저장되며 이 파일은 저장소에 올라가지 않습니다(.gitignore).
"""

import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import build_ics
import store

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")

ROOT_URL = "https://caldav.icloud.com/"
TIMEOUT = 30

DAV = "{DAV:}"
CALDAV = "{urn:ietf:params:xml:ns:caldav}"

PRODID = "-//law-secretary//quickadd//KO//"


class CalDAVError(Exception):
    pass


# ---------------------------------------------------------------------------
# 설정
# ---------------------------------------------------------------------------
def load_config():
    if not os.path.exists(CONFIG_PATH):
        return {}
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as fp:
            return json.load(fp)
    except (OSError, json.JSONDecodeError):
        return {}


def save_config(data):
    with open(CONFIG_PATH, "w", encoding="utf-8") as fp:
        json.dump(data, fp, ensure_ascii=False, indent=2)
        fp.write("\n")


def credentials(config=None):
    config = config if config is not None else load_config()
    apple_id = (config.get("icloud_id") or "").strip()
    password = (config.get("icloud_password") or "").strip()
    if not apple_id or not password:
        raise CalDAVError(
            "iCloud 설정이 없습니다.\n"
            "  python setup_icloud.py 를 먼저 한 번 실행해 주세요."
        )
    return apple_id, password


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """자동 따라가기를 끕니다.

    파이썬 3.11 부터는 호스트가 바뀌는 리디렉션에서 Authorization 헤더를
    지웁니다(보안상 옳은 동작입니다). 그런데 iCloud 는 caldav.icloud.com 에서
    pNN-caldav.icloud.com 으로 넘기기 때문에, 그냥 두면 인증이 빠진 채로
    따라가서 401 이 납니다. 그래서 직접 따라가면서 인증을 다시 붙입니다.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_opener = urllib.request.build_opener(_NoRedirect)


def _request(method, url, apple_id, password, body=None, depth=None):
    token = base64.b64encode(f"{apple_id}:{password}".encode("utf-8")).decode("ascii")

    for _ in range(5):
        headers = {
            "Authorization": f"Basic {token}",
            "User-Agent": "law-secretary/1.0",
        }
        if body is not None:
            headers["Content-Type"] = "text/xml; charset=utf-8"
        if depth is not None:
            headers["Depth"] = str(depth)

        data = body.encode("utf-8") if isinstance(body, str) else body
        req = urllib.request.Request(url, data=data, headers=headers, method=method)

        try:
            with _opener.open(req, timeout=TIMEOUT) as resp:
                return resp.status, resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            if exc.code in (301, 302, 307, 308):
                location = exc.headers.get("Location")
                if not location:
                    raise CalDAVError(f"리디렉션에 주소가 없습니다 (HTTP {exc.code})") from exc
                url = urllib.parse.urljoin(url, location)
                continue
            if exc.code == 401:
                raise CalDAVError(
                    "애플 ID 또는 앱전용 암호가 맞지 않습니다.\n"
                    "  appleid.apple.com 에서 앱전용 암호를 새로 발급받아\n"
                    "  python setup_icloud.py 를 다시 실행해 주세요."
                ) from exc
            detail = exc.read().decode("utf-8", errors="replace")[:200]
            raise CalDAVError(f"HTTP {exc.code} {exc.reason}\n  {detail}") from exc
        except (urllib.error.URLError, OSError) as exc:
            raise CalDAVError(f"iCloud 에 연결하지 못했습니다: {exc}") from exc

    raise CalDAVError("리디렉션이 너무 많습니다")


def _propfind(url, apple_id, password, body, depth=0):
    status, text = _request("PROPFIND", url, apple_id, password, body=body, depth=depth)
    if status not in (200, 207):
        raise CalDAVError(f"PROPFIND 가 HTTP {status} 를 돌려줬습니다")
    try:
        return ET.fromstring(text)
    except ET.ParseError as exc:
        raise CalDAVError(f"iCloud 응답을 읽지 못했습니다: {exc}") from exc


# ---------------------------------------------------------------------------
# 캘린더 찾기
# ---------------------------------------------------------------------------
PROP_PRINCIPAL = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<d:propfind xmlns:d="DAV:"><d:prop><d:current-user-principal/></d:prop></d:propfind>'
)
PROP_HOME = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<d:propfind xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">'
    "<d:prop><c:calendar-home-set/></d:prop></d:propfind>"
)
PROP_CALENDARS = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<d:propfind xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">'
    "<d:prop><d:displayname/><d:resourcetype/>"
    "<c:supported-calendar-component-set/></d:prop></d:propfind>"
)


def discover(apple_id, password):
    """쓸 수 있는 캘린더 목록을 [(이름, 주소), ...] 로 돌려줍니다."""
    tree = _propfind(ROOT_URL, apple_id, password, PROP_PRINCIPAL)
    node = tree.find(f".//{DAV}current-user-principal/{DAV}href")
    if node is None or not node.text:
        raise CalDAVError("iCloud 계정 정보를 찾지 못했습니다")
    principal = urllib.parse.urljoin(ROOT_URL, node.text.strip())

    tree = _propfind(principal, apple_id, password, PROP_HOME)
    node = tree.find(f".//{CALDAV}calendar-home-set/{DAV}href")
    if node is None or not node.text:
        raise CalDAVError("캘린더 위치를 찾지 못했습니다")
    home = urllib.parse.urljoin(principal, node.text.strip())

    tree = _propfind(home, apple_id, password, PROP_CALENDARS, depth=1)

    found = []
    for resp in tree.findall(f"{DAV}response"):
        href = resp.find(f"{DAV}href")
        if href is None or not href.text:
            continue
        url = urllib.parse.urljoin(home, href.text.strip())
        if url.rstrip("/") == home.rstrip("/"):
            continue                        # 홈 자기 자신

        if resp.find(f".//{DAV}resourcetype/{CALDAV}calendar") is None:
            continue                        # 캘린더가 아닌 것(주소록 등)

        # 일정(VEVENT)을 받는 캘린더만. 미리알림 전용 캘린더가 섞여 있습니다.
        comps = resp.findall(f".//{CALDAV}supported-calendar-component-set/{CALDAV}comp")
        if comps and not any(c.get("name") == "VEVENT" for c in comps):
            continue

        name_node = resp.find(f".//{DAV}displayname")
        name = (name_node.text or "").strip() if name_node is not None else ""
        found.append((name or "(이름 없음)", url))

    if not found:
        raise CalDAVError("일정을 넣을 수 있는 캘린더를 찾지 못했습니다")
    return found


# ---------------------------------------------------------------------------
# 어느 날에 일정이 있는지 (달력에 점 찍는 용도)
# ---------------------------------------------------------------------------
# 공지에서 뽑은 일정은 '구독' 캘린더라 CalDAV 로는 보이지 않습니다.
# 구독은 아이폰이 각자 알아서 받아가는 것이라 서버에 없습니다.
# 그래서 공개된 캘린더 파일을 직접 읽습니다.
NOTICE_ICS_URL = "https://rlawodlr9667-hub.github.io/lawschool-secretary/law.ics"

# 창을 열 때마다 물어보면 느립니다. 잠깐은 재사용합니다.
_CACHE = {"key": None, "at": None, "events": {}}
_CACHE_SECONDS = 120

QUERY_TEMPLATE = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<c:calendar-query xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">'
    "<d:prop><c:calendar-data><c:expand start=\"{start}\" end=\"{end}\"/>"
    "</c:calendar-data></d:prop>"
    '<c:filter><c:comp-filter name="VCALENDAR">'
    '<c:comp-filter name="VEVENT">'
    '<c:time-range start="{start}" end="{end}"/>'
    "</c:comp-filter></c:comp-filter></c:filter></c:calendar-query>"
)


def _unfold(text):
    """접힌 줄을 다시 잇습니다. 이어지는 줄은 공백이나 탭으로 시작합니다."""
    lines = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw[:1] in (" ", "\t") and lines:
            lines[-1] += raw[1:]
        else:
            lines.append(raw)
    return lines


def _to_date(value):
    """DTSTART/DTEND 값 하나를 날짜로."""
    value = value.strip()
    if len(value) == 8 and value.isdigit():                 # 20260808
        return datetime.strptime(value, "%Y%m%d").date()
    if value.endswith("Z"):                                 # 20260808T040000Z
        try:
            moment = datetime.strptime(value, "%Y%m%dT%H%M%SZ")
        except ValueError:
            return None
        # UTC 로 적힌 시각은 한국 날짜로 옮겨야 합니다. 안 그러면 오전 9시
        # 이전 일정이 하루 앞 날짜에 점으로 찍힙니다.
        return moment.replace(tzinfo=timezone.utc).astimezone(store.KST).date()
    if "T" in value and len(value) >= 8:                    # TZID 가 붙은 지역시각
        try:
            return datetime.strptime(value[:8], "%Y%m%d").date()
        except ValueError:
            return None
    return None


def _unescape(text):
    """ICS 가 막아 둔 글자를 되돌립니다. 막을 때의 역순이어야 합니다."""
    out = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "\\" and i + 1 < len(text):
            nxt = text[i + 1]
            out.append({"n": "\n", "N": "\n"}.get(nxt, nxt))
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def events_in_ics(text, limit_days=400):
    """캘린더 글에서 {날짜: [제목, ...]} 을 모읍니다. 여러 날짜리는 펼칩니다."""
    found = {}
    start = end = None
    title = ""

    for line in _unfold(text):
        name, _, value = line.partition(":")
        key = name.split(";")[0].upper()

        if key == "BEGIN" and value.strip().upper() == "VEVENT":
            start = end = None
            title = ""
        elif key == "DTSTART":
            start = _to_date(value)
        elif key == "DTEND":
            end = _to_date(value)
        elif key == "SUMMARY":
            title = _unescape(value).strip().replace("\n", " ")
        elif key == "END" and value.strip().upper() == "VEVENT":
            if start is None:
                continue
            last = end or start
            # 종일 일정의 DTEND 는 '다음 날 0시'라서 하루를 빼야 합니다.
            if end is not None and end > start:
                last = end - timedelta(days=1)
            span = min((last - start).days, limit_days)
            for offset in range(max(span, 0) + 1):
                found.setdefault(start + timedelta(days=offset), []).append(
                    title or "(제목 없음)"
                )
            start = end = None
            title = ""

    return found


def dates_in_ics(text, limit_days=400):
    """일정이 있는 날짜만."""
    return set(events_in_ics(text, limit_days))


def _merge(into, more):
    for day, titles in more.items():
        into.setdefault(day, []).extend(titles)
    return into


# 캘린더 목록은 좀처럼 바뀌지 않습니다. 매번 찾으면 PROPFIND 3번이
# 그냥 버려집니다. 프로그램이 켜져 있는 동안 한 번만 찾습니다.
_CALENDARS = None


def calendars_cached(apple_id, password):
    global _CALENDARS
    if _CALENDARS is None:
        _CALENDARS = discover(apple_id, password)
    return _CALENDARS


def _icloud_dates(start, end, config):
    """iCloud 의 모든 캘린더에서 해당 기간의 일정 날짜를 모읍니다.

    캘린더가 7개면 하나씩 물어볼 때 5초가 넘게 걸립니다. 기다리는 시간의
    대부분이 '응답을 기다리는' 시간이라, 동시에 물어보면 한 개 걸리는
    시간으로 줄어듭니다.
    """
    try:
        apple_id, password = credentials(config)
    except CalDAVError:
        return set()

    body = QUERY_TEMPLATE.format(
        start=f"{start:%Y%m%d}T000000Z", end=f"{end:%Y%m%d}T000000Z"
    )

    try:
        calendars = calendars_cached(apple_id, password)
    except CalDAVError:
        return set()

    def ask(url):
        try:
            status, text = _request("REPORT", url, apple_id, password,
                                    body=body, depth=1)
            if status not in (200, 207):
                return {}
            tree = ET.fromstring(text)
        except (CalDAVError, ET.ParseError):
            return {}                       # 하나가 실패해도 나머지는 봅니다

        events = {}
        for node in tree.findall(f".//{CALDAV}calendar-data"):
            if node.text:
                _merge(events, events_in_ics(node.text))
        return events

    found = {}
    with ThreadPoolExecutor(max_workers=min(8, len(calendars))) as pool:
        for events in pool.map(ask, [url for _name, url in calendars]):
            _merge(found, events)
    return found


def _notice_events(config):
    """공지에서 뽑은 일정(구독 캘린더)도 함께 보여 줍니다."""
    url = (config.get("notice_ics_url") or NOTICE_ICS_URL).strip()
    if not url:
        return {}
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "law-secretary/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            return events_in_ics(resp.read().decode("utf-8", errors="replace"))
    except (urllib.error.URLError, OSError):
        return {}


def event_titles(start, end, config=None, use_cache=True):
    """[start, end] 사이의 {날짜: [제목, ...]}. 실패해도 예외를 내지 않습니다.

    달력에 점을 찍고 제목을 보여 주는 용도라, 하나쯤 못 가져와도
    창은 떠야 합니다.
    """
    config = config if config is not None else load_config()
    key = (start, end)
    now = datetime.now(store.KST)

    if use_cache and _CACHE["key"] == key and _CACHE["at"] is not None:
        if (now - _CACHE["at"]).total_seconds() < _CACHE_SECONDS:
            return _CACHE["events"]

    # iCloud 와 공지 캘린더는 서로 상관없으니 동시에 물어봅니다.
    with ThreadPoolExecutor(max_workers=2) as pool:
        mine = pool.submit(_icloud_dates, start, end, config)
        notices = pool.submit(_notice_events, config)
        events = _merge(mine.result(), notices.result())

    # 같은 일정이 여러 캘린더에 겹쳐 있을 수 있습니다. 순서는 지키면서 겹침만 뺍니다.
    trimmed = {}
    for day, titles in events.items():
        if not (start <= day <= end):
            continue
        trimmed[day] = list(dict.fromkeys(titles))

    _CACHE.update({"key": key, "at": now, "events": trimmed})
    return trimmed


def event_dates(start, end, config=None, use_cache=True):
    """일정이 있는 날짜만."""
    return frozenset(event_titles(start, end, config, use_cache))


def clear_cache():
    _CACHE.update({"key": None, "at": None, "events": {}})


# ---------------------------------------------------------------------------
# 일정 만들어 넣기
# ---------------------------------------------------------------------------
def build_vevent(event, uid=None, now=None):
    """nlp_date.parse() 가 준 것을 iCalendar 한 덩어리로 만듭니다.

    build_ics.py 의 글자 규칙(fold/escape)을 그대로 씁니다. 두 벌로 두면
    한쪽만 고쳐져서 한글 제목이 깨지는 사고가 납니다.
    """
    uid = uid or f"{uuid.uuid4()}@law-secretary"
    now = now or datetime.now(store.KST)

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:{PRODID}",
        "CALSCALE:GREGORIAN",
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"DTSTAMP:{build_ics.utc_stamp(now)}",
        f"SUMMARY:{build_ics.escape_text(event['title'])}",
    ]

    if event["all_day"]:
        lines.append(f"DTSTART;VALUE=DATE:{event['start']:%Y%m%d}")
        lines.append(f"DTEND;VALUE=DATE:{event['end']:%Y%m%d}")
    else:
        # UTC 로 적습니다. 이러면 VTIMEZONE 블록이 필요 없고, 애플이
        # 사용자의 시간대에 맞춰 알아서 보여 줍니다.
        lines.append(f"DTSTART:{build_ics.utc_stamp(event['start'])}")
        lines.append(f"DTEND:{build_ics.utc_stamp(event['end'])}")

    lines += ["END:VEVENT", "END:VCALENDAR"]

    out = []
    for line in lines:
        out.extend(build_ics.fold(line))
    return b"\r\n".join(out) + b"\r\n", uid


def add_event(event, config=None):
    """일정 하나를 iCloud 에 올립니다. 성공하면 (캘린더이름, uid)."""
    config = config if config is not None else load_config()
    apple_id, password = credentials(config)

    calendar_url = (config.get("icloud_calendar_url") or "").strip()
    calendar_name = config.get("icloud_calendar_name") or "iCloud"
    if not calendar_url:
        raise CalDAVError(
            "어느 캘린더에 넣을지 정해지지 않았습니다.\n"
            "  python setup_icloud.py 를 실행해 캘린더를 골라 주세요."
        )

    body, uid = build_vevent(event)
    url = calendar_url.rstrip("/") + f"/{uid}.ics"

    token = base64.b64encode(f"{apple_id}:{password}".encode("utf-8")).decode("ascii")
    req = urllib.request.Request(
        url, data=body, method="PUT",
        headers={
            "Authorization": f"Basic {token}",
            "Content-Type": "text/calendar; charset=utf-8",
            # 같은 주소에 이미 뭔가 있으면 덮어쓰지 말고 실패하라는 뜻입니다.
            # uid 는 새로 만든 값이라 부딪힐 일이 없지만, 부딪혔다면 그건
            # 뭔가 잘못된 상황이므로 조용히 덮어쓰면 안 됩니다.
            "If-None-Match": "*",
        },
    )

    try:
        with _opener.open(req, timeout=TIMEOUT) as resp:
            if resp.status not in (200, 201, 204):
                raise CalDAVError(f"일정을 넣지 못했습니다 (HTTP {resp.status})")
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            raise CalDAVError("앱전용 암호가 더 이상 맞지 않습니다. 다시 발급받아 주세요.") from exc
        detail = exc.read().decode("utf-8", errors="replace")[:200]
        raise CalDAVError(f"일정을 넣지 못했습니다 (HTTP {exc.code})\n  {detail}") from exc
    except (urllib.error.URLError, OSError) as exc:
        raise CalDAVError(f"iCloud 에 연결하지 못했습니다: {exc}") from exc

    return calendar_name, uid


# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="iCloud 캘린더에 직접 일정을 넣습니다")
    ap.add_argument("--list", action="store_true", help="캘린더 목록 보기")
    ap.add_argument("--test", action="store_true", help="연결만 확인")
    ap.add_argument("--add", metavar="문장", help="한 줄을 해석해 넣어 보기")
    args = ap.parse_args()

    try:
        config = load_config()
        apple_id, password = credentials(config)

        if args.list or args.test:
            calendars = discover(apple_id, password)
            print(f"연결 성공 — 캘린더 {len(calendars)}개")
            chosen = (config.get("icloud_calendar_url") or "").strip()
            for name, url in calendars:
                mark = " ← 여기에 넣습니다" if url.rstrip("/") == chosen.rstrip("/") else ""
                print(f"  - {name}{mark}")
            return 0

        if args.add:
            import nlp_date
            event = nlp_date.parse(args.add)
            name, _uid = add_event(event, config)
            print(f"넣었습니다 → [{name}] {nlp_date.describe(event)}")
            return 0

    except CalDAVError as exc:
        print(f"실패: {exc}")
        return 1

    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main())
