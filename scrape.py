# -*- coding: utf-8 -*-
"""게시판을 둘러보고, 새 글이 있으면 알려주고, 일정 추출 대기열을 채웁니다.

사용법:
    python scrape.py                 # 평소 실행
    python scrape.py --dry-run       # 아무것도 보내지 않고 결과만 화면에 출력
    python scrape.py --seed-only     # 지금 글을 전부 '이미 본 것'으로 기록 (알림 없음)
    python scrape.py --board sg_1601 # 게시판 하나만

일 처리 순서가 곧 안전장치입니다
--------------------------------
    1) 목록 수집        한 게시판이 죽어도 나머지는 계속합니다
    2) 신규 판별        아래 세 가지 가드를 통과해야 '새 글'로 칩니다
    3) 목록 저장        탭 버튼이 읽을 파일
    4) 본문 수집        잘린 제목을 온전하게 채우고, Claude 대기열을 만듭니다
    5) 텔레그램 알림    ★ Claude 보다 먼저. Claude 가 실패해도 알림은 갑니다
    6) 기억 저장        알림이 성공했을 때만. 실패하면 다음 회차에 다시 시도합니다

5번과 6번의 순서를 바꾸면 안 됩니다. 먼저 기억해 버리면, 알림 발송이 실패했을 때
그 글은 영영 '이미 본 글'이 되어 알림이 사라집니다.
"""

import argparse
import sys
import traceback

import parsers as P
import sources as cfg
import store
import telegram_api as tg

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
except Exception:
    pass


# ---------------------------------------------------------------------------
# 1) 목록 수집
# ---------------------------------------------------------------------------
def collect(board_keys=None):
    """모든 게시판의 목록을 받아옵니다.

    한 게시판이 실패해도 예외를 밖으로 던지지 않습니다. 서강대 서버가
    잠깐 흔들렸다고 LEET 알림까지 멈추면 곤란합니다.
    """
    ok, failed = [], []
    for tab, board in cfg.all_boards():
        if board_keys and board["board_key"] not in board_keys:
            continue
        try:
            rows = P.fetch_list(board)
        except Exception as exc:
            failed.append((tab, board, f"{type(exc).__name__}: {exc}"))
            print(f"  [실패] {board['name']}: {type(exc).__name__}: {exc}")
            continue

        if not rows:
            # 글이 0개인 게시판은 없습니다. 마크업이 바뀐 것으로 봅니다.
            failed.append((tab, board, "글을 하나도 찾지 못했습니다 (게시판 구조 변경 의심)"))
            print(f"  [실패] {board['name']}: 글을 하나도 찾지 못했습니다")
            continue

        ok.append((tab, board, rows))
        print(f"  [수집] {board['name']}: {len(rows)}건")
    return ok, failed


# ---------------------------------------------------------------------------
# 2) 신규 판별
# ---------------------------------------------------------------------------
def detect_new(seen, collected):
    """새 글을 가려냅니다. 알림이 쏟아지지 않게 하는 가드가 여기 있습니다."""
    fresh_by_board, seeded, flooded = [], [], []

    for tab, board, rows in collected:
        entry = store.board_state(seen, board["board_key"])
        entry["fail_streak"] = 0
        ids = [r["post_id"] for r in rows]

        # 가드 ① 처음 보는 게시판이면 조용히 기억만 합니다.
        #        안 그러면 시작하자마자 알림 16개가 쏟아집니다.
        if not entry["seeded"]:
            store.remember_ids(entry, ids, cfg.SEEN_KEEP)
            entry["seeded"] = True
            seeded.append((tab, board, len(rows)))
            continue

        known = set(entry["ids"])
        fresh = [r for r in rows if r["post_id"] not in known]

        # 가드 ② 거의 전부가 새 글이면, 새 글이 쏟아진 게 아니라
        #        글 번호 체계가 바뀐 것입니다. 알림 대신 경고 한 줄만 보냅니다.
        if fresh and len(rows) >= 5 and len(fresh) / len(rows) > cfg.FLOOD_RATIO:
            store.remember_ids(entry, ids, cfg.SEEN_KEEP)
            flooded.append((tab, board, len(fresh), len(rows)))
            continue

        if fresh:
            fresh_by_board.append((tab, board, fresh))

    return fresh_by_board, seeded, flooded


