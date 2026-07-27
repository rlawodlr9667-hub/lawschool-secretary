# -*- coding: utf-8 -*-
"""챗봇이 읽을 검색용 파일 docs/notices.json 을 만듭니다.

사용법:
    python build_index.py
    python build_index.py --selftest

왜 이런 파일을 따로 만드나
-------------------------
채팅에 답하는 일은 Cloudflare Worker 가 맡습니다. 노트북이 꺼져 있어도
답이 가야 하고, 공짜여야 하고, 즉시 와야 하기 때문입니다. 그런데 Worker 는
자바스크립트로 돌아갑니다.

여기서 흔히 저지르는 실수는 search.py 의 판단 규칙을 자바스크립트로
'또 한 벌' 옮겨 적는 것입니다. 그러면 한쪽만 고쳐져서 노트북과 챗봇의
대답이 달라집니다.

그래서 **어려운 판단은 전부 파이썬이 미리 해 둡니다.**
  - 공지 본문에서 뽑아낸 일정
  - 제목에 적힌 마감일 ("(신청 ~7/19(일))")
  - 마감일을 모를 때 언제까지 유효하다고 볼지

Worker 가 하는 일은 '오늘 날짜와 견주기'와 '낱말 맞춰보기'뿐입니다.
규칙이 바뀌면 이 파일만 고치면 양쪽이 함께 바뀝니다.
"""

import argparse
import json
import os
import sys
from datetime import date, timedelta

import search
import sources as cfg
import store

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

INDEX_PATH = os.path.join(store.DOCS_DIR, "notices.json")


def build(today=None):
    """docs/notices.json 에 넣을 자료를 만듭니다."""
    today = today or store.now_kst().date()

    # 일정을 뽑아 둔 글: 일정 전부(지난 것 포함)를 그대로 싣습니다.
    # 지난 일정도 있어야 Worker 가 '언제까지 유효한가'를 스스로 판단합니다.
    detailed = {}
    for _name, data in store.all_extracted():
        url = data.get("url") or ""
        if not url:
            continue
        events = []
        for event in data.get("events") or []:
            when = search._parse_date(event.get("start_date"))
            if when is None:
                continue
            events.append([when.isoformat(),
                           event.get("start_time") or "",
                           event.get("summary") or ""])
        events.sort()
        detailed[url] = {
            "title": data.get("title") or "(제목 없음)",
            "board": data.get("board") or "",
            "posted": data.get("posted_date") or "",
            "events": events,
        }

    posts = []
    seen = set()

    for _tab, board in cfg.all_boards():
        for post in store.load_board_posts(board["board_key"]):
            url = post.get("url") or ""
            if not url or url in seen:
                continue
            seen.add(url)

            extra = detailed.pop(url, None)
            title = (extra or post).get("title") or post.get("title") or "(제목 없음)"
            posted = search._parse_date((extra or {}).get("posted")
                                        or post.get("date"))
            events = (extra or {}).get("events") or []

            posts.append(_shape(title, url,
                                post.get("board_name") or board["name"],
                                posted, events))

    # 게시판 목록에서는 밀려났지만 일정은 뽑아 둔 글도 함께 싣습니다.
    # 게시판은 최근 몇 건만 들고 있어서, 오래된 글은 여기에만 남아 있습니다.
    for url, extra in detailed.items():
        posts.append(_shape(extra["title"], url, extra["board"],
                            search._parse_date(extra["posted"]), extra["events"]))

    # 유효기간이 이미 한참 지난 것은 실을 필요가 없습니다. 파일만 커집니다.
    cutoff = today - timedelta(days=7)
    posts = [p for p in posts if p["d"] >= cutoff.isoformat()]
    posts.sort(key=lambda p: (p["d"], p["t"]))

    return {
        "generated": store.now_iso(),
        "today": today.isoformat(),
        "unknown_days": search.RECENT_DAYS,
        "posts": posts,
    }


