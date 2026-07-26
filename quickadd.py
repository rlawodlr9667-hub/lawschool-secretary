# -*- coding: utf-8 -*-
"""Alt+Space 를 누르면 뜨는 한 줄 입력창. 적은 대로 아이폰 캘린더에 들어갑니다.

    python quickadd.py                    # 켜 두면 Alt+Space 로 불러냅니다
    python quickadd.py --once             # 단축키 없이 창만 한 번 띄우기(시험용)
    python quickadd.py --text "내일 3시 면담"   # 창 없이 바로 넣기
    python quickadd.py --install-startup  # 부팅할 때 자동으로 켜지게
    python quickadd.py --remove-startup   # 자동 실행 해제

    "8월 8일 결전의날, 시작 13시, 두시간 동안"

넣기 전에 반드시 보여주고 확인받습니다
------------------------------------
Enter 를 한 번 누르면 해석 결과가 뜨고, 한 번 더 눌러야 들어갑니다. 번거로워
보이지만 이게 핵심입니다. "두시간"을 2일로 잘못 읽어도 확인 화면에서 바로
보이기 때문에, 엉뚱한 일정이 조용히 캘린더에 박히는 일이 없습니다.

창이 뜨는 것이 먼저, 달력은 나중
--------------------------------
입력창 아래 3주짜리 달력에는 일정이 있는 날에 점이 찍힙니다. 그 점을 찍으려면
iCloud 에 물어봐야 하는데, 그걸 기다렸다가 창을 띄우면 Alt+Space 를 누르고
1초쯤 멍하니 기다리게 됩니다. 그래서 **창을 먼저 띄우고, 점은 도착하는 대로
칠합니다.** 못 가져와도 창은 그냥 씁니다.

설치할 것이 없습니다
-------------------
단축키는 윈도우 API(ctypes), 창은 tkinter — 둘 다 파이썬에 들어 있습니다.
이 프로젝트의 다른 부분과 같이, pip install 할 것이 없습니다.
"""

import argparse
import ctypes
import os
import queue
import sys
import threading
import tkinter as tk
import tkinter.font as tkfont
from ctypes import wintypes
from datetime import datetime, timedelta

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ---------------------------------------------------------------------------
# 윈도우 단축키
# ---------------------------------------------------------------------------
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000       # 누르고 있어도 한 번만
WM_HOTKEY = 0x0312

MOD_NAMES = {
    "alt": MOD_ALT, "ctrl": MOD_CONTROL, "control": MOD_CONTROL,
    "shift": MOD_SHIFT, "win": MOD_WIN,
}
VK_NAMES = {"space": 0x20, "enter": 0x0D, "tab": 0x09}

DEFAULT_HOTKEY = "alt+space"


def parse_hotkey(text):
    """'alt+space' -> (수식키, 키코드)."""
    mods = 0
    key = None
    for part in (text or DEFAULT_HOTKEY).lower().replace(" ", "").split("+"):
        if part in MOD_NAMES:
            mods |= MOD_NAMES[part]
        elif part in VK_NAMES:
            key = VK_NAMES[part]
        elif len(part) == 1:
            key = ord(part.upper())
    if key is None:
        raise ValueError(f"단축키를 알아듣지 못했습니다: {text!r}")
    return mods | MOD_NOREPEAT, key


def hotkey_loop(hotkey, on_press, on_error):
    """단축키를 등록하고 눌릴 때까지 기다립니다. 별도 스레드에서 돕니다.

    윈도우는 단축키 알림을 '등록한 스레드'의 메시지 대기줄로 보냅니다.
    그래서 등록과 대기가 같은 스레드에 있어야 합니다.
    """
    user32 = ctypes.windll.user32
    try:
        mods, key = parse_hotkey(hotkey)
    except ValueError as exc:
        on_error(str(exc))
        return

    if not user32.RegisterHotKey(None, 1, mods, key):
        on_error(
            f"{hotkey} 를 잡지 못했습니다. 다른 프로그램이 이미 쓰고 있습니다.\n"
            f"  config.json 에 \"quickadd_hotkey\": \"ctrl+alt+space\" 처럼 적어\n"
            f"  다른 조합으로 바꿀 수 있습니다."
        )
        return

    msg = wintypes.MSG()
    try:
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY:
                on_press()
    finally:
        user32.UnregisterHotKey(None, 1)


