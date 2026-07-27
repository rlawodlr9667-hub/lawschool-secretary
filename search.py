# -*- coding: utf-8 -*-
"""'장학금' 처럼 한 단어를 던지면, 아직 안 지난 관련 공지를 찾아 줍니다.

사용법:
    python search.py 장학금
    python search.py 실무수습 모집
    python search.py --selftest

무엇을 '유효하다'고 보는가
-------------------------
공지는 대부분 시효가 있습니다. 지난달에 끝난 신청 안내를 보여주는 것은
안 보여주는 것만 못합니다. 그래서 세 갈래로 나눕니다.

  1. 마감이 남은 것    — 뽑아낸 일정 중 오늘 이후가 하나라도 있는 글
  2. 마감을 모르는 것  — 일정을 못 뽑았지만 최근에 올라온 글
                         (LEET 공지처럼 내용이 .hwp 안에 있는 경우가 많습니다)
  3. 지난 것          — 일정이 전부 과거인 글. 보여주지 않습니다.

2번을 버리지 않는 이유: 마감일을 모른다고 해서 지났다는 뜻은 아닙니다.
모르면 모른다고 적어서 보여주고, 판단은 사람이 합니다. 조용히 감추면
정작 중요한 공지를 놓칩니다.
"""

import argparse
import re
import sys
from datetime import date

import sources as cfg
import store

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

WEEKDAYS = ["월", "화", "수", "목", "금", "토", "일"]

# 일정을 못 뽑은 글은 이 기간 안에 올라온 것만 보여 줍니다.
# 마감일을 모르는 채로 작년 글까지 끌어오면 목록이 쓸모없어집니다.
RECENT_DAYS = 120

# 한 번에 보여줄 최대 건수
MAX_RESULTS = 12


def _norm(text):
    """띄어쓰기와 대소문자를 무시하고 견주기 위해 다듬습니다.

    한국어는 띄어쓰기가 사람마다 다릅니다. '장학금 신청'과 '장학금신청'이
    같은 말인데 못 찾으면 안 됩니다.
    """
    return "".join((text or "").split()).lower()


def _parse_date(text):
    try:
        return date.fromisoformat((text or "").strip())
    except (ValueError, AttributeError):
        return None


# 제목에 마감일이 그대로 적힌 글이 아주 많습니다.
#   "[학교추천] 2026년 해양경찰 실무수습 과정 참여 학생 모집 (신청 ~7/19(일))"
#   "[공익법센터 어필] 실무수습 모집 공고(~6.26.(금) 18:00)"
# 이걸 안 읽으면 이미 끝난 모집을 '마감일 미상'이라며 계속 보여 주게 됩니다.
TILDE_RE = re.compile(r"[~∼〜～]\s*(\d{1,2})\s*[./월]\s*(\d{1,2})")
UNTIL_RE = re.compile(r"(\d{1,2})\s*[./월]\s*(\d{1,2})\s*일?\s*(?:까지|마감)")


def deadline_in_title(title, posted):
    """제목에 적힌 마감일을 읽습니다. 없으면 None.

    연도는 글이 올라온 날을 기준으로 정합니다. 12월에 올라온 글이 '~1/5'
    라고 하면 그건 다음 해 1월입니다.
    """
    if not title:
        return None

    found = []
    for pattern in (TILDE_RE, UNTIL_RE):
        for match in pattern.finditer(title):
            month, day = int(match.group(1)), int(match.group(2))
            if not (1 <= month <= 12 and 1 <= day <= 31):
                continue
            year = posted.year if posted else store.now_kst().year
            try:
                when = date(year, month, day)
            except ValueError:
                continue
            # 게시일보다 한참 앞선 날짜면 해가 넘어간 것입니다.
            if posted and (posted - when).days > 30:
                try:
                    when = date(year + 1, month, day)
                except ValueError:
                    continue
            found.append(when)

    return max(found) if found else None


def _deadline_label(when, time_text, today):
    days = (when - today).days
    if days == 0:
        head = "오늘"
    elif days > 0:
        head = f"D-{days}"
    else:
        head = f"{-days}일 지남"
    clock = f" {time_text}" if time_text else ""
    return f"{head}  {when.month}/{when.day}({WEEKDAYS[when.weekday()]}){clock}"