def _shape(title, url, board, posted, events):
    """글 하나를 챗봇이 쓸 모양으로. 열쇠말을 짧게 쓴 것은 파일 크기 때문입니다.

    d = 이 날까지는 유효하다고 본다
    k = 마감일을 아는가 (모르면 '마감일 미상'이라고 표시해야 합니다)
    """
    written = search.deadline_in_title(title, posted)

    if events:
        valid_until = max(e[0] for e in events)
        known = True
    elif written is not None:
        valid_until = written.isoformat()
        known = True
        events = [[valid_until, "", "제목에 적힌 마감"]]
    elif posted is not None:
        # 마감일을 모르면 '올라온 지 얼마 안 됐으면 아직 쓸모 있다'고 봅니다.
        valid_until = (posted + timedelta(days=search.RECENT_DAYS)).isoformat()
        known = False
    else:
        valid_until = "0000-00-00"          # 게시일도 모르면 싣지 않습니다
        known = False

    return {
        "t": title,
        "u": url,
        "b": board or "",
        "p": posted.isoformat() if posted else "",
        "e": events,
        "d": valid_until,
        "k": known,
    }


def write(data=None):
    data = data if data is not None else build()
    os.makedirs(store.DOCS_DIR, exist_ok=True)

    # 같은 내용이면 같은 바이트가 나오도록 정렬해서 씁니다.
    # (build_ics.py 와 같은 이유 — 쓸데없는 커밋이 쌓이지 않게)
    body = json.dumps(data, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))

    previous = None
    if os.path.exists(INDEX_PATH):
        with open(INDEX_PATH, "r", encoding="utf-8") as fp:
            previous = fp.read()

    if previous is not None:
        try:
            # 만든 시각과 오늘 날짜만 다른 것은 '바뀐 것 없음'으로 봅니다.
            volatile = ("generated", "today")
            old = {k: v for k, v in json.loads(previous).items()
                   if k not in volatile}
            new = {k: v for k, v in json.loads(body).items()
                   if k not in volatile}
            if old == new:
                print(f"바뀐 것 없음 — 글 {len(data['posts'])}건")
                return False
        except json.JSONDecodeError:
            pass

    with open(INDEX_PATH, "w", encoding="utf-8") as fp:
        fp.write(body)
    print(f"{INDEX_PATH} — 글 {len(data['posts'])}건, "
          f"{len(body.encode('utf-8')):,}바이트")
    return True


# ---------------------------------------------------------------------------
def selftest():
    today = date(2026, 7, 27)
    data = build(today)
    problems = []

    if not data["posts"]:
        problems.append("  글이 하나도 없습니다")

    for post in data["posts"]:
        for key in ("t", "u", "b", "p", "e", "d", "k"):
            if key not in post:
                problems.append(f"  {post.get('t')!r} 에 '{key}' 가 없습니다")
        if post["d"] < (today - timedelta(days=7)).isoformat():
            problems.append(f"  이미 한참 지난 글이 실렸습니다: {post['t']!r}")
        if post["k"] and not post["e"]:
            problems.append(f"  마감일을 안다면서 일정이 없습니다: {post['t']!r}")

    # 챗봇이 그대로 쓸 수 있는지 — 파이썬 검색 결과와 견줍니다.
    for word in ("장학금", "실무수습", "신청"):
        mine = {h["url"] for h in search.find(word, today)}
        theirs = set()
        needle = search._norm(word)
        for post in data["posts"]:
            hay = search._norm(post["t"] + " " + " ".join(e[2] for e in post["e"]))
            if needle in hay and post["d"] >= today.isoformat():
                theirs.add(post["u"])
        missing = mine - theirs
        if missing:
            problems.append(f"  '{word}': 검색 파일에서 빠진 글 {len(missing)}건")

    if problems:
        print(f"[실패] {len(problems)}건")
        for line in problems[:10]:
            print(line)
        return 1
    print(f"[통과] 검색 파일 이상 없음 — 글 {len(data['posts'])}건")
    return 0


def main():
    ap = argparse.ArgumentParser(description="챗봇이 읽을 검색 파일 만들기")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        return selftest()

    write()
    return 0


if __name__ == "__main__":
    sys.exit(main())