# ---------------------------------------------------------------------------
# 3) 탭에 보여줄 목록 저장
# ---------------------------------------------------------------------------
def save_lists(collected):
    """게시판별로 최신 글을 저장해 둡니다.

    버튼을 눌렀을 때 그 자리에서 크롤링하지 않기 위해서입니다. 버튼은 10초
    안에 응답해야 하는데 학교 서버가 그만큼 빠르다는 보장이 없습니다.

    게시판별로 따로 저장하는 이유: 한 게시판 수집이 실패해도 나머지 게시판의
    목록은 예전 것이 그대로 남아 탭이 비지 않습니다.
    """
    keep = max(cfg.LIST_COUNT * 2, 10)
    for _tab, board, rows in collected:
        ordered = sorted(rows,
                         key=lambda r: (r.get("date") or "", r["post_id"]),
                         reverse=True)
        store.save_board_posts(board["board_key"], [
            {"post_id": r["post_id"], "title": r["title"], "date": r.get("date", ""),
             "url": r["url"], "pinned": bool(r.get("pinned")),
             "board_name": board["name"]}
            for r in ordered[:keep]
        ])


def merge_list_titles(board_key, updates):
    """상세에서 알아낸 온전한 제목을 저장해 둔 목록에도 반영합니다."""
    if not updates:
        return
    posts = store.load_board_posts(board_key)
    changed = False
    for post in posts:
        better = updates.get(post["post_id"])
        if better and better != post["title"]:
            post["title"] = better
            changed = True
    if changed:
        store.save_board_posts(board_key, posts)


# ---------------------------------------------------------------------------
# 4) 본문 수집 + 일정 추출 대기열
# ---------------------------------------------------------------------------
def fetch_body_and_queue(tab, board, row, attempts, dry_run=False):
    """글 하나의 본문을 받아 Claude 대기열에 넣습니다.

    본문이 거의 비어 있으면 Claude 를 부르지 않습니다. LEET 공지가 대부분
    그런데, 진짜 내용이 첨부된 .hwp 파일 안에 있어서 파이썬 표준 라이브러리로는
    열 방법이 없습니다. 없는 능력을 있는 척하지 않고, '직접 확인하세요'라고
    알려주는 편이 정직합니다.

    돌려주는 값: True 면 Claude 가 읽을 거리가 생겼다는 뜻
    """
    board_key = board["board_key"]
    post_id = row["post_id"]
    key = store.make_key(board_key, post_id)

    try:
        detail = P.fetch_detail(board, post_id)
    except Exception as exc:
        attempts[key] = attempts.get(key, 0) + 1
        print(f"    [본문 실패] {row['title'][:30]}: {type(exc).__name__}")
        if attempts[key] >= cfg.MAX_EXTRACT_ATTEMPTS and not dry_run:
            store.save_extracted(board_key, post_id, {
                "post_id": key, "board": board["name"], "tab": tab["key"],
                "title": row["title"], "url": row["url"],
                "posted_date": row.get("date", ""),
                "extracted_at": store.now_iso(), "revision": 0,
                "needs_manual": True,
                "no_events_reason": f"본문을 {attempts[key]}번 시도했지만 받지 못했습니다",
                "events": [],
            })
            print("      -> 여러 번 실패해서 '일정 없음'으로 확정합니다")
        return False

    # 목록의 제목은 '...' 으로 잘려 있습니다. 상세의 온전한 제목으로 바꿉니다.
    if detail["title"] and len(detail["title"]) >= len(row["title"]) - 3:
        row["title"] = detail["title"]
    row["attachments"] = detail["attachments"]

    if len(detail["body"]) < cfg.BODY_MIN_CHARS:
        attach = ", ".join(detail["attachments"]) or "없음"
        if not dry_run:
            store.save_extracted(board_key, post_id, {
                "post_id": key, "board": board["name"], "tab": tab["key"],
                "title": row["title"], "url": row["url"],
                "posted_date": row.get("date", ""),
                "extracted_at": store.now_iso(), "revision": 0,
                "needs_manual": True,
                "no_events_reason": f"본문이 비어 있고 내용이 첨부파일에 있습니다 (첨부: {attach})",
                "events": [],
            })
        row["needs_manual"] = True
        print(f"    [첨부뿐] {row['title'][:36]} -> 자동 추출 불가")
        return False

    if not dry_run:
        store.write_pending(board_key, post_id,
                            render_pending(tab, board, row, detail))
    attempts[key] = attempts.get(key, 0) + 1
    print(f"    [본문] {row['title'][:36]} ({len(detail['body'])}자)")
    return True


