# -*- coding: utf-8 -*-
"""뽑아낸 일정들을 아이폰·아웃룩이 읽는 캘린더 파일로 만듭니다.

사용법:
    python build_ics.py             # docs/law.ics 를 다시 만듭니다
    python build_ics.py --selftest  # 만들어진 파일이 규격에 맞는지 검사
    python build_ics.py --list      # 어떤 일정이 들어갈지 화면으로 확인

같은 내용이면 같은 바이트가 나와야 합니다
------------------------------------------
새 일정이 없는데도 파일이 조금씩 달라지면, 30분마다 쓸데없는 커밋이 쌓이고
GitHub Pages 의 시간당 빌드 한도(10회)에 걸립니다. 그래서

  - DTSTAMP 에 '지금 시각'을 쓰지 않습니다. 일정을 뽑아낸 시각을 그대로 씁니다.
  - 파일을 이름순으로 읽습니다.
  - '지났는지' 판단은 오늘 0시 기준으로 합니다. 시:분 단위로 판단하면
    같은 날 두 번 실행했을 때 결과가 달라집니다.

그래서 build_ics.py 를 연달아 두 번 돌리면 git 에 아무 변화가 없어야 합니다.
변화가 생긴다면 위 세 가지 중 하나가 깨진 것입니다.
"""

import argparse
import hashlib
import re
import sys
from datetime import date, datetime, time, timedelta, timezone

import sources as cfg
import store

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

UTC = timezone.utc
KST = store.KST

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")

# 알림을 울릴 시각. 종일 일정은 자정이 기준이라, 그냥 '3일 전'으로 하면
# 새벽 0시에 울립니다. 아침 9시에 울리도록 맞춥니다.
ALARM_HOUR = 9

PRODID = "-//law-secretary//KO//"


# ---------------------------------------------------------------------------
# 검사
# ---------------------------------------------------------------------------
def parse_date(text):
    if not isinstance(text, str) or not DATE_RE.match(text):
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def validate_events(record, problems):
    """Claude 가 뽑아낸 일정을 믿지 않고 하나씩 확인합니다.

    이상한 것은 그 일정만 버리고 나머지는 살립니다. 파일 하나가 잘못됐다고
    캘린더 전체가 비면 안 됩니다.
    """
    label = record.get("post_id", "?")
    posted = parse_date(record.get("posted_date") or "")
    good = []

    for index, event in enumerate(record.get("events") or []):
        if not isinstance(event, dict):
            problems.append(f"{label}[{index}]: 일정 형식이 잘못됨")
            continue

        summary = (event.get("summary") or "").strip()
        if not summary:
            problems.append(f"{label}[{index}]: 제목이 비어 있음")
            continue

        start = parse_date(event.get("start_date") or "")
        if not start:
            problems.append(f"{label}[{index}]: 날짜가 YYYY-MM-DD 형식이 아님 "
                            f"({event.get('start_date')!r})")
            continue
        if not (2020 <= start.year <= 2100):
            problems.append(f"{label}[{index}]: 연도가 이상함 ({start.year})")
            continue

        # 공지가 올라온 날보다 1년 넘게 과거인 날짜는, 작년 공지를 인용한
        # 문장에서 잘못 주운 것으로 봅니다.
        if posted and start < posted - timedelta(days=365):
            problems.append(f"{label}[{index}]: 게시일보다 1년 이상 과거 ({start})")
            continue

        start_time = event.get("start_time")
        all_day = bool(event.get("all_day", not start_time))
        if start_time is not None and not TIME_RE.match(str(start_time)):
            problems.append(f"{label}[{index}]: 시각이 HH:MM 형식이 아님 "
                            f"({start_time!r}) — 종일 일정으로 바꿉니다")
            start_time, all_day = None, True
        if not start_time:
            all_day = True

        good.append({
            "slug": (event.get("slug") or f"e{index}").strip() or f"e{index}",
            "summary": summary,
            "start": start,
            "start_time": start_time,
            "all_day": all_day,
            "evidence": (event.get("evidence") or "").strip(),
            "uid": event.get("uid"),
            "index": index,
        })

    return good


# ---------------------------------------------------------------------------
# 고유 번호
# ---------------------------------------------------------------------------
def make_uid(post_id, slug):
    """일정 하나를 영원히 가리키는 번호.

    한 번 정하면 바꾸지 않습니다. 캘린더 앱은 이 번호로 '같은 일정'인지
    판단하기 때문에, 번호가 바뀌면 아이폰에 같은 일정이 두 개 생깁니다.
    그래서 계산한 값을 원본 JSON 에 적어 두고 다음부터는 그걸 그대로 씁니다.
    """
    digest = hashlib.sha1(f"{post_id}#{slug}".encode("utf-8")).hexdigest()
    return f"{digest[:32]}@law-secretary"


