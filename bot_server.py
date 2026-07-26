# -*- coding: utf-8 -*-
"""탭 버튼을 보내고, 누르면 최신 공지를 돌려줍니다.

사용법:
    python bot_server.py --send-menu   # 탭 버튼 메뉴를 보냅니다
    python bot_server.py               # 버튼 클릭을 계속 기다립니다 (내 PC용)
    python bot_server.py --once        # 밀린 클릭만 처리하고 끝냅니다 (클라우드용)

버튼을 누르면 왜 바로 답이 안 오나요
------------------------------------
텔레그램은 "누가 버튼을 눌렀다"는 사실을 봇이 직접 물어보러 올 때만 알려줍니다.
개인 PC에는 인터넷에서 접근할 수 있는 주소가 없어서, 이쪽에서 주기적으로
물어보는 방식을 씁니다. GitHub Actions 가 10분마다 물어보므로 최대 10분쯤
걸릴 수 있습니다.

누른 버튼이 사라지지는 않습니다. 텔레그램이 24시간 보관해 줍니다.

주의: 이 봇은 daily brief 의 경제뉴스 봇과 반드시 '다른 봇'이어야 합니다.
텔레그램은 봇 하나에 물어보러 올 수 있는 프로그램을 1개로 제한합니다.
같은 토큰을 쓰면 두 봇이 서로의 클릭을 가로채서 조용히 사라집니다.
"""

import argparse
import html as html_mod
import os
import sys
import time

import sources as cfg
import store
import telegram_api as tg

if sys.stdout is None:                      # pythonw.exe 로 실행한 경우
    import io
    sys.stdout = io.StringIO()
if sys.stderr is None:
    import io
    sys.stderr = io.StringIO()

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
except Exception:
    pass

WEEKDAYS = ["월", "화", "수", "목", "금", "토", "일"]


# ---------------------------------------------------------------------------
# 탭 내용 만들기
# ---------------------------------------------------------------------------
def tab_posts(tab, limit=None):
    """탭에 속한 게시판들의 글을 모아 최신순으로 돌려줍니다."""
    limit = limit or cfg.LIST_COUNT
    merged = []
    for board in tab["boards"]:
        merged.extend(store.load_board_posts(board["board_key"]))
    merged.sort(key=lambda p: (p.get("date") or "", p.get("post_id") or ""),
                reverse=True)
    return merged[:limit]


def render_tab(tab):
    """탭 하나를 텔레그램 HTML 로 그립니다.

    여기서는 마크다운 변환기를 거치지 않고 HTML 을 직접 조립하므로
    제목을 반드시 html.escape 해야 합니다. 공지 제목에 < 나 & 가 실제로
    들어옵니다. (예: "2026년 2학기 <리걸클리닉3(법원)-2학점> 과목 수강신청 안내")
    빠뜨리면 텔레그램이 메시지 전체를 거부합니다.
    """
    posts = tab_posts(tab)
    head = f"<b>{html_mod.escape(tab['emoji'] + ' ' + tab['label'])}</b>"

    if not posts:
        return (f"{head}\n\n아직 저장된 목록이 없습니다.\n"
                f"<code>python scrape.py</code> 를 한 번 실행하거나, "
                f"GitHub 의 Actions 탭에서 '공지 수집'을 실행해 주세요.")

    lines = [f"{head} — 최신 {len(posts)}건", ""]
    for index, post in enumerate(posts, 1):
        title = html_mod.escape(post.get("title") or "(제목 없음)")
        board = html_mod.escape(post.get("board_name") or "")
        date = post.get("date") or "날짜 미상"
        pin = "📌 " if post.get("pinned") else ""
        url = html_mod.escape(post.get("url") or "", quote=True)
        lines.append(f"{index}. {pin}{title}")
        lines.append(f"    <i>{board} · {date}</i> · <a href=\"{url}\">🔗 열기</a>")
        lines.append("")
    return "\n".join(lines).rstrip()


# ---------------------------------------------------------------------------
# 버튼
# ---------------------------------------------------------------------------
def build_keyboard():
    """탭 버튼을 만듭니다.

    버튼에 날짜를 넣지 않습니다. 공지 목록은 '언제나 최신'이 맞으므로,
    지난주에 받은 메뉴를 눌러도 오늘의 최신 5건이 옵니다.
    """
    buttons = [{"text": f"{tab['emoji']} {tab['label']}",
                "callback_data": f"t|{tab['key']}"} for tab in cfg.TABS]
    rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    rows.append([{"text": "📅 다가오는 일정", "callback_data": "t|__upcoming__"}])
    return {"inline_keyboard": rows}


def menu_text():
    now = store.now_kst()
    return (f"<b>⚖️ 로스쿨 비서</b>\n"
            f"<i>{now.year}년 {now.month}월 {now.day}일 "
            f"({WEEKDAYS[now.weekday()]}) 기준</i>\n\n"
            f"보고 싶은 것을 눌러 주세요.")


def send_menu():
    token, chat_id = tg.load_config()
    ok, err = tg.send_message(token, chat_id, menu_text(),
                              reply_markup=build_keyboard())
    if not ok:
        print(f"메뉴 발송 실패: {err}")
        return 1
    print(f"메뉴를 보냈습니다. 탭 {len(cfg.TABS)}개: "
          f"{', '.join(t['label'] for t in cfg.TABS)}")
    return 0


