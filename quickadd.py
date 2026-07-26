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

BG = "#1f2126"
CARD = "#272a30"
FG = "#f0f2f5"
HINT = "#828a96"
DIM = "#4a505a"          # 이번 달이 아닌 날
HOVER = "#363b44"        # 마우스를 올린 날
OK = "#93d19a"
BAD = "#e8918f"
ACCENT = "#7ea6f0"
SUN = "#e08b8b"
SAT = "#8fa8dc"

WIDTH = 660
RADIUS = 18

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


class MiniCalendar:
    """입력창 아래 붙는 3주짜리 달력.

    일정이 있는 날에는 점을 찍고, 마우스를 올리면 그 날이 밝아지면서
    제목을 알려 줍니다(알려 주는 곳은 바깥에서 정합니다).
    """

    def __init__(self, parent, today, font_name, on_hover=None):
        self.today = today
        self.on_hover = on_hover or (lambda _day: None)
        self.cells = {}                    # 날짜 -> (칸, 숫자, 점)
        self.hovered = None
        self._clear_job = None

        wrap = tk.Frame(parent, bg=BG)
        wrap.pack(fill="x", padx=14, pady=(2, 4))

        head = tk.Label(wrap, text=f"{today.year}년 {today.month}월",
                        font=(font_name, 9), bg=BG, fg=HINT)
        head.grid(row=0, column=0, columnspan=7, pady=(0, 4))

        for col, name in enumerate(WEEKDAY_LABELS):
            color = SUN if col == 0 else (SAT if col == 6 else HINT)
            tk.Label(wrap, text=name, font=(font_name, 9), bg=BG, fg=color) \
                .grid(row=1, column=col, sticky="nsew")

        for row, week in enumerate(three_weeks(today), start=2):
            for col, day in enumerate(week):
                cell = tk.Frame(wrap, bg=BG)
                cell.grid(row=row, column=col, sticky="nsew", pady=1)

                if day.month == today.month:
                    color = SUN if col == 0 else (SAT if col == 6 else FG)
                else:
                    color = DIM                     # 이번 달이 아닌 날은 흐리게

                weight = "bold" if day == today else "normal"
                if day == today:
                    color = ACCENT

                num = tk.Label(cell, text=str(day.day), font=(font_name, 10, weight),
                               bg=BG, fg=color)
                num.pack()
                # 점 자리는 비어 있어도 미리 잡아 둡니다. 나중에 글자만
                # 채우면 달력이 위아래로 덜컹거리지 않습니다.
                dot = tk.Label(cell, text="", font=(font_name, 8), bg=BG, fg=ACCENT)
                dot.pack()
                self.cells[day] = (cell, num, dot)

                # 칸과 그 안의 글자에 모두 걸어야 합니다. Tk 는 마우스가
                # 칸에서 숫자로 넘어가는 것도 '칸에서 나갔다'로 세기 때문에,
                # 칸에만 걸면 숫자 위에 있을 때 효과가 풀립니다.
                for widget in (cell, num, dot):
                    widget.bind("<Enter>", lambda _e, d=day: self._enter(d))
                    widget.bind("<Leave>", lambda _e, d=day: self._leave(d))

        for col in range(7):
            wrap.grid_columnconfigure(col, weight=1, uniform="day")

        self.wrap = wrap

    def span(self):
        days = sorted(self.cells)
        return days[0], days[-1]

    def mark(self, titles):
        """일정이 있는 날에 점을 켭니다. titles 는 {날짜: [제목, ...]}."""
        for day, (_cell, _num, dot) in self.cells.items():
            has = bool(titles.get(day))
            dot.configure(text="•" if has else "",
                          fg=ACCENT if day.month == self.today.month else DIM)

    # -- 마우스 ------------------------------------------------------------
    def _paint(self, day, background):
        parts = self.cells.get(day)
        if not parts:
            return
        for widget in parts:
            widget.configure(bg=background)

    def _enter(self, day):
        # 칸 안에서 숫자로 옮겨가는 순간 잠깐 '나감'이 끼어듭니다.
        # 예약된 지우기를 취소해서 깜빡이지 않게 합니다.
        if self._clear_job is not None:
            self.wrap.after_cancel(self._clear_job)
            self._clear_job = None
        if self.hovered == day:
            return
        if self.hovered is not None:
            self._paint(self.hovered, BG)
        self.hovered = day
        self._paint(day, HOVER)
        self.on_hover(day)

    def _leave(self, _day):
        if self._clear_job is not None:
            self.wrap.after_cancel(self._clear_job)
        self._clear_job = self.wrap.after(40, self._clear)

    def _clear(self):
        self._clear_job = None
        if self.hovered is not None:
            self._paint(self.hovered, BG)
            self.hovered = None
        self.on_hover(None)


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

        frame = tk.Frame(win, bg=BG)
        frame.pack(fill="both", expand=True)

        # 입력칸은 조금 밝은 판 위에 올려 눈에 띄게 합니다.
        card = tk.Frame(frame, bg=CARD)
        card.pack(fill="x", padx=14, pady=(14, 8))

        text = tk.StringVar()
        entry = tk.Entry(card, textvariable=text, font=(self.font, 15), bg=CARD,
                         fg=FG, insertbackground=FG, relief="flat", borderwidth=0)
        entry.pack(fill="x", padx=12, pady=10)

        # 회색 안내글은 입력칸 '위에 겹쳐 놓은 라벨'입니다.
        # 입력칸에 실제 글자로 넣어 두면, 한글 입력기로 첫 글자를 칠 때
        # 지워지지 않고 "일정을 추가하세요.가나다" 처럼 뒤에 붙어 버립니다.
        # (입력기는 보통의 키 이벤트를 거치지 않고 완성된 글자를 넣습니다)
        hint = tk.Label(card, text=PLACEHOLDER, font=(self.font, 15),
                        bg=CARD, fg=HINT)
        hint.place(in_=entry, x=2, y=0)
        hint.bind("<Button-1>", lambda _e: entry.focus_set())

        def toggle_hint(*_args):
            if text.get():
                hint.place_forget()
            else:
                hint.place(in_=entry, x=2, y=0)

        text.trace_add("write", toggle_hint)

        status = tk.Label(frame, text="Enter 로 확인 · Esc 로 닫기",
                          font=(self.font, 9), bg=BG, fg=HINT, anchor="w")
        status.pack(fill="x", padx=18, pady=(0, 6))

        entry.bind("<Return>", self._on_enter)
        entry.bind("<Escape>", self._on_escape)
        entry.bind("<FocusOut>", self._on_focus_out)

        self.win, self.entry, self.status = win, entry, status
        self.pending = None
        self.busy = False

        today = datetime.now(self.nlp.KST).date()
        self.calendar = MiniCalendar(frame, today, self.font, self._on_hover)

        # 마우스를 올린 날의 일정 제목이 여기 뜹니다. 비어 있어도 자리를
        # 남겨 둡니다. 글이 들어올 때마다 창 높이가 바뀌면 눈이 피곤합니다.
        self.tip_font = tkfont.Font(family=self.font, size=9)
        self.tip = tk.Label(frame, textvariable=None, text=" ", font=self.tip_font,
                            bg=BG, fg=HINT, anchor="w")
        self.tip.pack(fill="x", padx=18, pady=(0, 12))

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
    def _say(self, text, color=HINT):
        if self.status is not None:
            self.status.configure(text=text, fg=color)

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
            self._say("Enter 로 확인 · Esc 로 닫기")
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
            self._say(f"{exc} — 날짜를 넣어 주세요 (예: 내일, 8월 8일)", BAD)
            return "break"

        self.pending = event
        self._say(f"{self.nlp.describe(event)}      Enter 확정 · Esc 수정", OK)
        return "break"

    # -- 보내기 -----------------------------------------------------------
    def _send(self, event):
        self.busy = True
        self._say("넣는 중...", HINT)

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
            self._say(f"[{detail}] 에 넣었습니다 — {self.nlp.describe(event)}", OK)
            # 방금 넣은 날에 점이 찍히는 것을 보여 주고 닫습니다.
            self._fetch_dots(fresh=True)
            self.win.after(1400, self.hide)
        else:
            self.pending = event            # 다시 Enter 로 재시도할 수 있게
            first_line = detail.strip().splitlines()[0]
            self._say(f"실패: {first_line}   Enter 로 다시 시도", BAD)


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
