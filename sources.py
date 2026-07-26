# -*- coding: utf-8 -*-
"""어떤 게시판을 볼지 정하는 설정 파일.

프로그램 로직은 여기에 없습니다. 감시 대상을 바꾸고 싶으면
이 파일만 고치면 됩니다.

탭을 하나 더 만들고 싶다면 TABS 에 항목을 하나 추가하세요.
  - 서강대와 같은 CMS를 쓰는 학교라면  parser: "sogang_cms" 로 두고
    config_fk 숫자만 바꾸면 끝입니다.
  - 완전히 다른 형태의 게시판이라면 parsers.py 에 파서를 하나 더
    만들어야 합니다. (그건 좀 손이 갑니다)

새로 추가한 게시판은 첫 실행 때 '기존 글 전부'를 조용히 기록만 하고
알림을 보내지 않습니다. 그래야 갑자기 수십 개의 알림이 쏟아지지 않습니다.
"""

TABS = [
    {
        "key": "leet",
        "label": "LEET 일반사항",
        "emoji": "📝",
        "boards": [
            {
                "board_key": "leet_82",
                "name": "법학적성시험 공지사항",
                "parser": "uway",
                "board_id": 82,
            },
        ],
    },
    {
        "key": "sogang",
        "label": "서강대학교 로스쿨",
        "emoji": "🎓",
        "boards": [
            {"board_key": "sg_1601", "name": "학사공지",
             "parser": "sogang_cms", "config_fk": 1601},
            {"board_key": "sg_1602", "name": "일반공지",
             "parser": "sogang_cms", "config_fk": 1602},
            {"board_key": "sg_1603", "name": "법전원 소식",
             "parser": "sogang_cms", "config_fk": 1603},
            {"board_key": "sg_2706", "name": "실무수습",
             "parser": "sogang_cms", "config_fk": 2706},
        ],
    },
]


# ---------------------------------------------------------------------------
# 동작 조절값
# ---------------------------------------------------------------------------

# 탭을 눌렀을 때 보여줄 글 개수
LIST_COUNT = 5

# Claude 에게 넘길 본문 길이 상한. 너무 길면 읽는 데 오래 걸립니다.
BODY_MAX_CHARS = 6000

# 본문이 이 길이보다 짧으면 "내용이 첨부파일에 있다"고 보고 일정 추출을 포기합니다.
# LEET 공지가 대부분 여기 해당합니다 (실제 내용이 .hwp 파일 안에 있음).
BODY_MIN_CHARS = 80

# 마감 며칠 전에 알릴지. 0은 당일입니다.
REMINDER_OFFSETS = [3, 1, 0]

# 게시판 하나당 기억해 둘 글 번호 개수. 넘으면 오래된 것부터 버립니다.
SEEN_KEEP = 300

# 한 번 실행에서 본문을 읽어올 최대 글 수.
# 학교 서버에 부담을 주지 않고, 워크플로가 시간 초과로 죽지 않게 합니다.
MAX_BODY_FETCH = 10

# 일정 추출에 이 횟수만큼 실패하면 포기하고 '일정 없음'으로 확정합니다.
MAX_EXTRACT_ATTEMPTS = 5

# 이미 감시 중인 게시판에서 신규 글이 전체의 이 비율을 넘으면,
# 새 글이 쏟아진 게 아니라 게시판 구조가 바뀐 것으로 봅니다.
FLOOD_RATIO = 0.7

# 게시판이 이 횟수만큼 연속 실패하면 텔레그램으로 알립니다.
FAIL_ALERT_AFTER = 3

# 요청 사이 간격(초). 학교 서버에 예의를 지키는 값입니다.
REQUEST_DELAY = 1.5

# 캘린더에서 이 일수보다 오래된 지난 일정은 뺍니다.
ICS_PAST_DAYS = 90

CALENDAR_NAME = "로스쿨 일정"
CALENDAR_DESC = "서강대 로스쿨·LEET 공지에서 자동으로 뽑아낸 일정"


# ---------------------------------------------------------------------------
# 편의 함수
# ---------------------------------------------------------------------------
def all_boards():
    """모든 게시판을 (탭, 게시판) 짝으로 돌려줍니다."""
    for tab in TABS:
        for board in tab["boards"]:
            yield tab, board


def find_board(board_key):
    """게시판 키로 (탭, 게시판) 을 찾습니다. 없으면 (None, None)."""
    for tab, board in all_boards():
        if board["board_key"] == board_key:
            return tab, board
    return None, None


def find_tab(tab_key):
    for tab in TABS:
        if tab["key"] == tab_key:
            return tab
    return None
