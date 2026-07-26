# -*- coding: utf-8 -*-
"""게시판 HTML 을 읽어서 글 목록과 본문을 뽑아냅니다.

왜 정규식이 아니라 html.parser 인가
-----------------------------------
두 사이트가 각각 고약한 버릇이 있는데, 표준 라이브러리의 HTMLParser 가
그 버릇들을 공짜로 처리해 줍니다.

  서강대: 글 제목이 바로 앞 HTML 주석 안에 한 번 더 들어 있습니다.
          HTMLParser 는 주석을 handle_comment 로 따로 넘겨주므로
          본문 텍스트(handle_data)에는 애초에 섞이지 않습니다.
          덤으로 '상단고정 시작/끝' 표시도 같은 통로로 들어옵니다.

  LEET:   <a> 태그를 닫지 않습니다. 제목 글자가 </td> 까지 흘러갑니다.
          칸(<td>) 단위로 글자를 모으면 </a> 가 없어도 아무 문제가 없습니다.

정규식으로 하면 두 경우 모두 예외 처리를 따로 붙여야 하고, 학교가 마크업을
조금만 손봐도 조용히 깨집니다.
"""

import html as html_mod
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser

import sources as cfg

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
              "AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/124.0 Safari/537.36 LawSecretary/1.0")

SOGANG_BASE = "https://lawschool.sogang.ac.kr/front"
UWAY_BASE = "https://leet.uwayapply.com/board"

# 글자 사이에 줄바꿈을 넣어야 하는 태그들.
# 이 목록에 없는 태그(<span>, <b> 같은 것)는 자리에 아무것도 넣지 않습니다.
# 그래야 "2026.7.27.(월)" 이 "2026.7.27.( 월 )" 로 벌어지지 않습니다.
BLOCK_TAGS = {
    "p", "div", "br", "hr", "tr", "td", "th", "li", "ul", "ol", "table",
    "h1", "h2", "h3", "h4", "h5", "h6", "section", "article", "blockquote", "pre",
}

DATE_RE = re.compile(r"(\d{4})\s*[.\-/]\s*(\d{1,2})\s*[.\-/]\s*(\d{1,2})")
NOTICE_PREFIX_RE = re.compile(r"^\s*\[\s*공지\s*\]\s*")
# 첨부 문서의 확장자를 찾는 규칙.
#  - 그림 파일(jpg/png)은 뺍니다. 페이지 장식용 이미지가 잔뜩 딸려옵니다.
#  - 확장자 뒤에 글자가 이어지면 안 됩니다. 이게 없으면 자바스크립트의
#    'window.document' 에서 'window.doc' 를 첨부파일로 잘못 잡습니다.
#
# 확장자만 찾고 파일 이름은 뒤로 걸어가며 모읍니다. '이름+확장자'를 한 번에
# 찾는 정규식(예: [^\s<>"']+\.hwp)을 쓰면 안 됩니다. 서강대 게시글에는
# 인라인 스타일과 긴 주소가 잔뜩 들어 있어서, 그런 규칙은 글자마다 수천 자를
# 되짚어 보느라 페이지 한 장에 수십 초가 걸립니다. (실제로 겪었습니다)
ATTACH_EXT_RE = re.compile(
    r"\.(?:hwpx?|pdf|docx?|xlsx?|pptx?|zip)(?![A-Za-z0-9])", re.IGNORECASE)

# 파일 이름이 여기서 끊긴다고 보는 글자들.
# 괄호는 넣지 않습니다. '실시계획(안).hwp' 같은 이름이 실제로 있습니다.
NAME_STOP = set(" \t\r\n\f\v<>\"'/\\=&?;|,")
NAME_MAX = 100

SCRIPT_BLOCK_RE = re.compile(r"<(script|style)\b.*?</\1>", re.IGNORECASE | re.DOTALL)

_last_request_at = [0.0]


# ---------------------------------------------------------------------------
# 글자 다듬기
# ---------------------------------------------------------------------------
def clean_inline(text):
    """제목처럼 한 줄짜리 글자를 다듬습니다.

    서강대 제목에는 눈에 보이지 않는 문자가 섞여 있습니다.
    (U+FEFF, U+200B) 그대로 두면 같은 제목인데도 달라 보이고,
    텔레그램에서 이상한 자리에 줄이 바뀝니다.
    """
    if not text:
        return ""
    text = text.replace("﻿", "").replace("​", "").replace("\xa0", " ")
    text = html_mod.unescape(text)
    return " ".join(text.split()).strip()


def strip_notice_prefix(title):
    """제목 앞에 붙은 '[공지]' 를 뗍니다. 고정글 여부는 따로 기록하므로 필요 없습니다."""
    return NOTICE_PREFIX_RE.sub("", title or "").strip()