def assign_uids(records):
    """uid 가 없는 일정에 번호를 매기고 원본 파일에 적어 둡니다."""
    written = 0
    for name, record, events in records:
        changed = False
        raw_events = record.get("events") or []
        for event in events:
            if event["uid"]:
                continue
            event["uid"] = make_uid(record.get("post_id", name), event["slug"])
            if event["index"] < len(raw_events):
                raw_events[event["index"]]["uid"] = event["uid"]
                changed = True
        if changed:
            board_key, _, post_id = str(record.get("post_id", "")).partition(":")
            if board_key and post_id:
                store.save_extracted(board_key, post_id, record)
                written += 1
    return written


# ---------------------------------------------------------------------------
# ICS 글자 규칙
# ---------------------------------------------------------------------------
def escape_text(text):
    """iCalendar 가 특별하게 취급하는 글자를 막아 둡니다.

    콜론(:)은 이스케이프하지 않습니다. 자주 하는 실수인데, 값 안의 콜론은
    그대로 두는 것이 규격입니다.
    """
    text = (text or "").replace("\\", "\\\\")
    text = text.replace(";", "\\;").replace(",", "\\,")
    text = text.replace("\r\n", "\\n").replace("\n", "\\n").replace("\r", "\\n")
    # 제어문자는 파일을 깨뜨립니다.
    return "".join(ch for ch in text if ch >= " " or ch == "\t")


def fold(line):
    """한 줄을 75옥텟씩 잘라 여러 줄로 접습니다.

    규격이 '글자 수'가 아니라 '바이트 수' 기준이라는 점이 중요합니다.
    한글은 한 글자가 3바이트라 25자만 넘어도 접힙니다. 아무 데서나 자르면
    한글 한 글자가 반토막 나서 제목이 깨집니다. 그래서 자를 위치가 글자
    중간이면 한 바이트씩 뒤로 물러납니다.

    (UTF-8 에서 0b10xxxxxx 로 시작하는 바이트는 '앞 글자의 이어지는 부분'입니다)
    """
    raw = line.encode("utf-8")
    out = []
    first = True
    while raw:
        budget = 75 if first else 74      # 이어지는 줄은 맨 앞 공백이 1바이트를 씁니다
        cut = min(budget, len(raw))
        while 0 < cut < len(raw) and (raw[cut] & 0xC0) == 0x80:
            cut -= 1
        if cut == 0:                       # 한 글자가 예산보다 큰 경우는 없지만 대비
            cut = min(budget, len(raw))
        out.append(raw[:cut] if first else b" " + raw[:cut])
        raw = raw[cut:]
        first = False
    return out


def ical_duration(delta):
    """timedelta -> '-P2DT15H' 같은 규격 문자열."""
    total = int(delta.total_seconds())
    sign = "-" if total < 0 else ""
    total = abs(total)
    days, rest = divmod(total, 86400)
    hours, rest = divmod(rest, 3600)
    minutes, seconds = divmod(rest, 60)

    clock = ""
    if hours:
        clock += f"{hours}H"
    if minutes:
        clock += f"{minutes}M"
    if seconds:
        clock += f"{seconds}S"

    if days and not clock:
        return f"{sign}P{days}D"
    if days:
        return f"{sign}P{days}DT{clock}"
    return f"{sign}PT{clock or '0S'}"


def utc_stamp(dt):
    return dt.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


# ---------------------------------------------------------------------------
# 캘린더 만들기
# ---------------------------------------------------------------------------
def event_times(event):
    """일정의 시작 시각을 계산합니다.

    돌려주는 값: (DTSTART 줄, DTEND 줄, 알림 기준이 될 시각)

    시간이 붙은 일정은 UTC(끝에 Z)로 적습니다. VTIMEZONE 블록을 손으로 쓰다
    틀리는 것이 ICS 에서 가장 흔한 버그인데, 한국은 서머타임이 없어서
    UTC 로 바꿔 적으면 그 위험이 통째로 사라집니다.
    """
    if event["all_day"]:
        start = event["start"]
        # 종일 일정의 DTEND 는 '끝나는 날 다음 날'입니다. 하루짜리라도
        # 다음 날을 적어야 합니다. 빠뜨리면 일정이 안 보이거나 하루 밀립니다.
        return (f"DTSTART;VALUE=DATE:{start.strftime('%Y%m%d')}",
                f"DTEND;VALUE=DATE:{(start + timedelta(days=1)).strftime('%Y%m%d')}",
                datetime.combine(start, time(0, 0), tzinfo=KST))

    hour, minute = (int(x) for x in event["start_time"].split(":"))
    begin = datetime.combine(event["start"], time(hour, minute), tzinfo=KST)
    # 길이가 0인 일정은 일부 캘린더 앱이 그리지 않습니다.
    finish = begin + timedelta(minutes=30)
    return (f"DTSTART:{utc_stamp(begin)}",
            f"DTEND:{utc_stamp(finish)}",
            begin)