def render_pending(tab, board, row, detail):
    """Claude 가 읽을 파일 한 장. 필요한 정보를 전부 머리말에 적어 둡니다."""
    board_key = board["board_key"]
    post_id = row["post_id"]
    return "\n".join([
        f"글번호: {store.make_key(board_key, post_id)}",
        f"게시판: {board['name']}",
        f"탭: {tab['key']}",
        f"제목: {row['title']}",
        f"주소: {row['url']}",
        f"게시일: {row.get('date', '')}",
        f"첨부: {', '.join(detail['attachments']) or '없음'}",
        f"저장할파일: state/extracted/"
        f"{store.safe_name(board_key)}_{store.safe_name(post_id)}.json",
        "",
        "---- 본문 시작 ----",
        detail["body"],
        "---- 본문 끝 ----",
        "",
    ])


def backlog_rows(collected, already):
    """이미 본 글인데 아직 일정을 뽑지 않은 것들.

    work/pending 은 저장소에 올리지 않으므로 다음 실행에는 남아 있지 않습니다.
    그래서 '뽑아낸 일정 파일이 있는가'를 기준으로 남은 일감을 다시 찾습니다.
    """
    attempts = store.load_attempts()
    out = []
    for tab, board, rows in collected:
        for row in rows:
            key = store.make_key(board["board_key"], row["post_id"])
            if key in already:
                continue
            if store.has_extracted(board["board_key"], row["post_id"]):
                continue
            if attempts.get(key, 0) >= cfg.MAX_EXTRACT_ATTEMPTS:
                continue
            out.append((tab, board, row))
    return out


# ---------------------------------------------------------------------------
# 5) 알림 문구
# ---------------------------------------------------------------------------
def build_new_message(fresh_by_board):
    total = sum(len(rows) for _t, _b, rows in fresh_by_board)
    lines = [f"## 🆕 새 공지 {total}건", ""]
    index = 0
    for tab, board, rows in fresh_by_board:
        for row in rows:
            index += 1
            lines.append(f"{index}. {row['title']}")
            date = row.get("date") or "날짜 미상"
            lines.append(f"   {tab['emoji']} {tab['label']} · {board['name']} · {date}")
            if row.get("needs_manual"):
                lines.append("   ⚠️ 본문이 첨부파일에 있어 일정을 자동으로 넣지 못했습니다."
                             " 링크에서 직접 확인해 주세요.")
            lines.append(f"   🔗 [원문 보기]({row['url']})")
            lines.append("")
    return "\n".join(lines).rstrip()


def build_setup_message(seeded):
    lines = ["## 📚 게시판 감시를 시작합니다", ""]
    for tab, board, count in seeded:
        lines.append(f"- {tab['emoji']} {tab['label']} · {board['name']} "
                     f"— 기존 {count}건은 알리지 않습니다")
    lines.append("")
    lines.append("지금부터 올라오는 글만 알려 드립니다.")
    return "\n".join(lines)


