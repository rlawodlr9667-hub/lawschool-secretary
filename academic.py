# -*- coding: utf-8 -*-
"""학교 공식 학사일정을 받아 우리 일정으로 만듭니다.

사용법:
    python academic.py             # 받아서 기록
    python academic.py --list      # 무엇이 들어갈지 화면으로만
    python academic.py --selftest  # 형식 검사

긁지 않습니다
------------
서강대가 학사일정을 공식 .ics 캘린더로 이미 공개하고 있습니다. HTML 을
뜯을 필요도, 화면이 바뀔 때마다 파서를 고칠 필요도 없습니다.

왜 아이폰에서 그냥 구독하지 않는가
--------------------------------
그 주소를 아이폰 캘린더에 바로 넣어도 일정은 보입니다. 다만 그렇게 하면
**우리 시스템은 그 일정의 존재를 모릅니다.** 그래서 이쪽으로 가져옵니다.

  - 마감 3일 전·1일 전·당일에 텔레그램 알림이 옵니다
  - Alt+Space 달력에 점이 찍힙니다
  - 챗봇에게 '수강신청' 이라고 물으면 걸립니다

공지에서 뽑은 일정과 같은 모양으로 state/extracted/ 에 떨어뜨리기만 하면,
캘린더·알림·검색이 한 줄도 고치지 않고 학사일정을 함께 다룹니다.
"""

import argparse
import hashlib
import os
import sys
import urllib.error
import urllib.request
from datetime import timedelta

import build_ics
import icloud_cal
import sources as cfg
import store

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BOARD_KEY = "academic"
TIMEOUT = 40

# 이 접두어로 시작하는 기록만 우리가 관리합니다. 공지에서 뽑은 일정은
# 건드리지 않습니다.
FILE_PREFIX = f"{BOARD_KEY}_"