def normalize_date(text):
    """'2026.07.24' / '2026-07-01' / '2026.7.4 15:40:50' -> '2026-07-24'.

    날짜가 없으면 빈 문자열을 돌려줍니다.
    """
    m = DATE_RE.search(text or "")
    if not m:
        return ""
    year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return ""
    if not (2000 <= year <= 2100):
        return ""
    return f"{year:04d}-{month:02d}-{day:02d}"


def qs_value(url, key):
    """URL 의 물음표 뒤에서 값 하나를 꺼냅니다.

    정규식 대신 표준 파서를 쓰는 이유: 파라미터 순서가 바뀌어도 안전합니다.
    """
    try:
        query = urllib.parse.urlparse(html_mod.unescape(url)).query
        values = urllib.parse.parse_qs(query).get(key) or []
        return (values[0] or "").strip()
    except ValueError:
        return ""


# ---------------------------------------------------------------------------
# 내려받기
# ---------------------------------------------------------------------------
def fetch(url, timeout=30, retries=2):
    """페이지 하나를 글자로 받아옵니다.

    학교 서버에 부담을 주지 않도록 요청 사이에 간격을 둡니다.
    한 번 실패하면 잠깐 쉬었다가 다시 시도합니다. 학교 서버는 가끔
    아무 이유 없이 한 번 끊고 두 번째에 잘 받아 줍니다.
    """
    last_error = None
    for attempt in range(1, retries + 1):
        gap = time.time() - _last_request_at[0]
        if gap < cfg.REQUEST_DELAY:
            time.sleep(cfg.REQUEST_DELAY - gap)
        _last_request_at[0] = time.time()

        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "ko-KR,ko;q=0.9",
            })
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
                charset = resp.headers.get_content_charset()
            return _decode(raw, charset)
        except (urllib.error.URLError, urllib.error.HTTPError, OSError) as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(5)

    raise IOError(f"{type(last_error).__name__}: {last_error}")


def _decode(raw, charset=None):
    """받은 바이트를 글자로 바꿉니다. 한국 사이트는 EUC-KR 인 곳도 있습니다."""
    raw = raw.lstrip(b"\xef\xbb\xbf")
    candidates = [charset] if charset else []
    # 헤더가 거짓말을 하는 경우가 있어서 문서 안의 선언도 봅니다.
    head = raw[:2048].decode("ascii", errors="ignore")
    m = re.search(r'charset=["\']?([\w-]+)', head, re.IGNORECASE)
    if m:
        candidates.append(m.group(1))
    candidates += ["utf-8", "euc-kr", "cp949"]

    for enc in candidates:
        if not enc:
            continue
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# 영역 잘라내기
# ---------------------------------------------------------------------------
def extract_region(html_text, tag, class_name):
    """<tag class="...class_name..."> 부터 짝이 맞는 </tag> 까지를 잘라냅니다.

    본문만 뽑아내려고 씁니다. 페이지 전체를 글자로 바꾸면 메뉴·푸터·저작권
    문구까지 딸려와서 Claude 가 엉뚱한 날짜를 주울 수 있습니다.
    """
    opener = re.compile(
        r"<" + tag + r"\b[^>]*\bclass\s*=\s*[\"']([^\"']*)[\"'][^>]*>", re.IGNORECASE)
    scanner = re.compile(r"<(/?)" + tag + r"\b", re.IGNORECASE)

    for m in opener.finditer(html_text):
        if class_name not in m.group(1).split():
            continue
        start = m.end()
        depth = 1
        pos = start
        while depth:
            mm = scanner.search(html_text, pos)
            if not mm:
                return html_text[start:]        # 닫는 태그가 없으면 끝까지
            depth += -1 if mm.group(1) else 1
            pos = mm.end()
            if depth == 0:
                return html_text[start:mm.start()]
    return ""


class _TextExtractor(HTMLParser):
    """태그를 걷어내고 글자만 남깁니다. script/style 안쪽은 버립니다."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
        elif tag in BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip = max(0, self._skip - 1)
        elif tag in BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def html_to_text(html_text):
    """HTML 조각을 읽기 좋은 여러 줄 글자로 바꿉니다."""
    parser = _TextExtractor()
    parser.feed(html_text or "")
    parser.close()
    text = "".join(parser.parts)
    text = text.replace("﻿", "").replace("​", "").replace("\xa0", " ")

    lines = []
    for line in text.splitlines():
        line = " ".join(line.split())
        if line:
            lines.append(line)
    return "\n".join(lines)


class _AnchorTextParser(HTMLParser):
    """<a> 태그 안의 글자를 모읍니다. 첨부파일 이름을 찾는 데 씁니다."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.texts = []
        self.hrefs = []
        self._depth = 0
        self._buf = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
        elif tag == "a":
            self._depth += 1
            self._buf = []
            self.hrefs.append((dict(attrs).get("href") or ""))

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip = max(0, self._skip - 1)
        elif tag == "a" and self._depth:
            self._depth -= 1
            self.texts.append(clean_inline("".join(self._buf)))
            self._buf = []

    def handle_data(self, data):
        if self._depth and not self._skip:
            self._buf.append(data)


