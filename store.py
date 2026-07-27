# -*- coding: utf-8 -*-
"""프로그램이 기억해야 하는 것들을 파일로 읽고 씁니다.

데이터베이스를 쓰지 않는 이유: 설치할 것이 없고, 내용이 그냥 눈에 보이고,
GitHub 에 그대로 올려서 클라우드와 내 PC가 같은 기억을 공유할 수 있습니다.

무엇을 어디에 두는가
--------------------
  state/seen.json            이미 본 글 번호. 신규 판별의 근거 (저장소에 올림)
  state/posts/<게시판>.json   탭 버튼이 보여줄 최신 목록      (저장소에 올림)
  state/extracted/<글>.json   Claude 가 뽑아낸 일정            (저장소에 올림)
  state/reminded.json        이미 보낸 마감 알림 기록          (저장소에 올림)
  state/heartbeat.txt        하루 한 번 찍는 생존 신호        (저장소에 올림)
  work/pending/<글>.txt      Claude 에게 넘길 본문     (임시, 올리지 않음)
  work/bot_offset.txt        텔레그램 수신 위치        (임시, 올리지 않음)

state/ 는 반드시 저장소에 올려야 합니다. 여기가 비면 프로그램이 모든 글을
'새 글'로 착각해서 알림을 수십 개 쏟아냅니다.
"""

import json
import os
import re
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_DIR = os.path.join(BASE_DIR, "state")
POSTS_DIR = os.path.join(STATE_DIR, "posts")
EXTRACTED_DIR = os.path.join(STATE_DIR, "extracted")
WORK_DIR = os.path.join(BASE_DIR, "work")
PENDING_DIR = os.path.join(WORK_DIR, "pending")
DOCS_DIR = os.path.join(BASE_DIR, "docs")

SEEN_PATH = os.path.join(STATE_DIR, "seen.json")
ATTEMPTS_PATH = os.path.join(STATE_DIR, "extract_attempts.json")
REMINDED_PATH = os.path.join(STATE_DIR, "reminded.json")
DONE_PATH = os.path.join(STATE_DIR, "done_today.json")
HEARTBEAT_PATH = os.path.join(STATE_DIR, "heartbeat.txt")
OFFSET_PATH = os.path.join(WORK_DIR, "bot_offset.txt")
ICS_PATH = os.path.join(DOCS_DIR, "law.ics")

SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9_.-]")


# ---------------------------------------------------------------------------
# 시간
# ---------------------------------------------------------------------------
def now_kst():
    return datetime.now(KST)


def today_str():
    return now_kst().strftime("%Y-%m-%d")


def now_iso():
    return now_kst().replace(microsecond=0).isoformat()


# ---------------------------------------------------------------------------
# 기본 읽기·쓰기
# ---------------------------------------------------------------------------
def read_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as fp:
            return json.load(fp)
    except (OSError, json.JSONDecodeError):
        return default