# ---------------------------------------------------------------------------
# 받기
# ---------------------------------------------------------------------------
def fetch(url=None):
    """공식 캘린더를 받아 글자로 돌려줍니다. 실패하면 None."""
    url = url or cfg.ACADEMIC_ICS_URL
    req = urllib.request.Request(url, headers={"User-Agent": "law-secretary/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, OSError) as exc:
        print(f"학사일정을 받지 못했습니다: {exc}")
        return None


# ---------------------------------------------------------------------------
# 뜯기
# ---------------------------------------------------------------------------
def read_events(text):
    """VEVENT 를 (제목, 시작일, 마지막날) 목록으로.

    줄 잇기와 이스케이프 해제는 icloud_cal.py 의 것을 그대로 씁니다.
    실제로 '1\\,2학년 수강신청' 처럼 쉼표가 막혀서 오는 제목이 있습니다.
    """
    out = []
    summary = start = end = None

    for line in icloud_cal._unfold(text):
        name, _, value = line.partition(":")
        key = name.split(";")[0].upper()

        if key == "BEGIN" and value.strip().upper() == "VEVENT":
            summary = start = end = None
        elif key == "SUMMARY":
            summary = icloud_cal._unescape(value).strip().replace("\n", " ")
        elif key == "DTSTART":
            start = icloud_cal._to_date(value)
        elif key == "DTEND":
            end = icloud_cal._to_date(value)
        elif key == "END" and value.strip().upper() == "VEVENT":
            if summary and start:
                # 종일 일정의 DTEND 는 '다음 날 0시'라서 하루를 뺍니다.
                last = start
                if end is not None and end > start:
                    last = end - timedelta(days=1)
                out.append((summary, start, last))
            summary = start = end = None

    return out


def to_records(events, today=None):
    """뜯어낸 일정을 '공지에서 뽑은 일정'과 같은 모양으로 바꿉니다.

    여러 날 일정은 '시작'과 '마감' 두 건으로 쪼갭니다. 이 프로젝트의 기존
    규칙입니다(automation/extract_prompt.txt). 하나의 긴 일정으로 두면
    D-3 알림이 시작일 기준으로 울려서 정작 마감을 놓칩니다.
    """
    today = today or store.now_kst().date()
    cutoff = today - timedelta(days=cfg.ACADEMIC_PAST_DAYS)

    records = []
    for summary, start, last in events:
        if last < cutoff:
            continue                        # 지난 학기 것까지 들고 있지 않습니다

        # 제목을 그대로 파일 이름에 쓸 수 없습니다. store.safe_name() 은
        # 영문·숫자만 남기므로 한글 제목이 전부 '_' 가 되어, 같은 날짜에
        # 글자 수가 같은 두 일정이 같은 이름이 됩니다. 실제로 '겨울방학 시작'과
        # '계절수업 개강'이 부딪혀서 하나가 사라지고 있었습니다.
        # 제목을 짧게 줄인 값을 붙여 갈라 줍니다.
        digest = hashlib.sha1(summary.encode("utf-8")).hexdigest()[:10]
        slug = f"{start:%Y%m%d}-{digest}"
        post_id = f"{BOARD_KEY}:{slug}"

        if last == start:
            pieces = [("", summary, start)]
        else:
            pieces = [("start", f"{summary} 시작", start),
                      ("end", f"{summary} 마감", last)]

        payload = []
        for suffix, label, when in pieces:
            event_slug = f"{slug}-{suffix}" if suffix else slug
            payload.append({
                "all_day": True,
                "confidence": "high",
                # 근거는 원문 그대로. 챗봇 검색에서 이 글자도 함께 봅니다.
                "evidence": f"학사일정: {summary} ({start} ~ {last})",
                "slug": event_slug,
                "start_date": when.isoformat(),
                "start_time": None,
                "summary": label,
                # uid 는 미리 정해 둡니다. 나중에 바뀌면 아이폰에 같은 일정이
                # 두 개 생깁니다.
                "uid": build_ics.make_uid(post_id, event_slug),
            })

        records.append({
            "board": cfg.ACADEMIC_BOARD_NAME,
            "events": payload,
            "needs_manual": False,
            "no_events_reason": None,
            "post_id": post_id,
            "posted_date": start.isoformat(),
            "revision": 0,
            "tab": BOARD_KEY,
            "title": summary,
            "url": cfg.ACADEMIC_PAGE_URL,
        })

    records.sort(key=lambda r: r["post_id"])
    return records


# ---------------------------------------------------------------------------
# 저장
# ---------------------------------------------------------------------------
def _path_for(record):
    _board, _, post_id = record["post_id"].partition(":")
    return store.extracted_path(BOARD_KEY, post_id)


def save(records):
    """바뀐 것만 씁니다. 그리고 없어진 일정은 지웁니다.

    바뀌지 않은 파일을 다시 쓰지 않는 이유: 30분마다 도는 작업이라,
    내용이 같아도 파일을 새로 쓰면 쓸데없는 커밋이 계속 쌓입니다.
    (build_ics.py 가 같은 이유로 같은 규칙을 지킵니다)

    'extracted_at' 을 넣지 않는 것도 같은 이유입니다. 그 값을 넣으면
    내용이 그대로여도 실행할 때마다 파일이 달라집니다.
    """
    written = removed = 0
    keep = set()

    for record in records:
        path = _path_for(record)
        keep.add(os.path.basename(path))
        if store.read_json(path, None) == record:
            continue
        store.write_json(path, record)
        written += 1

    # 학교가 일정을 지웠거나 날짜를 바꾸면 옛 파일이 남습니다.
    # 그대로 두면 취소된 일정이 캘린더에 영영 남아 있게 됩니다.
    if os.path.isdir(store.EXTRACTED_DIR):
        for name in sorted(os.listdir(store.EXTRACTED_DIR)):
            if name.startswith(FILE_PREFIX) and name not in keep:
                os.remove(os.path.join(store.EXTRACTED_DIR, name))
                removed += 1

    return written, removed


# ---------------------------------------------------------------------------
# 화면
# ---------------------------------------------------------------------------
def show(records, today=None):
    today = today or store.now_kst().date()
    rows = []
    for record in records:
        for event in record["events"]:
            rows.append((event["start_date"], event["summary"]))
    rows.sort()

    upcoming = [r for r in rows if r[0] >= today.isoformat()]
    print(f"일정 {len(rows)}건 (앞으로 남은 것 {len(upcoming)}건)\n")
    for when, summary in upcoming[:25]:
        print(f"  {when}  {summary}")
    if len(upcoming) > 25:
        print(f"  ... 외 {len(upcoming) - 25}건")


# ---------------------------------------------------------------------------
# 자가검증
# ---------------------------------------------------------------------------
SAMPLE = """BEGIN:VCALENDAR
BEGIN:VEVENT
SUMMARY:1\\,2학년 수강신청
DTSTART;VALUE=DATE:20260819
DTEND;VALUE=DATE:20260820
END:VEVENT
BEGIN:VEVENT
SUMMARY:2026학년도 2학기 휴복학 신청
DTSTART;VALUE=DATE:20260727
DTEND;VALUE=DATE:20260808
END:VEVENT
BEGIN:VEVENT
SUMMARY:아주 오래된 일정
DTSTART;VALUE=DATE:20200101
DTEND;VALUE=DATE:20200102
END:VEVENT
END:VCALENDAR
"""


def selftest():
    from datetime import date

    today = date(2026, 7, 27)
    problems = []

    events = read_events(SAMPLE)
    if len(events) != 3:
        problems.append(f"  VEVENT 를 {len(events)}개만 읽었습니다 (3개여야)")

    # 쉼표 이스케이프가 풀려야 합니다
    if not any(e[0] == "1,2학년 수강신청" for e in events):
        problems.append(f"  제목의 이스케이프가 안 풀렸습니다: {[e[0] for e in events]}")

    records = to_records(events, today)

    if any("아주 오래된" in r["title"] for r in records):
        problems.append("  한참 지난 일정이 걸러지지 않았습니다")

    one_day = [r for r in records if r["title"] == "1,2학년 수강신청"]
    if not one_day:
        problems.append("  하루짜리 일정이 사라졌습니다")
    elif len(one_day[0]["events"]) != 1:
        problems.append(f"  하루짜리인데 {len(one_day[0]['events'])}건으로 쪼갰습니다")

    span = [r for r in records if "휴복학" in r["title"]]
    if not span:
        problems.append("  여러 날 일정이 사라졌습니다")
    else:
        names = [e["summary"] for e in span[0]["events"]]
        dates = [e["start_date"] for e in span[0]["events"]]
        if names != ["2026학년도 2학기 휴복학 신청 시작",
                     "2026학년도 2학기 휴복학 신청 마감"]:
            problems.append(f"  시작/마감으로 안 쪼개졌습니다: {names}")
        if dates != ["2026-07-27", "2026-08-07"]:
            # DTEND 는 다음 날 0시이므로 마감은 8/7 이어야 합니다
            problems.append(f"  마감일이 하루 밀렸습니다: {dates}")

    # build_ics 가 그대로 받아들이는 모양이어야 합니다
    for record in records:
        found = []
        build_ics.validate_events(record, found)
        if found:
            problems.append(f"  {record['post_id']}: {found[0]}")

    # uid 는 여러 번 만들어도 같아야 합니다
    again = to_records(events, today)
    if [e["uid"] for r in records for e in r["events"]] != \
       [e["uid"] for r in again for e in r["events"]]:
        problems.append("  uid 가 실행할 때마다 달라집니다")

    # 서로 다른 일정이 같은 파일을 쓰면 하나가 조용히 사라집니다.
    # 한글 제목이 파일 이름에서 전부 '_' 가 되던 탓에 실제로 겪었습니다.
    collide = read_events(SAMPLE) + [
        ("겨울방학 시작", date(2026, 12, 22), date(2026, 12, 22)),
        ("계절수업 개강", date(2026, 12, 22), date(2026, 12, 22)),
    ]
    made = to_records(collide, today)
    paths = [_path_for(r) for r in made]
    if len(set(paths)) != len(paths):
        problems.append("  서로 다른 일정이 같은 파일 이름을 씁니다")

    if problems:
        print(f"[실패] {len(problems)}건")
        for line in problems:
            print(line)
        return 1
    print(f"[통과] 학사일정 형식 이상 없음 — 시험 자료 {len(records)}건")
    return 0


# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="학교 공식 학사일정 가져오기")
    ap.add_argument("--list", action="store_true", help="화면으로만 보기")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        return selftest()

    text = fetch()
    if text is None:
        # 받기에 실패했을 때 기존 기록을 지우면, 네트워크가 한 번 끊긴 것
        # 때문에 학사일정이 통째로 사라집니다. 아무것도 하지 않습니다.
        print("기존 학사일정은 그대로 둡니다.")
        return 0

    events = read_events(text)
    if not events:
        print("일정을 하나도 찾지 못했습니다. 캘린더 주소가 바뀌었을 수 있습니다.")
        print(f"  {cfg.ACADEMIC_PAGE_URL} 에서 '캘린더 구독' 주소를 확인해 주세요.")
        return 1

    records = to_records(events)

    if args.list:
        show(records)
        return 0

    written, removed = save(records)
    print(f"학사일정 {len(records)}건 — 새로 쓴 것 {written}, 지운 것 {removed}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
