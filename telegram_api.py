# -*- coding: utf-8 -*-
"""텔레그램 발송 도구 모음.

daily brief 프로젝트의 send_telegram.py 에서 검증된 부분만 옮겼습니다.
동작이 이미 확인된 코드이므로 손대지 않는 것이 원칙입니다.

이 파일은 혼자서는 아무것도 하지 않습니다. scrape.py / bot_server.py /
remind.py 가 가져다 씁니다.
"""

import html as html_mod
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")

# 텔레그램은 메시지 1건에 4096자까지 허용합니다.
# HTML 태그도 글자 수에 포함되므로 여유를 두고 자릅니다.
TELEGRAM_LIMIT = 4096
SAFE_LIMIT = 3800


# ---------------------------------------------------------------------------
# 설정 읽기
# ---------------------------------------------------------------------------
def load_config():
    """봇 토큰과 chat_id를 읽습니다.

    두 곳에서 찾습니다.
      1) 환경변수 TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID  (클라우드 실행용)
      2) config.json                                     (내 PC 실행용)

    GitHub Actions 같은 클라우드에는 config.json 을 올릴 수 없습니다.
    (토큰이 들어 있어서 저장소에 올리면 안 됩니다) 그래서 환경변수를 먼저 봅니다.

    주의: 이 프로젝트는 daily brief 와 '다른 봇'을 씁니다. 텔레그램은 봇 하나에
    붙을 수 있는 수신 프로그램을 1개로 제한하기 때문입니다. 토큰을 헷갈리면
    두 봇이 서로의 버튼 클릭을 잡아먹습니다.
    """
    env_token = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
    env_chat = (os.environ.get("TELEGRAM_CHAT_ID") or "").strip()
    if env_token and env_chat:
        return env_token, env_chat

    if not os.path.exists(CONFIG_PATH):
        raise SystemExit(
            "텔레그램 설정을 찾을 수 없습니다.\n"
            "  - 내 PC에서 쓸 때는:  python setup_telegram.py <봇토큰>\n"
            "  - 클라우드에서 쓸 때는: TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 환경변수\n"
        )
    with open(CONFIG_PATH, "r", encoding="utf-8") as fp:
        cfg = json.load(fp)

    token = (cfg.get("bot_token") or "").strip()
    chat_id = str(cfg.get("chat_id") or "").strip()
    if not token or not chat_id:
        raise SystemExit(
            "config.json에 bot_token 또는 chat_id가 비어 있습니다.\n"
            "    python setup_telegram.py 를 다시 실행해 주세요.\n"
        )
    return token, chat_id


# ---------------------------------------------------------------------------
# 마크다운 -> 텔레그램 HTML
# ---------------------------------------------------------------------------
# 텔레그램은 MarkdownV2도 지원하지만 . ! - ( ) 같은 흔한 글자까지
# 전부 역슬래시로 감싸야 해서 실수가 나기 쉽습니다. HTML 방식이 훨씬 안전합니다.
# 쓸 수 있는 태그: <b> <i> <u> <s> <code> <pre> <a href="">

LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^\s)]+)\)")
BOLD_RE = re.compile(r"\*\*([^*]+)\*\*")
ITALIC_RE = re.compile(r"(?<![\w*])\*([^*\n]+)\*(?![\w*])")
HEADING_RE = re.compile(r"^(#{1,6})\s*(.+)$")
QUOTE_RE = re.compile(r"^>\s*(.+)$")
BULLET_RE = re.compile(r"^(\s*)[-*+]\s+(.+)$")
HR_RE = re.compile(r"^\s*([-*_])\1{2,}\s*$")