def write_json(path, data):
    """저장 중에 프로그램이 죽어도 파일이 반토막 나지 않도록 임시파일을 거칩니다."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fp:
        json.dump(data, fp, ensure_ascii=False, indent=2, sort_keys=True)
        fp.write("\n")
    os.replace(tmp, path)


def safe_name(text):
    return SAFE_NAME_RE.sub("_", str(text))[:80]


# ---------------------------------------------------------------------------
# 이미 본 글
# ---------------------------------------------------------------------------
def load_seen():
    return read_json(SEEN_PATH, {})


def save_seen(seen):
    write_json(SEEN_PATH, seen)


def board_state(seen, board_key):
    """게시판 하나의 기억. 없으면 '아직 한 번도 안 본' 상태로 만들어 줍니다."""
    entry = seen.get(board_key)
    if not isinstance(entry, dict):
        entry = {}
    entry.setdefault("seeded", False)
    entry.setdefault("ids", [])
    entry.setdefault("fail_streak", 0)
    seen[board_key] = entry
    return entry


def remember_ids(entry, ids, keep):
    """새 글 번호를 앞에 붙이고, 오래된 것부터 잘라냅니다."""
    merged = list(dict.fromkeys(list(ids) + list(entry["ids"])))
    entry["ids"] = merged[:keep]


# ---------------------------------------------------------------------------
# 탭에 보여줄 목록
# ---------------------------------------------------------------------------
def posts_path(board_key):
    return os.path.join(POSTS_DIR, f"{safe_name(board_key)}.json")


def load_board_posts(board_key):
    data = read_json(posts_path(board_key), {})
    return data.get("posts", [])


def save_board_posts(board_key, posts):
    write_json(posts_path(board_key), {"updated": now_iso(), "posts": posts})


# ---------------------------------------------------------------------------
# 뽑아낸 일정
# ---------------------------------------------------------------------------
def make_key(board_key, post_id):
    """글 하나를 가리키는 이름. 게시판이 달라도 겹치지 않습니다."""
    return f"{board_key}:{post_id}"


def extracted_path(board_key, post_id):
    return os.path.join(EXTRACTED_DIR,
                        f"{safe_name(board_key)}_{safe_name(post_id)}.json")


def has_extracted(board_key, post_id):
    return os.path.exists(extracted_path(board_key, post_id))


def load_extracted(board_key, post_id):
    return read_json(extracted_path(board_key, post_id), None)


def save_extracted(board_key, post_id, data):
    write_json(extracted_path(board_key, post_id), data)


def load_attempts():
    """일정 추출을 몇 번이나 시도했는지. 영영 재시도하는 것을 막습니다."""
    data = read_json(ATTEMPTS_PATH, {})
    return data if isinstance(data, dict) else {}


def save_attempts(data):
    write_json(ATTEMPTS_PATH, data)


def all_extracted():
    """뽑아낸 일정 파일을 이름순으로 전부 읽습니다.

    이름순으로 읽는 이유: 캘린더 파일의 내용이 매번 똑같은 순서로 나와야
    '바뀐 게 없으면 커밋도 없다'가 성립합니다.
    """
    if not os.path.isdir(EXTRACTED_DIR):
        return []
    items = []
    for name in sorted(os.listdir(EXTRACTED_DIR)):
        if not name.endswith(".json"):
            continue
        data = read_json(os.path.join(EXTRACTED_DIR, name), None)
        if isinstance(data, dict):
            items.append((name, data))
    return items


# ---------------------------------------------------------------------------
# Claude 에게 넘길 본문
# ---------------------------------------------------------------------------
def pending_path(board_key, post_id):
    return os.path.join(PENDING_DIR,
                        f"{safe_name(board_key)}_{safe_name(post_id)}.txt")


def write_pending(board_key, post_id, text):
    os.makedirs(PENDING_DIR, exist_ok=True)
    with open(pending_path(board_key, post_id), "w", encoding="utf-8") as fp:
        fp.write(text)


def pending_count():
    if not os.path.isdir(PENDING_DIR):
        return 0
    return sum(1 for n in os.listdir(PENDING_DIR) if n.endswith(".txt"))


# ---------------------------------------------------------------------------
# 마감 알림 기록
# ---------------------------------------------------------------------------
def load_reminded():
    data = read_json(REMINDED_PATH, {})
    return data if isinstance(data, dict) else {}


def save_reminded(data):
    write_json(REMINDED_PATH, data)


# ---------------------------------------------------------------------------
# 하루 한 번만 하기
# ---------------------------------------------------------------------------
def done_today(name):
    """오늘 이미 했는지 묻습니다.

    GitHub 의 예약 실행은 자주 건너뜁니다. 그래서 아침 브리핑은 한 시각이
    아니라 여러 시각에 걸어 두는데, 그러면 세 번 다 도는 날에 같은 메시지가
    세 번 갑니다. 이 표시로 첫 번째만 실제로 보냅니다.
    """
    data = read_json(DONE_PATH, {})
    return isinstance(data, dict) and data.get(name) == today_str()


def mark_done(name):
    data = read_json(DONE_PATH, {})
    if not isinstance(data, dict):
        data = {}
    data[name] = today_str()
    write_json(DONE_PATH, data)


# ---------------------------------------------------------------------------
# 생존 신호
# ---------------------------------------------------------------------------
def touch_heartbeat():
    """하루에 한 번만 파일을 바꿉니다.

    왜 필요한가: GitHub 은 저장소에 60일간 아무 변화가 없으면 예약 실행을
    꺼버립니다. 이 프로그램은 새 공지가 없으면 아무것도 커밋하지 않도록
    일부러 만들었기 때문에, 방학이 길어지면 조용히 죽습니다.
    하루 한 줄이면 그걸 막을 수 있습니다.

    돌려주는 값: 파일이 바뀌었으면 True
    """
    today = today_str()
    try:
        with open(HEARTBEAT_PATH, "r", encoding="utf-8") as fp:
            if fp.read().strip() == today:
                return False
    except OSError:
        pass
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(HEARTBEAT_PATH, "w", encoding="utf-8") as fp:
        fp.write(today + "\n")
    return True