def find_attachments(html_text):
    """첨부파일 이름들을 찾습니다. 본문이 비었을 때 이유를 알려주려고 씁니다.

    링크에 보이는 글자에서 이름을 얻습니다. 주소(href)에서 캐내지 않는 이유가
    두 가지입니다.
      1) 서강대는 주소를 퍼센트 인코딩해서 '%EC%9E%A5%ED%95%99' 처럼 보냅니다.
      2) 파일 이름에는 띄어쓰기와 쉼표가 들어갑니다. 주소에서 잘라내면
         '공고.hwp' 처럼 뒷토막만 남습니다.
    링크 글자에는 사람이 읽는 온전한 이름이 그대로 들어 있습니다.
    """
    parser = _AnchorTextParser()
    parser.feed(html_text or "")
    parser.close()

    names = []
    for text in parser.texts:
        match = ATTACH_EXT_RE.search(text)
        # 확장자가 글자 맨 끝에 와야 파일 이름입니다.
        # ('첨부파일 안내.hwp 를 확인하세요' 같은 문장을 걸러냅니다)
        if not match or match.end() != len(text):
            continue
        if text not in names and len(text) < 120:
            names.append(text)

    if names:
        return names[:5]

    # 링크 글자에서 못 찾으면 주소에서라도 찾아봅니다.
    for href in parser.hrefs:
        candidate = urllib.parse.unquote(href).rsplit("/", 1)[-1]
        candidate = candidate.split("?")[0].split("&")[0].strip()
        match = ATTACH_EXT_RE.search(candidate)
        if match and match.end() == len(candidate) and candidate not in names:
            names.append(candidate)
    return names[:5]


# ---------------------------------------------------------------------------
# 서강대 CMS 목록
# ---------------------------------------------------------------------------
class SogangListParser(HTMLParser):
    """서강대 로스쿨 게시판 목록에서 글 줄을 뽑습니다."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows = []
        self._pinned_zone = False
        self._in_title = False
        self._title_buf = []
        self._pending = None
        self._info_depth = 0
        self._in_span = False
        self._span_buf = []
        self._spans = []

    def handle_comment(self, data):
        # 제목 중복 주석은 여기로 빠지므로 handle_data 를 오염시키지 않습니다.
        # 우리가 볼 것은 고정글 구역 표시뿐입니다.
        text = (data or "").strip()
        if text.startswith("상단고정 시작"):
            self._pinned_zone = True
        elif text.startswith("상단고정 끝"):
            self._pinned_zone = False

    def handle_starttag(self, tag, attrs):
        attr = dict(attrs)
        if tag == "a":
            href = attr.get("href") or ""
            if "cmsboardview.do" in href:
                post_id = qs_value(href, "pkid")
                if post_id:
                    self._in_title = True
                    self._title_buf = []
                    self._pending = {"post_id": post_id,
                                     "pinned": self._pinned_zone}
        elif tag == "div":
            if self._info_depth:
                self._info_depth += 1
            elif self._pending and "info" in (attr.get("class") or "").split():
                self._info_depth = 1
                self._spans = []
        elif tag == "span" and self._info_depth:
            self._in_span = True
            self._span_buf = []

    def handle_data(self, data):
        if self._in_title:
            self._title_buf.append(data)
        elif self._in_span:
            self._span_buf.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self._in_title:
            self._in_title = False
            if self._pending is not None:
                self._pending["title"] = clean_inline("".join(self._title_buf))
        elif tag == "span" and self._in_span:
            self._in_span = False
            self._spans.append(clean_inline("".join(self._span_buf)))
        elif tag == "div" and self._info_depth:
            self._info_depth -= 1
            if self._info_depth == 0:
                self._finish_row()

    def _finish_row(self):
        row, self._pending = self._pending, None
        if not row or not row.get("title"):
            return
        row["date"] = next((d for d in (normalize_date(s) for s in self._spans) if d), "")
        # 제목이 '[공지]' 로 시작해도 고정글입니다. 주석 표시와 함께 봅니다.
        if NOTICE_PREFIX_RE.match(row["title"]):
            row["pinned"] = True
        row["title"] = strip_notice_prefix(row["title"])
        row["truncated"] = row["title"].endswith("...")
        self.rows.append(row)


# ---------------------------------------------------------------------------
# 유웨이(LEET) 목록
# ---------------------------------------------------------------------------
class UwayListParser(HTMLParser):
    """LEET 공지사항 목록에서 글 줄을 뽑습니다.

    <a> 가 닫히지 않으므로 칸(<td>) 단위로 글자를 모읍니다.
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows = []
        self._reset_row()
        self._in_cell = False
        self._buf = []

    def _reset_row(self):
        self._cells = []
        self._post_id = None
        self._title_cell = None
        self._pinned = False

    def handle_starttag(self, tag, attrs):
        attr = dict(attrs)
        if tag == "tr":
            self._reset_row()
            self._in_cell = False
        elif tag in ("td", "th"):
            self._in_cell = True
            self._buf = []
        elif tag == "a":
            href = attr.get("href") or ""
            if "BoardView.htm" in href and self._post_id is None:
                post_id = qs_value(href, "board_seqnum")
                if post_id:
                    self._post_id = post_id
                    self._title_cell = len(self._cells)
        elif tag == "img":
            if (attr.get("alt") or "").strip() == "공지":
                self._pinned = True

    def handle_data(self, data):
        if self._in_cell:
            self._buf.append(data)

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._in_cell:
            self._in_cell = False
            self._cells.append(clean_inline("".join(self._buf)))
        elif tag == "tr":
            self._finish_row()

    def _finish_row(self):
        if self._post_id and self._title_cell is not None:
            title = ""
            if self._title_cell < len(self._cells):
                title = self._cells[self._title_cell]
            date = next((d for d in (normalize_date(c) for c in self._cells) if d), "")
            if title:
                self.rows.append({
                    "post_id": self._post_id,
                    "title": strip_notice_prefix(title),
                    "date": date,
                    "pinned": self._pinned,
                    # 목록의 제목은 '...' 으로 잘려 있습니다. 상세에서 채워야 합니다.
                    "truncated": title.endswith("..."),
                })
        self._reset_row()