# ---------------------------------------------------------------------------
# 모으기
# ---------------------------------------------------------------------------
def _from_extracted(today):
    """일정을 뽑아 둔 글. 마감이 남았는지 판단할 수 있습니다."""
    found = {}
    for _name, data in store.all_extracted():
        url = data.get("url") or ""
        if not url:
            continue

        upcoming = []
        for event in data.get("events") or []:
            when = _parse_date(event.get("start_date"))
            if when is not None and when >= today:
                upcoming.append((when, event.get("start_time"),
                                 event.get("summary") or ""))
        upcoming.sort()

        found[url] = {
            "title": data.get("title") or "(제목 없음)",
            "url": url,
            "board": data.get("board") or "",
            "posted": _parse_date(data.get("posted_date")),
            "upcoming": upcoming,
            "has_schedule": bool(data.get("events")),
            "haystack": _norm(" ".join(
                [data.get("title") or ""]
                + [e.get("summary") or "" for e in (data.get("events") or [])]
            )),
        }
    return found


def _from_posts(today, known):
    """아직 일정을 못 뽑은 글. 최근 것만 '마감 미상'으로 함께 보여 줍니다."""
    found = {}
    for _tab, board in cfg.all_boards():
        for post in store.load_board_posts(board["board_key"]):
            url = post.get("url") or ""
            if not url or url in known:
                continue
            posted = _parse_date(post.get("date"))
            if posted is None or (today - posted).days > RECENT_DAYS:
                continue

            title = post.get("title") or "(제목 없음)"
            found[url] = {
                "title": title,
                "url": url,
                "board": post.get("board_name") or board["name"],
                "posted": posted,
                "upcoming": [],
                "has_schedule": False,
                "haystack": _norm(title),
            }
    return found


# ---------------------------------------------------------------------------
# 찾기
# ---------------------------------------------------------------------------
def find(keyword, today=None, limit=MAX_RESULTS):
    """키워드에 걸리면서 아직 안 지난 글을 찾습니다."""
    today = today or store.now_kst().date()
    words = [_norm(w) for w in (keyword or "").split() if _norm(w)]
    if not words:
        return []

    records = _from_extracted(today)
    records.update(_from_posts(today, set(records)))

    hits = []
    for item in records.values():
        score = sum(1 for word in words if word in item["haystack"])
        if not score:
            continue

        # 아직 유효한지 한 곳에서 판단합니다. 뽑아낸 일정이 있는 글과
        # 없는 글의 판단이 갈리면, 한쪽에만 필터가 걸려 작년 글이 새어
        # 나옵니다(실제로 그랬습니다).
        if item["upcoming"]:
            item["status"] = "live"
        elif item["has_schedule"]:
            continue                    # 일정이 있었는데 전부 지났다 -> 버림
        else:
            written = deadline_in_title(item["title"], item["posted"])
            if written is not None:
                if written < today:
                    continue            # 제목에 적힌 마감이 지났다
                item["upcoming"] = [(written, None, "제목에 적힌 마감")]
                item["status"] = "live"
            elif item["posted"] is None or (today - item["posted"]).days > RECENT_DAYS:
                continue                # 마감도 모르고 오래되기까지 했다
            else:
                item["status"] = "unknown"

        item["score"] = score
        hits.append(item)

    def order(item):
        # 마감이 임박한 것부터. 마감을 모르는 것은 뒤로 보내되 최신순으로.
        if item["status"] == "live":
            return (0, -item["score"], item["upcoming"][0][0], "")
        return (1, -item["score"], date.max, "")

    hits.sort(key=order)
    return hits[:limit]