def build_trouble_message(flooded, alert_failures):
    lines = ["## ⚠️ 확인이 필요합니다", ""]
    for tab, board, fresh, total in flooded:
        lines.append(f"- {board['name']}: {total}건 중 {fresh}건이 처음 보는 글입니다. "
                     f"게시판 구조가 바뀐 것 같아 알림을 건너뛰었습니다.")
    for tab, board, reason, streak in alert_failures:
        lines.append(f"- {board['name']}: {streak}회 연속 수집 실패 — {reason}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 전체 흐름
# ---------------------------------------------------------------------------
def run(dry_run=False, seed_only=False, board_keys=None):
    print("게시판을 확인합니다...")
    collected, failed = collect(board_keys)

    if not collected:
        print("\n어느 게시판에서도 글을 가져오지 못했습니다. 네트워크나 사이트 상태를 확인하세요.")
        return 1

    seen = store.load_seen()

    if seed_only:
        for _tab, board, rows in collected:
            entry = store.board_state(seen, board["board_key"])
            store.remember_ids(entry, [r["post_id"] for r in rows], cfg.SEEN_KEEP)
            entry["seeded"] = True
            entry["fail_streak"] = 0
            print(f"  [기억] {board['name']}: {len(rows)}건")
        save_lists(collected)
        store.save_seen(seen)
        print("\n지금 올라와 있는 글을 전부 '이미 본 것'으로 기록했습니다. 알림은 보내지 않았습니다.")
        return 0

    fresh_by_board, seeded, flooded = detect_new(seen, collected)
    if not dry_run:
        save_lists(collected)

    # 연속 실패를 세어 두었다가, 계속 실패하면 알려 줍니다.
    alert_failures = []
    for tab, board, reason in failed:
        entry = store.board_state(seen, board["board_key"])
        entry["fail_streak"] = entry.get("fail_streak", 0) + 1
        if entry["fail_streak"] == cfg.FAIL_ALERT_AFTER:
            alert_failures.append((tab, board, reason, entry["fail_streak"]))

    # --- 본문 수집 ---
    attempts = store.load_attempts()
    budget = cfg.MAX_BODY_FETCH
    handled = set()
    title_updates = {}

    for tab, board, rows in fresh_by_board:
        for row in rows:
            if budget <= 0:
                break
            budget -= 1
            handled.add(store.make_key(board["board_key"], row["post_id"]))
            fetch_body_and_queue(tab, board, row, attempts, dry_run)
            title_updates.setdefault(board["board_key"], {})[row["post_id"]] = \
                row["title"]

    # 새 글을 처리하고 남은 여유로, 지난번에 못 끝낸 것을 마저 합니다.
    if budget > 0:
        backlog = backlog_rows(collected, handled)
        if backlog:
            print(f"  지난번에 못 끝낸 일정 추출 {len(backlog)}건 중 "
                  f"{min(budget, len(backlog))}건을 처리합니다")
        for tab, board, row in backlog[:budget]:
            fetch_body_and_queue(tab, board, row, attempts, dry_run)
            # 목록에서 '...' 으로 잘려 있던 제목을 상세에서 알아낸 온전한 것으로
            # 바꿉니다. 새 글뿐 아니라 밀린 글에도 해 줘야, 처음 설치했을 때
            # 탭에 잘린 제목이 그대로 남지 않습니다.
            title_updates.setdefault(board["board_key"], {})[row["post_id"]] = \
                row["title"]

    if not dry_run:
        store.save_attempts(attempts)
    for board_key, updates in title_updates.items():
        if not dry_run:
            merge_list_titles(board_key, updates)

    # --- 알림 ---
    messages = []
    if seeded:
        messages.append(build_setup_message(seeded))
    if fresh_by_board:
        messages.append(build_new_message(fresh_by_board))
    if flooded or alert_failures:
        messages.append(build_trouble_message(flooded, alert_failures))

    if not messages:
        print("\n새 글이 없습니다.")
    if dry_run:
        for msg in messages:
            print("\n" + "-" * 60)
            print(msg)
        print("\n(미리보기만 했습니다. 아무것도 보내지 않았고 기억도 저장하지 않았습니다.)")
        return 0

    sent_ok = True
    if messages:
        token, chat_id = tg.load_config()
        for msg in messages:
            if not tg.send_markdown(token, chat_id, msg):
                sent_ok = False

    # --- 기억 저장 ---
    # 알림이 실패했다면 새 글 번호를 기억하지 않습니다. 그래야 다음 회차에
    # 다시 시도합니다. 이미 처리한 seed/flood 게시판은 그대로 저장합니다.
    if sent_ok:
        for _tab, board, rows in collected:
            entry = store.board_state(seen, board["board_key"])
            store.remember_ids(entry, [r["post_id"] for r in rows], cfg.SEEN_KEEP)
    else:
        print("\n알림 발송에 실패했습니다. 새 글 기록을 남기지 않아 다음 실행에서 다시 시도합니다.")

    store.save_seen(seen)
    store.touch_heartbeat()

    pending = store.pending_count()
    print(f"\n새 글 {sum(len(r) for _t, _b, r in fresh_by_board)}건, "
          f"일정 추출 대기 {pending}건")
    return 0 if sent_ok else 1


def main():
    parser = argparse.ArgumentParser(description="로스쿨 공지 수집기")
    parser.add_argument("--dry-run", action="store_true",
                        help="보내지도 저장하지도 않고 결과만 확인")
    parser.add_argument("--seed-only", action="store_true",
                        help="지금 글을 전부 '이미 본 것'으로 기록 (알림 없음)")
    parser.add_argument("--board", action="append", dest="boards",
                        help="게시판 하나만 (예: --board sg_1601)")
    args = parser.parse_args()

    try:
        return run(dry_run=args.dry_run, seed_only=args.seed_only,
                   board_keys=args.boards)
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