def window_handle(win):
    """tkinter 창의 진짜 윈도우 손잡이(HWND)."""
    hwnd = win.winfo_id()
    parent = ctypes.windll.user32.GetParent(hwnd)
    return parent or hwnd


def round_corners(hwnd, width, height, radius=18):
    """창 모서리를 둥글게 깎습니다.

    제목표시줄을 없앤 창(WS_POPUP)이라 윈도우가 알아서 둥글게 해 주지
    않습니다. 창의 모양 자체를 둥근 사각형으로 잘라 냅니다.
    윈도우 11 의 DwmSetWindowAttribute 도 함께 시도합니다 — 그쪽이 먹으면
    가장자리가 더 매끈합니다.
    """
    user32 = ctypes.windll.user32
    try:
        # 33 = DWMWA_WINDOW_CORNER_PREFERENCE, 2 = ROUND
        pref = ctypes.c_int(2)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            wintypes.HWND(hwnd), ctypes.c_uint(33),
            ctypes.byref(pref), ctypes.sizeof(pref),
        )
    except Exception:
        pass

    try:
        region = ctypes.windll.gdi32.CreateRoundRectRgn(
            0, 0, width + 1, height + 1, radius * 2, radius * 2
        )
        user32.SetWindowRgn(hwnd, region, True)
    except Exception:
        pass


def force_foreground(hwnd):
    """창을 맨 앞으로 끌어옵니다.

    윈도우는 배경 프로그램이 제멋대로 앞으로 나오는 것을 막습니다. 다만
    사용자가 단축키를 누른 직후에는 허용해 줍니다. 그래도 안 될 때를 대비해
    입력 스레드를 잠시 붙였다 뗍니다.
    """
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    try:
        foreground = user32.GetForegroundWindow()
        target = user32.GetWindowThreadProcessId(foreground, None)
        mine = kernel32.GetCurrentThreadId()
        if target and target != mine:
            user32.AttachThreadInput(target, mine, True)
            user32.SetForegroundWindow(hwnd)
            user32.AttachThreadInput(target, mine, False)
        else:
            user32.SetForegroundWindow(hwnd)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 보이는 것
# ---------------------------------------------------------------------------
PLACEHOLDER = "일정을 추가하세요."

# macOS 스포트라이트의 어두운 판을 따라갑니다. 회색 판 위에 흰 글씨,
# 고른 것 하나만 파란 알약으로 칠하는 방식입니다.
BG = "#3a3a3c"           # 창 바탕
DIVIDER = "#4c4c4e"      # 검색줄 아래 가는 선
PILL = "#5a5a5e"         # 오른쪽 작은 표지
FG = "#f2f2f7"           # 본문 글씨
HINT = "#98989d"         # 흐린 글씨
DIM = "#6d6d72"          # 이번 달이 아닌 날
HOVER = "#4c4c50"        # 마우스를 올린 날
BLUE = "#0a6cf5"         # 고른 것 / 오늘
OK = "#7ee08a"
BAD = "#ff7b72"
SUN = "#ff8a80"
SAT = "#8ab4ff"

WIDTH = 660
RADIUS = 20

# 부드러운 순서대로 찾습니다. 맑은 고딕 Semilight 는 윈도우에 들어 있는
# 가는 획 글꼴이라 같은 맑은 고딕이라도 훨씬 부드럽게 보입니다.
FONT_CHOICES = ["맑은 고딕 Semilight", "Noto Sans KR", "맑은 고딕",
                "Malgun Gothic", "Segoe UI"]


def pick_font():
    available = set(tkfont.families())
    for name in FONT_CHOICES:
        if name in available:
            return name
    return "TkDefaultFont"


WEEKDAY_LABELS = ["일", "월", "화", "수", "목", "금", "토"]