# ---------------------------------------------------------------------------
# 보여주기
# ---------------------------------------------------------------------------
def render(keyword, hits, today=None, as_html=False):
    """사용자가 요청한 양식 그대로 적습니다."""
    import html as html_mod

    today = today or store.now_kst().date()
    esc = (lambda s: html_mod.escape(s or "")) if as_html else (lambda s: s or "")
    head = (f"현재 {today.year}년 {today.month:02d}월 {today.day:02d}일 기준 "
            f"'{esc(keyword)}'에 대해 ")

    if not hits:
        return (head + "유효한 정보가 없어.\n\n"
                "다른 낱말로 해 볼래? 예: 장학금, 신청, 접수, 수업, 실무수습")

    lines = [head + "유효한 정보는 다음과 같아.", ""]
    for item in hits:
        title = esc(item["title"])
        if as_html:
            url = html_mod.escape(item["url"], quote=True)
            lines.append(f"· <a href=\"{url}\">{title}</a>")
        else:
            lines.append(f"· {title}")

        if item["status"] == "live":
            when, clock, summary = item["upcoming"][0]
            label = _deadline_label(when, clock, today)
            detail = f"{label} — {esc(summary)}" if summary else label
        else:
            posted = item["posted"]
            posted_text = f"{posted.month}/{posted.day} 게시" if posted else "게시일 미상"
            detail = f"마감일 미상 · {posted_text}"

        board = esc(item["board"])
        if as_html:
            lines.append(f"    <i>{detail} · {board}</i>")
        else:
            lines.append(f"    {detail} · {board}")
            lines.append(f"    {item['url']}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 자가검증
# ---------------------------------------------------------------------------
def selftest():
    today = date(2026, 7, 27)
    problems = []

    sample_live = {
        "title": "2026학년도 2학기 장학금 신청 안내", "url": "http://a",
        "board": "학사공지", "posted": date(2026, 7, 23),
        "upcoming": [(date(2026, 8, 7), "17:00", "장학금 신청 마감")],
        "has_schedule": True, "haystack": _norm("2026학년도 2학기 장학금 신청 안내"),
    }
    text = render("장학금", [dict(sample_live, status="live", score=1)], today)
    if "현재 2026년 07월 27일 기준 '장학금'에 대해 유효한 정보는 다음과 같아." not in text:
        problems.append("  머리글 양식이 요청과 다릅니다:\n    " + text.splitlines()[0])
    if "D-11" not in text:
        problems.append("  남은 날짜(D-11)가 안 보입니다")

    if "유효한 정보가 없어" not in render("없는말", [], today):
        problems.append("  결과가 없을 때의 문구가 빠졌습니다")

    # 띄어쓰기가 달라도 찾아야 합니다
    if _norm("장학금 신청") not in _norm("2학기장학금신청안내"):
        problems.append("  띄어쓰기를 무시한 견주기가 안 됩니다")

    # 제목에 적힌 마감일 읽기
    title_cases = [
        ("[학교추천] 해양경찰 실무수습 모집 (신청 ~7/19(일))", date(2026, 7, 6),
         date(2026, 7, 19)),
        ("[공익법센터 어필] 실무수습 모집 공고(~6.26.(금) 18:00)", date(2026, 6, 9),
         date(2026, 6, 26)),
        ("겨울 프로그램 신청 ~1/5(월)", date(2026, 12, 20), date(2027, 1, 5)),
        ("2026년 8월 7일까지 신청", date(2026, 7, 23), date(2026, 8, 7)),
        ("마감일이 적혀 있지 않은 안내", date(2026, 7, 1), None),
    ]
    for title, posted, expected in title_cases:
        got = deadline_in_title(title, posted)
        if got != expected:
            problems.append(f"  제목 마감일: {title!r}\n     기대 {expected} / 실제 {got}")

    # 실제 자료로 한 번 돌려 봅니다
    for word in ("장학금", "신청", "실무수습"):
        for item in find(word, today):
            if item["status"] == "live" and item["upcoming"][0][0] < today:
                problems.append(f"  '{word}': 지난 일정이 유효한 것으로 나왔습니다")

    if problems:
        print(f"[실패] {len(problems)}건")
        for line in problems:
            print(line)
        return 1
    print("[통과] 검색 규칙 이상 없음")
    return 0


def main():
    ap = argparse.ArgumentParser(description="아직 안 지난 관련 공지 찾기")
    ap.add_argument("words", nargs="*", help="찾을 낱말")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        return selftest()
    if not args.words:
        print(__doc__)
        return 1

    keyword = " ".join(args.words)
    print(render(keyword, find(keyword)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
