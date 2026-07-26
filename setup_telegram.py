# -*- coding: utf-8 -*-
"""내 PC에서 쓸 텔레그램 설정을 한 번만 만들어 줍니다.

사용법:
    python setup_telegram.py <봇토큰>

봇토큰은 텔레그램에서 @BotFather 와 대화하면 받을 수 있습니다.
'123456789:AAF...' 처럼 생긴 긴 글자입니다.

★ 중요 ★
이 프로젝트는 daily brief(경제뉴스)와 '다른 봇'을 씁니다.
텔레그램은 봇 하나에 물어보러 올 수 있는 프로그램을 1개로 제한하기 때문에,
같은 토큰을 쓰면 두 봇이 서로의 버튼 클릭을 가로채서 조용히 사라집니다.
BotFather 에서 /newbot 으로 새 봇을 하나 더 만들어 주세요.

만들어지는 config.json 은 저장소에 올라가지 않습니다(.gitignore).
GitHub Actions 에서는 이 파일 대신 Secrets 를 씁니다.
"""

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")


def call(token, method, params=None):
    url = f"https://api.telegram.org/bot{token}/{method}"
    data = urllib.parse.urlencode(params or {}).encode("utf-8")
    req = urllib.request.Request(url, data=data)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            return {"ok": False, "description": f"HTTP {exc.code}: {body[:200]}"}
    except (urllib.error.URLError, OSError) as exc:
        return {"ok": False, "description": f"연결 실패: {exc}"}


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 1

    # 화면 폭 때문에 두 줄로 표시된 토큰을 복사하면 줄바꿈이 딸려옵니다.
    # 토큰에는 원래 공백이 없으므로 전부 지워도 안전합니다.
    token = "".join(sys.argv[1].split())

    print("1) 봇 토큰을 확인합니다...")
    result = call(token, "getMe")
    if not result.get("ok"):
        print(f"   실패: {result.get('description')}")
        print("   토큰을 다시 확인해 주세요. @BotFather 에게 /mybots 로 물어볼 수 있습니다.")
        return 1
    bot_name = result["result"].get("username", "?")
    print(f"   확인됨: @{bot_name}")

    print("\n2) 대화방 번호를 찾습니다...")
    print(f"   텔레그램에서 @{bot_name} 을(를) 찾아 대화를 열고,")
    print("   아무 말이나 한 마디 보낸 뒤 여기서 Enter 를 눌러 주세요.")
    try:
        input()
    except EOFError:
        pass

    result = call(token, "getUpdates", {"limit": 20})
    if not result.get("ok"):
        print(f"   실패: {result.get('description')}")
        return 1

    chat_id = None
    for update in reversed(result.get("result", [])):
        chat = ((update.get("message") or {}).get("chat")) or {}
        if chat.get("id"):
            chat_id = str(chat["id"])
            break

    if not chat_id:
        print("   대화 내용을 찾지 못했습니다.")
        print(f"   @{bot_name} 대화방에서 /start 를 한 번 누른 뒤 다시 실행해 주세요.")
        return 1
    print(f"   찾았습니다: {chat_id}")

    existing = {}
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as fp:
                existing = json.load(fp)
        except (OSError, json.JSONDecodeError):
            existing = {}

    existing.update({"bot_token": token, "chat_id": chat_id})
    with open(CONFIG_PATH, "w", encoding="utf-8") as fp:
        json.dump(existing, fp, ensure_ascii=False, indent=2)
        fp.write("\n")

    print(f"\n3) config.json 에 저장했습니다.")

    ok = call(token, "sendMessage", {
        "chat_id": chat_id,
        "parse_mode": "HTML",
        "text": ("<b>⚖️ 로스쿨 비서 연결 완료</b>\n\n"
                 "이제 새 공지가 올라오면 알려 드립니다.\n"
                 "<code>/menu</code> 를 보내면 탭 버튼이 나옵니다."),
    })
    if ok.get("ok"):
        print("   테스트 메시지를 보냈습니다. 텔레그램을 확인해 보세요.")
    else:
        print(f"   테스트 메시지 발송 실패: {ok.get('description')}")
        return 1

    print("\n다음 단계:")
    print("    python scrape.py --seed-only   # 지금 글을 '이미 본 것'으로 기록")
    print("    python scrape.py               # 이후부터 새 글 알림")
    return 0


if __name__ == "__main__":
    sys.exit(main())