def three_weeks(today):
    """지난주·이번주·다음주 = 3주치 날짜를 [7개씩 3줄] 로.

    일요일 시작입니다. 파이썬의 weekday() 는 월요일이 0 이라
    일요일이 앞에 오도록 하루를 밀어 계산합니다.
    """
    sunday = today - timedelta(days=(today.weekday() + 1) % 7)
    return [[sunday + timedelta(days=week * 7 + day) for day in range(7)]
            for week in (-1, 0, 1)]


def round_rect(canvas, x1, y1, x2, y2, radius, **options):
    """캔버스에 모서리가 둥근 사각형. 고른 날을 칠하는 알약 모양입니다.

    tkinter 캔버스에는 둥근 사각형이 없습니다. 모서리에 점을 촘촘히 두고
    smooth 로 이어 붙이면 곡선이 됩니다.
    """
    points = [
        x1 + radius, y1, x2 - radius, y1, x2, y1,
        x2, y1 + radius, x2, y2 - radius, x2, y2,
        x2 - radius, y2, x1 + radius, y2, x1, y2,
        x1, y2 - radius, x1, y1 + radius, x1, y1,
    ]
    return canvas.create_polygon(points, smooth=True, **options)


class MiniCalendar:
    """입력창 아래 붙는 3주짜리 달력.

    칸마다 위젯을 두지 않고 캔버스에 직접 그립니다. 위젯으로 만들면
    네모난 칸밖에 칠할 수 없는데, 스포트라이트처럼 고른 것을 둥근 알약으로
    칠하려면 직접 그리는 수밖에 없습니다. 덤으로, 마우스가 칸과 글자
    사이를 오갈 때 효과가 깜빡이던 문제도 사라집니다 — 캔버스 하나 안에서는
    마우스가 나간 적이 없기 때문입니다.
    """

    HEAD_H = 26              # 요일 줄 높이
    CELL_H = 38
    PILL_W = 58              # 열 너비(약 89) 안에서 넉넉하게
    PILL_H = 32

    def __init__(self, parent, today, font_name, on_hover=None):
        self.today = today
        self.font = font_name
        self.on_hover = on_hover or (lambda _day: None)
        self.weeks = three_weeks(today)
        self.titles = {}
        self.hovered = None

        width = WIDTH - 36
        self.col_w = width / 7
        height = self.HEAD_H + self.CELL_H * len(self.weeks)

        self.cv = tk.Canvas(parent, width=width, height=height, bg=BG,
                            highlightthickness=0, bd=0)
        self.cv.pack(padx=18, pady=(2, 2))
        self.cv.bind("<Motion>", self._motion)
        self.cv.bind("<Leave>", lambda _e: self._hover(None))

        self._draw()

    # -- 그리기 ------------------------------------------------------------
    def _center(self, row, col):
        return (col + 0.5) * self.col_w, self.HEAD_H + (row + 0.5) * self.CELL_H

    def _draw(self):
        cv = self.cv
        cv.delete("all")

        for col, name in enumerate(WEEKDAY_LABELS):
            color = SUN if col == 0 else (SAT if col == 6 else HINT)
            cv.create_text((col + 0.5) * self.col_w, self.HEAD_H / 2,
                           text=name, fill=color, font=(self.font, 9))

        for row, week in enumerate(self.weeks):
            for col, day in enumerate(week):
                cx, cy = self._center(row, col)
                this_month = day.month == self.today.month
                is_today = day == self.today

                if is_today or day == self.hovered:
                    round_rect(cv, cx - self.PILL_W / 2, cy - self.PILL_H / 2,
                               cx + self.PILL_W / 2, cy + self.PILL_H / 2,
                               10, fill=BLUE if is_today else HOVER, outline="")

                if is_today:
                    color = "#ffffff"
                elif not this_month:
                    color = DIM
                elif col == 0:
                    color = SUN
                elif col == 6:
                    color = SAT
                else:
                    color = FG

                weight = "bold" if is_today else "normal"
                cv.create_text(cx, cy - 4, text=str(day.day), fill=color,
                               font=(self.font, 11, weight))

                if self.titles.get(day):
                    dot = "#ffffff" if is_today else (BLUE if this_month else DIM)
                    cv.create_oval(cx - 2, cy + 8, cx + 2, cy + 12,
                                   fill=dot, outline="")

    # -- 바깥에서 쓰는 것 ---------------------------------------------------
    def span(self):
        return self.weeks[0][0], self.weeks[-1][-1]

    def mark(self, titles):
        self.titles = titles or {}
        self._draw()

    # -- 마우스 ------------------------------------------------------------
    def _motion(self, event):
        col = int(event.x // self.col_w)
        row = int((event.y - self.HEAD_H) // self.CELL_H)
        if 0 <= col < 7 and 0 <= row < len(self.weeks):
            self._hover(self.weeks[row][col])
        else:
            self._hover(None)

    def _hover(self, day):
        if day == self.hovered:
            return
        self.hovered = day
        self._draw()
        self.on_hover(day)


class QuickAdd:
    def __init__(self, root):
        import nlp_date

        self.root = root
        self.nlp = nlp_date
        self.font = pick_font()
        self.win = None
        self.entry = None
        self.status = None
        self.calendar = None
        self.tip = None
        self.tip_font = None
        self.badge = None
        self.titles = None           # 아직 안 가져옴 (빈 dict 와 구분합니다)
        self.pending = None          # 확인 대기 중인 일정
        self.busy = False
        self.autohide = True         # 시험할 때만 끕니다
        self.results = queue.Queue()
        self.dots = queue.Queue()

    # -- 창 만들기 ---------------------------------------------------------
    def show(self):
        if self.win is not None and self.win.winfo_exists():
            self.win.deiconify()
            self.entry.focus_force()
            return

        win = tk.Toplevel(self.root)
        win.overrideredirect(True)          # 제목표시줄 없이
        win.configure(bg=BG)
        win.attributes("-topmost", True)

        today = datetime.now(self.nlp.KST).date()

        frame = tk.Frame(win, bg=BG)
        frame.pack(fill="both", expand=True)

        # --- 검색줄 -------------------------------------------------------
        row = tk.Frame(frame, bg=BG)
        row.pack(fill="x", padx=20, pady=(16, 12))

        glass = tk.Canvas(row, width=26, height=26, bg=BG,
                          highlightthickness=0, bd=0)
        glass.create_oval(4, 4, 18, 18, outline=HINT, width=2)
        glass.create_line(17, 17, 23, 23, fill=HINT, width=2, capstyle="round")
        glass.pack(side="left", padx=(0, 12))

        # 오른쪽 작은 표지. 지금 Enter 를 누르면 무슨 일이 일어나는지 알려 줍니다.
        self.badge = tk.Label(row, text="확인", font=(self.font, 10), bg=PILL,
                              fg=FG, padx=10, pady=2)
        self.badge.pack(side="right", padx=(12, 0))

        text = tk.StringVar()
        entry = tk.Entry(row, textvariable=text, font=(self.font, 20), bg=BG,
                         fg=FG, insertbackground=FG, relief="flat", borderwidth=0)
        entry.pack(side="left", fill="x", expand=True)

        # 회색 안내글은 입력칸 '위에 겹쳐 놓은 라벨'입니다.
        # 입력칸에 실제 글자로 넣어 두면, 한글 입력기로 첫 글자를 칠 때
        # 지워지지 않고 "일정을 추가하세요.가나다" 처럼 뒤에 붙어 버립니다.
        # (입력기는 보통의 키 이벤트를 거치지 않고 완성된 글자를 넣습니다)
        hint = tk.Label(row, text=PLACEHOLDER, font=(self.font, 20), bg=BG, fg=HINT)
        hint.place(in_=entry, x=2, y=0)
        hint.bind("<Button-1>", lambda _e: entry.focus_set())

        def toggle_hint(*_args):
            if text.get():
                hint.place_forget()
            else:
                hint.place(in_=entry, x=2, y=0)

        text.trace_add("write", toggle_hint)

        # --- 구분선 -------------------------------------------------------
        tk.Frame(frame, bg=DIVIDER, height=1).pack(fill="x")

        status = tk.Label(frame, text="Enter 로 확인 · Esc 로 닫기",
                          font=(self.font, 9), bg=BG, fg=HINT, anchor="w")
        status.pack(fill="x", padx=20, pady=(10, 8))

        # 스포트라이트의 '시스템 설정' 같은 작은 구역 이름입니다.
        tk.Label(frame, text=f"{today.year}년 {today.month}월",
                 font=(self.font, 9, "bold"), bg=BG, fg=HINT,
                 anchor="w").pack(fill="x", padx=20, pady=(0, 2))

        entry.bind("<Return>", self._on_enter)
        entry.bind("<Escape>", self._on_escape)
        entry.bind("<FocusOut>", self._on_focus_out)

        self.win, self.entry, self.status = win, entry, status
        self.pending = None
        self.busy = False

        self.calendar = MiniCalendar(frame, today, self.font, self._on_hover)

        # 마우스를 올린 날의 일정 제목이 여기 뜹니다. 비어 있어도 자리를
        # 남겨 둡니다. 글이 들어올 때마다 창 높이가 바뀌면 눈이 피곤합니다.
        self.tip_font = tkfont.Font(family=self.font, size=9)
        self.tip = tk.Label(frame, text=" ", font=self.tip_font,
                            bg=BG, fg=HINT, anchor="w")
        self.tip.pack(fill="x", padx=20, pady=(2, 14))

        # 창을 화면 한가운데에. 높이는 내용이 정해진 뒤에야 알 수 있어서
        # 다 그려 놓고 마지막에 자리를 잡습니다.
        win.update_idletasks()
        height = win.winfo_reqheight()
        screen_w, screen_h = win.winfo_screenwidth(), win.winfo_screenheight()
        win.geometry(f"{WIDTH}x{height}"
                     f"+{(screen_w - WIDTH) // 2}+{(screen_h - height) // 2}")
        win.update_idletasks()

        hwnd = window_handle(win)
        round_corners(hwnd, WIDTH, height, RADIUS)
        force_foreground(hwnd)
        entry.focus_force()

        self._fetch_dots()

    def hide(self):
        if self.win is not None and self.win.winfo_exists():
            self.win.destroy()
        self.win = self.entry = self.status = self.calendar = self.tip = None
        self.badge = None
        self.titles = None
        self.pending = None
        self.busy = False

    # -- 달력에 마우스 올렸을 때 -------------------------------------------
    def _fit(self, text):
        """창 폭을 넘지 않게 자릅니다.

        글자 수로 세면 안 됩니다. 한글 한 글자는 영문 두 글자만큼 넓어서,
        같은 40글자라도 어떤 줄은 넘치고 어떤 줄은 절반만 찹니다.
        실제로 그려질 폭을 재서 자릅니다.
        """
        room = WIDTH - 44                   # 좌우 여백
        if self.tip_font is None or self.tip_font.measure(text) <= room:
            return text
        while text and self.tip_font.measure(text + "…") > room:
            text = text[:-1]
        return text.rstrip() + "…"

    def _on_hover(self, day):
        if self.tip is None:
            return
        if day is None:
            self.tip.configure(text=" ", fg=HINT)
            return

        head = f"{day.month}/{day.day}({WEEKDAY_LABELS[(day.weekday() + 1) % 7]})"

        if self.titles is None:             # 아직 가져오는 중
            self.tip.configure(text=f"{head}  일정을 확인하는 중...", fg=DIM)
            return

        titles = self.titles.get(day) or []
        if not titles:
            self.tip.configure(text=f"{head}  일정 없음", fg=DIM)
            return

        self.tip.configure(text=self._fit(f"{head}  " + " · ".join(titles)), fg=FG)

    # -- 달력 점 -----------------------------------------------------------
    def _fetch_dots(self, fresh=False):
        """일정이 있는 날을 뒤에서 알아 옵니다. 창은 이미 떠 있습니다."""
        if self.calendar is None:
            return
        start, end = self.calendar.span()

        def worker():
            import icloud_cal
            try:
                if fresh:
                    icloud_cal.clear_cache()
                self.dots.put(icloud_cal.event_titles(start, end))
            except Exception:
                self.dots.put({})            # 점이 없을 뿐, 창은 멀쩡합니다

        threading.Thread(target=worker, daemon=True).start()

    # -- 입력 처리 ---------------------------------------------------------
    def _say(self, text, color=HINT, badge=None):
        if self.status is not None:
            self.status.configure(text=text, fg=color)
        if self.badge is not None and badge is not None:
            # 지금 Enter 를 누르면 무슨 일이 생기는지를 표지에 적어 둡니다.
            self.badge.configure(text=badge,
                                 bg=BLUE if badge == "확정" else PILL)

    def _on_focus_out(self, _event=None):
        """다른 프로그램으로 넘어가면 창을 닫습니다.

        주의: 창 자체가 아니라 입력칸에 걸어야 합니다. 창에 걸면 포커스가
        창에서 입력칸으로 넘어가는 것까지 '나갔다'로 쳐서, 뜨자마자 닫힙니다.
        그리고 곧바로 판단하지 않고 한 박자 뒤에 봅니다. 포커스가 옮겨가는
        도중에는 아직 아무도 포커스를 안 가진 순간이 있기 때문입니다.
        """
        if self.win is None or not self.autohide:
            return
        self.win.after(150, self._hide_if_left)

    def _hide_if_left(self):
        if self.win is None or not self.win.winfo_exists() or self.busy:
            return
        try:
            if self.win.focus_displayof() is None:
                self.hide()
        except (KeyError, tk.TclError):
            pass

    def _on_escape(self, _event=None):
        if self.pending is not None:        # 확인 화면에서는 고치던 글로 돌아갑니다
            self.pending = None
            self._say("Enter 로 확인 · Esc 로 닫기", HINT, badge="확인")
            return "break"
        self.hide()
        return "break"

    def _on_enter(self, _event=None):
        if self.busy:
            return "break"

        if self.pending is not None:        # 두 번째 Enter — 확정
            self._send(self.pending)
            return "break"

        text = (self.entry.get() or "").strip()
        if not text:
            return "break"

        try:
            event = self.nlp.parse(text)
        except self.nlp.ParseError as exc:
            self._say(f"{exc} — 날짜를 넣어 주세요 (예: 내일, 8월 8일)", BAD,
                      badge="확인")
            return "break"

        self.pending = event
        self._say(f"{self.nlp.describe(event)}      Enter 확정 · Esc 수정", OK,
                  badge="확정")
        return "break"

    # -- 보내기 -----------------------------------------------------------
    def _send(self, event):
        self.busy = True
        self._say("넣는 중...", HINT, badge="전송 중")

        def worker():
            import icloud_cal
            try:
                name, _uid = icloud_cal.add_event(event)
                self.results.put(("ok", name, event))
            except Exception as exc:        # 네트워크·인증 등 무엇이든
                self.results.put(("fail", str(exc), event))

        threading.Thread(target=worker, daemon=True).start()

    def drain(self):
        """뒤에서 돌던 일이 끝났는지 살핍니다.

        tkinter 는 주 스레드에서만 건드릴 수 있어서, 결과를 대기줄로 받아
        여기서 화면에 반영합니다.
        """
        try:
            titles = self.dots.get_nowait()
        except queue.Empty:
            pass
        else:
            self.titles = titles
            if self.calendar is not None:
                self.calendar.mark(titles)
                # 가져오는 동안 이미 어떤 날에 마우스를 올려 뒀을 수 있습니다.
                if self.calendar.hovered is not None:
                    self._on_hover(self.calendar.hovered)

        try:
            kind, detail, event = self.results.get_nowait()
        except queue.Empty:
            return

        self.busy = False
        if self.win is None or not self.win.winfo_exists():
            return

        if kind == "ok":
            self._say(f"[{detail}] 에 넣었습니다 — {self.nlp.describe(event)}", OK,
                      badge="완료")
            # 방금 넣은 날에 점이 찍히는 것을 보여 주고 닫습니다.
            self._fetch_dots(fresh=True)
            self.win.after(1400, self.hide)
        else:
            self.pending = event            # 다시 Enter 로 재시도할 수 있게
            first_line = detail.strip().splitlines()[0]
            self._say(f"실패: {first_line}   Enter 로 다시 시도", BAD, badge="재시도")


# ---------------------------------------------------------------------------
# 시작프로그램 등록
# ---------------------------------------------------------------------------
STARTUP_NAME = "로스쿨-빠른일정.vbs"


def startup_path():
    return os.path.join(
        os.environ.get("APPDATA", ""),
        "Microsoft", "Windows", "Start Menu", "Programs", "Startup", STARTUP_NAME,
    )


def install_startup():
    pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    if not os.path.exists(pythonw):
        pythonw = sys.executable
    script = os.path.join(BASE_DIR, "quickadd.py")

    # 검은 창이 깜빡이지 않게 vbs 로 조용히 띄웁니다(세 번째 인자 0 = 창 숨김).
    content = (
        'CreateObject("WScript.Shell").Run '
        f'"""{pythonw}"" ""{script}""", 0, False\r\n'
    )
    path = startup_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fp:
        fp.write(content)
    print(f"등록했습니다: {path}")
    print("다음 부팅부터 자동으로 켜집니다. 지금 켜려면: python quickadd.py")
    return 0


def remove_startup():
    path = startup_path()
    if os.path.exists(path):
        os.remove(path)
        print(f"해제했습니다: {path}")
    else:
        print("등록돼 있지 않습니다.")
    return 0


# ---------------------------------------------------------------------------
def add_from_cli(text):
    import icloud_cal
    import nlp_date
    try:
        event = nlp_date.parse(text)
    except nlp_date.ParseError as exc:
        print(f"해석 실패: {exc}")
        return 1
    print(nlp_date.describe(event))
    try:
        name, _uid = icloud_cal.add_event(event)
    except icloud_cal.CalDAVError as exc:
        print(f"실패: {exc}")
        return 1
    print(f"[{name}] 에 넣었습니다.")
    return 0


def run_app(hotkey, show_now):
    root = tk.Tk()
    root.withdraw()

    app = QuickAdd(root)
    triggers = queue.Queue()
    errors = queue.Queue()

    def pump():
        try:
            triggers.get_nowait()
            app.show()
        except queue.Empty:
            pass
        try:
            message = errors.get_nowait()
            print(f"단축키 오류: {message}")
        except queue.Empty:
            pass
        app.drain()
        root.after(60, pump)

    if not show_now:
        threading.Thread(
            target=hotkey_loop,
            args=(hotkey, lambda: triggers.put(1), errors.put),
            daemon=True,
        ).start()
        print(f"켜졌습니다. {hotkey} 를 누르면 입력창이 뜹니다. (끄려면 Ctrl+C)")
    else:
        app.show()

    root.after(60, pump)
    try:
        root.mainloop()
    except KeyboardInterrupt:
        pass
    return 0


def main():
    ap = argparse.ArgumentParser(description="Alt+Space 로 아이폰 캘린더에 일정 추가")
    ap.add_argument("--once", action="store_true", help="단축키 없이 창만 한 번")
    ap.add_argument("--text", help="창 없이 바로 넣기")
    ap.add_argument("--install-startup", action="store_true", help="부팅 시 자동 실행")
    ap.add_argument("--remove-startup", action="store_true", help="자동 실행 해제")
    args = ap.parse_args()

    if args.install_startup:
        return install_startup()
    if args.remove_startup:
        return remove_startup()
    if args.text:
        return add_from_cli(args.text)

    import icloud_cal
    hotkey = (icloud_cal.load_config().get("quickadd_hotkey") or DEFAULT_HOTKEY)
    return run_app(hotkey, show_now=args.once)


if __name__ == "__main__":
    sys.exit(main())
