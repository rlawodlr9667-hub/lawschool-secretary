# -*- coding: utf-8 -*-
"""마감이 다가온 일정을 텔레그램으로 알려 줍니다.

사용법:
    python remind.py             # 오늘 알려야 할 것이 있으면 보냅니다
    python remind.py --dry-run   # 보내지 않고 화면으로만 확인
    python remind.py --list      # 앞으로 30일치 일정 보기

왜 캘린더 알림만으로는 부족한가
--------------------------------
아이폰에서 캘린더를 '구독'하면, 설정에 '알림 제거(Remove Alarms)'라는
스위치가 있습니다. 이게 켜져 있으면 캘린더 파일에 넣어 둔 알림이 통째로
지워집니다. 새로고침 주기도 사용자 설정에 달려 있어서, 캘린더 알림만
믿고 있다가는 마감 당일에 아무 소리도 안 날 수 있습니다.

그래서 마감 알림의 '진짜' 통로는 텔레그램입니다. 캘린더는 눈으로 보는
용도이고, 캘린더 안의 알림은 보조 수단입니다. 둘은 고장 나는 방식이
완전히 달라서, 하나가 조용히 죽어도 다른 하나가 남습니다.
"""

import argparse
import html as html_mod
import sys
from datetime import timedelta

import build_ics
import sources as cfg
import store
import telegram_api as tg

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

WEEKDAYS = ["월", "화", "수", "목", "금", "토", "일"]

# 기록을 영원히 쌓아 두지 않습니다.
REMINDED_KEEP_DAYS = 180


def collect_events():
    """모든 일정을 (날짜 오름차순으로) 모읍니다."""
    items = []
    for _name, record, events in build_ics.load_records()[0]:
        for event in events:
            items.append({
                "uid": event["uid"] or build_ics.make_uid(
                    record.get("post_id", ""), event["slug"]),
                "summary": event["summary"],
                "start": event["start"],
                "start_time": event["start_time"],
                "all_day": event["all_day"],
                "board": record.get("board") or "",
                "url": record.get("url") or "",
            })
    items.sort(key=lambda i: (i["start"], i["start_time"] or "", i["summary"]))
    return items


def upcoming(days=30, today=None):
    """앞으로 며칠 안에 있는 일정."""
    today = today or store.now_kst().date()
    limit = today + timedelta(days=days)
    out = []
    for item in collect_events():
        if today <= item["start"] <= limit:
            item = dict(item)
            item["dday"] = (item["start"] - today).days
            out.append(item)
    return out


def due_today(today=None):
    """오늘 알림을 보내야 하는 일정. (일정, 며칠 전인지) 목록."""
    today = today or store.now_kst().date()
    out = []
    for item in collect_events():
        gap = (item["start"] - today).days
        if gap in cfg.REMINDER_OFFSETS:
            entry = dict(item)
            entry["dday"] = gap
            out.append(entry)
    return out


def format_line(item, as_html=True):
    """'• D-1 8/7(금) 17:00 장학금 신청 마감 — 학사공지 🔗' 한 줄."""
    start = item["start"]
    when = f"{start.month}/{start.day}({WEEKDAYS[start.weekday()]})"
    if not item["all_day"] and item["start_time"]:
        when += f" {item['start_time']}"

    dday = item.get("dday")
    tag = "오늘" if dday == 0 else f"D-{dday}"

    if not as_html:
        line = f"- [{tag}] {when} {item['summary']}"
        if item["board"]:
            line += f" — {item['board']}"
        return line

    summary = html_mod.escape(item["summary"])
    board = html_mod.escape(item["board"])
    line = f"• <b>{tag}</b> {when} — {summary}"
    if board:
        line += f"\n   <i>{board}</i>"
    if item["url"]:
        url = html_mod.escape(item["url"], quote=True)
        line += f" · <a href=\"{url}\">🔗 원문</a>"
    return line


def prune(reminded, today):
    """오래된 발송 기록을 지웁니다."""
    cutoff = (today - timedelta(days=REMINDED_KEEP_DAYS)).isoformat()
    return {key: value for key, value in reminded.items()
            if isinstance(value, str) and value >= cutoff}


def run(dry_run=False):
    today = store.now_kst().date()
    reminded = store.load_reminded()

    fresh = []
    for item in due_today(today):
        key = f"{item['uid']}:D{item['dday']}"
        if key in reminded:
            continue
        fresh.append((key, item))

    if not fresh:
        print("오늘 보낼 마감 알림이 없습니다.")
        return 0

    lines = [f"<b>⏰ 다가오는 마감 {len(fresh)}건</b>", ""]
    for _key, item in fresh:
        lines.append(format_line(item, as_html=True))
        lines.append("")
    text = "\n".join(lines).rstrip()

    if dry_run:
        print(text)
        print("\n(미리보기만 했습니다.)")
        return 0

    token, chat_id = tg.load_config()
    ok, err = tg.send_message(token, chat_id, text)
    if not ok:
        print(f"발송 실패: {err}")
        return 1

    for key, _item in fresh:
        reminded[key] = today.isoformat()
    store.save_reminded(prune(reminded, today))
    print(f"마감 알림 {len(fresh)}건을 보냈습니다.")
    return 0


def main():
    parser = argparse.ArgumentParser(description="마감 알림")
    parser.add_argument("--dry-run", action="store_true", help="보내지 않고 확인만")
    parser.add_argument("--list", action="store_true", help="앞으로 30일치 일정 보기")
    parser.add_argument("--days", type=int, default=30, help="--list 의 기간")
    args = parser.parse_args()

    if args.list:
        items = upcoming(args.days)
        if not items:
            print(f"앞으로 {args.days}일 안에 잡힌 일정이 없습니다.")
            return 0
        for item in items:
            print(format_line(item, as_html=False))
        print(f"\n총 {len(items)}건")
        return 0

    return run(dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