def alarm_times(event, anchor):
    """알림을 울릴 실제 시각들. (오프셋 일수, 시각) 목록."""
    out = []
    for days in cfg.REMINDER_OFFSETS:
        if event["all_day"]:
            # 자정이 아니라 그날 아침 9시에 울리게 합니다.
            when = datetime.combine(event["start"] - timedelta(days=days),
                                    time(ALARM_HOUR, 0), tzinfo=KST)
        elif days:
            when = anchor - timedelta(days=days)
        else:
            when = anchor - timedelta(hours=1)   # 당일은 1시간 전
        out.append((days, when))
    return out


def build_calendar(records, today):
    """VCALENDAR 전체를 줄 목록으로 만듭니다."""
    cutoff = today - timedelta(days=cfg.ICS_PAST_DAYS)
    midnight = datetime.combine(today, time(0, 0), tzinfo=KST)

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:{PRODID}",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{escape_text(cfg.CALENDAR_NAME)}",
        f"X-WR-CALDESC:{escape_text(cfg.CALENDAR_DESC)}",
        "X-WR-TIMEZONE:Asia/Seoul",
        "REFRESH-INTERVAL;VALUE=DURATION:PT1H",
        "X-PUBLISHED-TTL:PT1H",
    ]

    count = 0
    for _name, record, events in records:
        try:
            stamp = utc_stamp(datetime.fromisoformat(record["extracted_at"]))
        except (KeyError, ValueError, TypeError):
            # 시각을 못 읽으면 고정된 값을 씁니다. '지금'을 쓰면 파일이 매번 달라집니다.
            stamp = "20200101T000000Z"

        sequence = record.get("revision")
        sequence = sequence if isinstance(sequence, int) and sequence >= 0 else 0

        for event in events:
            if event["start"] < cutoff:
                continue

            dtstart, dtend, anchor = event_times(event)
            description = []
            if record.get("board"):
                description.append(f"[{record['board']}] {record.get('title', '')}")
            if event["evidence"]:
                description.append(f"근거: {event['evidence']}")
            if record.get("url"):
                description.append(record["url"])

            lines += [
                "BEGIN:VEVENT",
                f"UID:{event['uid']}",
                f"DTSTAMP:{stamp}",
                dtstart,
                dtend,
                f"SEQUENCE:{sequence}",
                f"SUMMARY:{escape_text(event['summary'])}",
                f"DESCRIPTION:{escape_text(chr(10).join(description))}",
            ]
            if record.get("url"):
                lines.append(f"URL:{record['url']}")
            lines.append("TRANSP:TRANSPARENT")

            for days, when in alarm_times(event, anchor):
                # 이미 지난 알림은 넣지 않습니다. 캘린더를 새로 구독할 때
                # 지난 알림이 한꺼번에 울리는 것을 막습니다.
                if when < midnight:
                    continue
                label = f"D-{days}" if days else "오늘"
                lines += [
                    "BEGIN:VALARM",
                    "ACTION:DISPLAY",
                    f"DESCRIPTION:{escape_text(label + ' ' + event['summary'])}",
                    f"TRIGGER:{ical_duration(when - anchor)}",
                    "END:VALARM",
                ]

            lines.append("END:VEVENT")
            count += 1

    lines.append("END:VCALENDAR")
    return lines, count


def render(lines):
    """줄 목록 -> 실제 파일 바이트. 모든 줄 끝은 CRLF 입니다."""
    folded = []
    for line in lines:
        folded.extend(fold(line))
    return b"\r\n".join(folded) + b"\r\n"