# ---------------------------------------------------------------------------
# 게시판 종류별 주소·파서 연결
# ---------------------------------------------------------------------------
def list_url(board):
    if board["parser"] == "sogang_cms":
        return (f"{SOGANG_BASE}/cmsboardlist.do"
                f"?siteId=lawschool&bbsConfigFK={board['config_fk']}")
    if board["parser"] == "uway":
        return f"{UWAY_BASE}/BoardList.htm?board_id={board['board_id']}"
    raise ValueError(f"모르는 게시판 종류입니다: {board['parser']}")


def view_url(board, post_id):
    """글 하나의 주소. 목록 위치가 바뀌어도 그대로 열리는 형태만 씁니다."""
    if board["parser"] == "sogang_cms":
        return (f"{SOGANG_BASE}/cmsboardview.do"
                f"?bbsConfigFK={board['config_fk']}&siteId=lawschool&pkid={post_id}")
    if board["parser"] == "uway":
        return (f"{UWAY_BASE}/BoardView.htm"
                f"?board_id={board['board_id']}&board_seqnum={post_id}")
    raise ValueError(f"모르는 게시판 종류입니다: {board['parser']}")


def parse_list(board, html_text):
    """목록 HTML -> 글 줄 목록. 각 줄에 url 을 붙여서 돌려줍니다."""
    parser = (SogangListParser if board["parser"] == "sogang_cms"
              else UwayListParser)()
    parser.feed(html_text)
    parser.close()

    rows = []
    for row in parser.rows:
        row["url"] = view_url(board, row["post_id"])
        row["board_key"] = board["board_key"]
        row["board_name"] = board["name"]
        rows.append(row)
    return rows


def fetch_list(board):
    """게시판 목록을 받아서 글 줄 목록으로 돌려줍니다."""
    return parse_list(board, fetch(list_url(board)))


def fetch_detail(board, post_id):
    """글 하나의 전체 제목과 본문을 받아옵니다.

    돌려주는 값: {"title", "body", "attachments", "url"}
    목록의 제목이 '...' 으로 잘려 있어도 여기서 온전한 제목을 얻습니다.
    """
    url = view_url(board, post_id)
    page = fetch(url)

    if board["parser"] == "sogang_cms":
        title_html = extract_region(page, "div", "title")
        body_html = extract_region(page, "div", "post_cont")
    else:
        title_html = extract_region(page, "th", "tit")
        body_html = extract_region(page, "td", "qq")

    body = html_to_text(body_html)
    if len(body) > cfg.BODY_MAX_CHARS:
        body = body[:cfg.BODY_MAX_CHARS] + "\n…(본문이 길어 잘렸습니다)"

    return {
        "url": url,
        "title": strip_notice_prefix(clean_inline(html_to_text(title_html))),
        "body": body,
        "attachments": find_attachments(page),
    }