# ---------------------------------------------------------------------------
# 클릭 처리
# ---------------------------------------------------------------------------
def answer_callback(token, callback_id, text=""):
    """버튼의 '빙글빙글 도는 표시'를 멈춥니다. 10초 안에 응답해야 합니다."""
    tg.api_request(token, "answerCallbackQuery",
                   {"callback_query_id": callback_id, "text": text[:200]})


def upcoming_text():
    """다가오는 일정을 보여줍니다. remind.py 와 같은 자료를 씁니다."""
    import remind
    items = remind.upcoming(days=30)
    if not items:
        return ("<b>📅 다가오는 일정</b>\n\n"
                "앞으로 30일 안에 잡힌 일정이 없습니다.")
    lines = ["<b>📅 다가오는 일정</b> — 앞으로 30일", ""]
    for item in items:
        lines.append(remind.format_line(item, as_html=True))
    return "\n".join(lines)


def handle_callback(token, callback):
    data = callback.get("data") or ""
    callback_id = callback.get("id")
    chat_id = (((callback.get("message") or {}).get("chat")) or {}).get("id")

    parts = data.split("|")
    if len(parts) != 2 or parts[0] != "t":
        answer_callback(token, callback_id, "알 수 없는 버튼입니다.")
        return

    key = parts[1]

    if key == "__upcoming__":
        answer_callback(token, callback_id, "다가오는 일정을 보냅니다...")
        print("  [클릭] 다가오는 일정")
        tg.send_message(token, str(chat_id), upcoming_text())
        return

    tab = cfg.find_tab(key)
    if not tab:
        answer_callback(token, callback_id, "없어진 탭입니다.")
        tg.send_message(token, str(chat_id),
                        "그 탭은 더 이상 없습니다. /menu 로 새 메뉴를 받아 주세요.")
        return

    answer_callback(token, callback_id, f"{tab['label']} 최신 글을 보냅니다...")
    print(f"  [클릭] {tab['label']}")
    tg.send_message(token, str(chat_id), render_tab(tab))


def handle_message(token, message):
    """문자 명령도 받아 줍니다."""
    text = (message.get("text") or "").strip().lower()
    chat_id = ((message.get("chat")) or {}).get("id")
    if not chat_id:
        return

    if text.startswith(("/start", "/menu")) or text in ("메뉴", "공지"):
        tg.send_message(token, str(chat_id), menu_text(),
                        reply_markup=build_keyboard())
        print(f"  [명령] {text or '(빈 메시지)'} -> 메뉴 전송")
    elif text.startswith("/upcoming") or text in ("일정", "마감"):
        tg.send_message(token, str(chat_id), upcoming_text())
        print("  [명령] 다가오는 일정")
    elif text.startswith("/help"):
        tg.send_message(token, str(chat_id),
                        "<b>사용법</b>\n\n"
                        "<code>/menu</code> — 탭 버튼 보기\n"
                        "<code>/upcoming</code> — 다가오는 마감 일정\n\n"
                        "새 공지가 올라오면 누르지 않아도 먼저 알려 드립니다.")


# ---------------------------------------------------------------------------
# 폴링
# ---------------------------------------------------------------------------
def load_offset():
    try:
        with open(store.OFFSET_PATH, "r", encoding="utf-8") as fp:
            return int(fp.read().strip())
    except (OSError, ValueError):
        return None


def save_offset(offset):
    os.makedirs(os.path.dirname(store.OFFSET_PATH), exist_ok=True)
    with open(store.OFFSET_PATH, "w", encoding="utf-8") as fp:
        fp.write(str(offset))


def process_updates(token, updates):
    for update in updates:
        try:
            if "callback_query" in update:
                handle_callback(token, update["callback_query"])
            elif "message" in update:
                handle_message(token, update["message"])
        except Exception as exc:      # 한 건이 실패해도 나머지는 처리해야 합니다
            print(f"  [오류] 업데이트 처리 실패: {type(exc).__name__}: {exc}")


def poll(once=False):
    token, _chat_id = tg.load_config()
    offset = load_offset()

    print("밀린 버튼 클릭을 확인합니다..." if once
          else "버튼 클릭을 기다립니다. 멈추려면 Ctrl+C 를 누르세요.\n")

    while True:
        params = {
            "timeout": 0 if once else 50,
            "allowed_updates": '["message","callback_query"]',
        }
        if offset is not None:
            params["offset"] = offset

        result = tg.api_request(token, "getUpdates", params,
                                timeout=(20 if once else 70))

        if not result.get("ok"):
            desc = result.get("description", "알 수 없는 오류")
            print(f"  [오류] 업데이트 수신 실패: {desc}")
            if once:
                return 1
            time.sleep(10)
            continue

        updates = result.get("result", [])
        if updates:
            process_updates(token, updates)
            offset = updates[-1]["update_id"] + 1
            save_offset(offset)
        elif once:
            print("밀린 클릭이 없습니다.")
            return 0

        if once:
            return 0


def main():
    parser = argparse.ArgumentParser(description="로스쿨 비서 버튼 봇")
    parser.add_argument("--send-menu", action="store_true", help="탭 버튼 메뉴 발송")
    parser.add_argument("--once", action="store_true",
                        help="밀린 클릭만 처리하고 종료")
    args = parser.parse_args()

    if args.send_menu:
        return send_menu()

    try:
        return poll(once=args.once)
    except KeyboardInterrupt:
        print("\n중지했습니다.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