def line_to_html(line):
    """한 줄을 텔레그램 HTML로 바꿉니다."""
    if HR_RE.match(line):
        return "—" * 12          # 구분선

    # 1) 먼저 원문 상태에서 줄의 '종류'를 판별합니다.
    #    escape 를 먼저 하면 인용문 표시 > 가 &gt; 로 바뀌어
    #    인용문 규칙이 영원히 매칭되지 않습니다.
    raw = line
    heading_level = 0
    quote = False
    bullet_indent = None

    m = HEADING_RE.match(raw)
    if m:
        heading_level = len(m.group(1))
        raw = m.group(2)

    m = QUOTE_RE.match(raw)
    if m:
        raw = m.group(1)
        quote = True

    m = BULLET_RE.match(raw)
    if m:
        bullet_indent = m.group(1)
        raw = m.group(2)

    # 2) 이제 본문만 escape 합니다. 이 순서를 지켜야
    #    아래에서 넣는 <b> 같은 태그가 escape 되지 않습니다.
    text = html_mod.escape(raw, quote=False)

    # 3) 인라인 서식. 링크를 먼저 처리해야 URL 안의 * 가 기울임으로 오인되지 않습니다.
    text = LINK_RE.sub(lambda mm: f'<a href="{mm.group(2)}">{mm.group(1)}</a>', text)
    text = BOLD_RE.sub(lambda mm: f"<b>{mm.group(1)}</b>", text)
    text = ITALIC_RE.sub(lambda mm: f"<i>{mm.group(1)}</i>", text)

    if heading_level == 2:
        text = f"<b>▌{text}</b>"
    elif heading_level:
        text = f"<b>{text}</b>"

    if quote:
        marker = "" if text[:1] in ("💡", "📌", "⚠") else "💡 "
        text = f"{marker}<i>{text}</i>"
    if bullet_indent is not None:
        text = f"{bullet_indent}• {text}"

    return text


def markdown_to_html(md_text):
    """마크다운 전체를 줄 단위로 변환합니다."""
    return "\n".join(line_to_html(line) for line in md_text.splitlines())


def html_len(md_text):
    """변환 후 길이. 텔레그램 한도는 변환된 HTML 기준으로 셉니다."""
    return len(markdown_to_html(md_text))


# ---------------------------------------------------------------------------
# 메시지 쪼개기
# ---------------------------------------------------------------------------
def split_blocks(md_text):
    """'## 섹션' 단위 덩어리로 나눕니다.

    변환 전(마크다운 상태)에서 나누는 것이 중요합니다.
    변환 후 HTML을 글자 수로 자르면 <b> 태그가 중간에 끊겨
    텔레그램이 메시지 전체를 거부합니다.
    """
    lines = md_text.splitlines()
    blocks = []
    current = []

    for line in lines:
        if line.startswith("## ") and current:
            blocks.append("\n".join(current).strip())
            current = [line]
        else:
            current.append(line)
    if current:
        blocks.append("\n".join(current).strip())

    return [b for b in blocks if b]


def hard_wrap(text, limit):
    """어떤 기준으로도 나눌 수 없는 아주 긴 한 줄을 강제로 자릅니다.

    글자 수로 단순히 자를 수 없습니다. escape 때문에 & 한 글자가 &amp; 다섯 글자로
    늘어나므로, 실제 변환 길이를 재면서 줄여 나갑니다.
    """
    chunks = []
    rest = text
    while rest:
        take = len(rest)
        while take > 1:
            grown = html_len(rest[:take])
            if grown <= limit:
                break
            take = max(1, int(take * limit / grown) - 1)
        chunks.append(rest[:take])
        rest = rest[take:]
    return chunks


def to_atoms(md_text, limit):
    """'더 이상 쪼갤 필요 없는 조각' 목록으로 만듭니다.

    각 조각은 혼자서 반드시 한도 안에 들어갑니다. 그래서 뒤에서 조각을
    이어 붙이기만 하면 되고, 내용이 유실될 여지가 없습니다.
    """
    atoms = []
    for section in split_blocks(md_text):
        if html_len(section) <= limit:
            atoms.append(("\n\n", section))
            continue

        for para in section.split("\n\n"):
            if not para.strip():
                continue
            if html_len(para) <= limit:
                atoms.append(("\n\n", para))
                continue

            for line in para.splitlines():
                if html_len(line) <= limit:
                    atoms.append(("\n", line))
                    continue
                for chunk in hard_wrap(line, limit):
                    atoms.append(("\n", chunk))

    return atoms