# ---------------------------------------------------------------------------
# 검사 (--selftest)
# ---------------------------------------------------------------------------
def selftest(path=None):
    path = path or store.ICS_PATH
    try:
        with open(path, "rb") as fp:
            raw = fp.read()
    except OSError as exc:
        print(f"[실패] 파일을 열 수 없습니다: {exc}")
        return 1

    errors = []

    # 1) 줄바꿈은 전부 CRLF 여야 합니다.
    if raw.replace(b"\r\n", b"").count(b"\n"):
        errors.append("CRLF 가 아닌 줄바꿈이 있습니다 (git 이 바꿔버렸을 수 있음)")
    if not raw.endswith(b"\r\n"):
        errors.append("마지막 줄이 CRLF 로 끝나지 않습니다")

    physical = raw.split(b"\r\n")[:-1]

    # 2) 물리적인 한 줄은 75옥텟을 넘으면 안 됩니다.
    for number, line in enumerate(physical, 1):
        if len(line) > 75:
            errors.append(f"{number}번째 줄이 {len(line)}옥텟입니다 (75 초과)")

    # 3) UTF-8 로 읽혀야 하고, 접힌 줄을 펴면 원래 글자가 나와야 합니다.
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        errors.append(f"UTF-8 로 읽을 수 없습니다 ({exc}) — 줄 접기가 한글을 반토막 냈습니다")
        text = raw.decode("utf-8", errors="replace")

    unfolded = [ln for ln in text.replace("\r\n ", "").split("\r\n") if ln]

    # 4) BEGIN 과 END 의 짝이 맞아야 합니다.
    stack = []
    for line in unfolded:
        if line.startswith("BEGIN:"):
            stack.append(line[6:])
        elif line.startswith("END:"):
            if not stack or stack.pop() != line[4:]:
                errors.append(f"BEGIN/END 짝이 맞지 않습니다: {line}")
    if stack:
        errors.append(f"닫히지 않은 블록: {', '.join(stack)}")

    # 5) 모든 일정에 UID·DTSTAMP·DTSTART 가 있어야 하고 UID 는 겹치면 안 됩니다.
    uids, current = [], None
    for line in unfolded:
        if line == "BEGIN:VEVENT":
            current = set()
        elif line == "END:VEVENT" and current is not None:
            for need in ("UID", "DTSTAMP", "DTSTART"):
                if need not in current:
                    errors.append(f"{need} 가 없는 일정이 있습니다")
            current = None
        elif current is not None:
            name = line.split(":")[0].split(";")[0]
            current.add(name)
            if name == "UID":
                uids.append(line[4:])

    duplicates = {u for u in uids if uids.count(u) > 1}
    if duplicates:
        errors.append(f"UID 가 겹칩니다: {', '.join(sorted(duplicates))}")

    # 6) 종일 일정의 DTEND 는 DTSTART 보다 뒤여야 합니다.
    start_value = None
    for line in unfolded:
        if line.startswith("DTSTART;VALUE=DATE:"):
            start_value = line.rsplit(":", 1)[1]
        elif line.startswith("DTEND;VALUE=DATE:") and start_value:
            if line.rsplit(":", 1)[1] <= start_value:
                errors.append(f"종일 일정의 DTEND 가 DTSTART 보다 뒤가 아닙니다 "
                              f"({start_value})")
            start_value = None

    events = sum(1 for ln in unfolded if ln == "BEGIN:VEVENT")
    if errors:
        print(f"[실패] 캘린더 검사에서 {len(errors)}개 문제를 찾았습니다:")
        for error in errors:
            print(f"  - {error}")
        return 1

    print(f"[통과] 캘린더 검사 이상 없음 — 일정 {events}개, "
          f"{len(physical)}줄, {len(raw):,}바이트")
    return 0


# ---------------------------------------------------------------------------
# 실행
# ---------------------------------------------------------------------------
def load_records():
    problems = []
    records = []
    for name, record in store.all_extracted():
        events = validate_events(record, problems)
        records.append((name, record, events))
    return records, problems


def main():
    parser = argparse.ArgumentParser(description="캘린더 파일 만들기")
    parser.add_argument("--selftest", action="store_true",
                        help="만들어진 파일이 규격에 맞는지 검사")
    parser.add_argument("--list", action="store_true",
                        help="들어갈 일정을 화면에 출력")
    args = parser.parse_args()

    if args.selftest:
        return selftest()

    records, problems = load_records()
    written = assign_uids(records)

    today = store.now_kst().date()
    lines, count = build_calendar(records, today)
    payload = render(lines)

    if args.list:
        for _name, record, events in records:
            for event in events:
                when = event["start"].isoformat()
                if not event["all_day"]:
                    when += " " + event["start_time"]
                print(f"  {when}  {event['summary']}  ({record.get('board', '')})")
        print(f"\n총 {count}개가 캘린더에 들어갑니다.")
        return 0

    import os
    os.makedirs(store.DOCS_DIR, exist_ok=True)
    old = b""
    try:
        with open(store.ICS_PATH, "rb") as fp:
            old = fp.read()
    except OSError:
        pass

    if old == payload:
        print(f"캘린더에 바뀐 것이 없습니다. (일정 {count}개)")
    else:
        # 반드시 바이너리로 씁니다. 텍스트 모드로 쓰면 윈도우에서 CRLF 가
        # CRCRLF 로 부풀고, 리눅스에서는 LF 로 줄어듭니다.
        with open(store.ICS_PATH, "wb") as fp:
            fp.write(payload)
        print(f"캘린더를 새로 만들었습니다. 일정 {count}개, {len(payload):,}바이트")

    if written:
        print(f"일정 {written}개 파일에 고유번호를 새로 적었습니다.")
    if problems:
        print(f"\n걸러낸 일정 {len(problems)}건:")
        for problem in problems[:20]:
            print(f"  - {problem}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