def pack_messages(md_text, limit=SAFE_LIMIT):
    """마크다운을 텔레그램 메시지 여러 건으로 나눕니다."""
    messages = []
    buffer = ""

    for sep, text in to_atoms(md_text, limit):
        if not buffer:
            buffer = text
            continue

        candidate = f"{buffer}{sep}{text}"
        if html_len(candidate) > limit:
            messages.append(buffer.strip())
            buffer = text
        else:
            buffer = candidate

    if buffer.strip():
        messages.append(buffer.strip())
    return messages


# ---------------------------------------------------------------------------
# 실제 발송
# ---------------------------------------------------------------------------
def api_request(token, method, params, timeout=30):
    """텔레그램 Bot API를 호출하고 결과(dict)를 돌려줍니다."""
    url = f"https://api.telegram.org/bot{token}/{method}"
    data = urllib.parse.urlencode(params).encode("utf-8")
    req = urllib.request.Request(url, data=data)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        # 텔레그램은 오류 이유를 본문에 담아 보냅니다. 그게 훨씬 유용합니다.
        body = exc.read().decode("utf-8", errors="replace")
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            return {"ok": False, "description": f"HTTP {exc.code}: {body[:200]}"}
    except (urllib.error.URLError, OSError) as exc:
        return {"ok": False, "description": f"{type(exc).__name__}: {exc}"}


def send_message(token, chat_id, html_text, retries=3, reply_markup=None):
    """메시지 1건을 보냅니다. 실패하면 이유를 알려 줍니다."""
    params = {
        "chat_id": chat_id,
        "text": html_text,
        "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    }
    if reply_markup is not None:
        # 텔레그램은 이 항목만 JSON 문자열로 받습니다.
        params["reply_markup"] = json.dumps(reply_markup, ensure_ascii=False)

    for attempt in range(1, retries + 1):
        result = api_request(token, "sendMessage", params)
        if result.get("ok"):
            return True, ""

        desc = result.get("description", "알 수 없는 오류")

        # 너무 빨리 보내면 텔레그램이 잠깐 기다리라고 알려 줍니다.
        retry_after = (result.get("parameters") or {}).get("retry_after")
        if retry_after and attempt < retries:
            print(f"    (텔레그램 요청 제한: {retry_after}초 대기)")
            time.sleep(float(retry_after) + 1)
            continue

        # HTML 태그 문제라면 서식을 포기하고 순수 텍스트로라도 보냅니다.
        if "can't parse entities" in desc.lower() and attempt < retries:
            print(f"    (서식 오류 -> 일반 텍스트로 재시도: {desc})")
            params.pop("parse_mode", None)
            plain = re.sub(r"<[^>]+>", "", html_text)
            params["text"] = html_mod.unescape(plain)
            continue

        if attempt < retries:
            time.sleep(2)
            continue
        return False, desc

    return False, "재시도 횟수를 모두 소진했습니다."


def send_markdown(token, chat_id, md_text, prefix_index=True):
    """긴 마크다운을 알아서 나눠 보냅니다. 전부 성공하면 True."""
    messages = pack_messages(md_text)
    total = len(messages)
    all_ok = True
    for idx, msg in enumerate(messages, 1):
        body = markdown_to_html(msg)
        if total > 1 and prefix_index:
            body = f"<b>[{idx}/{total}]</b>\n{body}"
        ok, err = send_message(token, chat_id, body)
        if not ok:
            print(f"    발송 실패 ({idx}/{total}): {err}")
            all_ok = False
        if idx < total:
            time.sleep(1.2)
    return all_ok
