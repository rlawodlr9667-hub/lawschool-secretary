# -*- coding: utf-8 -*-
"""아이폰 캘린더에 직접 쓰기 위한 설정을 한 번만 해 둡니다.

    python setup_icloud.py

물어보는 것은 두 가지입니다.

1) 애플 ID  — 아이폰에서 쓰는 그 계정 (보통 이메일 주소)
2) 앱전용 암호 — 애플 ID 본체 암호가 **아닙니다**

앱전용 암호 받는 법
------------------
1. appleid.apple.com 에 접속해 로그인합니다.
2. '로그인 및 보안' -> '앱 암호'(App-Specific Passwords) 로 들어갑니다.
3. '+' 를 눌러 이름을 아무거나 적습니다. 예: 로스쿨 비서
4. 'abcd-efgh-ijkl-mnop' 처럼 생긴 16글자가 나옵니다. 그게 앱전용 암호입니다.

왜 본체 암호를 안 쓰나: 앱전용 암호는 캘린더 접근에만 쓰이고, 나중에 이것만
따로 폐기할 수 있습니다. 애플 계정 전체가 열리지 않습니다.

여기서 입력한 값은 config.json 에 저장되고, 이 파일은 저장소에 올라가지
않습니다(.gitignore). 화면에도 찍히지 않습니다.
"""

import getpass
import sys

import icloud_cal

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def ask(prompt, secret=False):
    try:
        value = getpass.getpass(prompt) if secret else input(prompt)
    except (EOFError, KeyboardInterrupt):
        print()
        return ""
    return value.strip()


def main():
    print(__doc__)
    print("-" * 60)

    config = icloud_cal.load_config()

    current_id = config.get("icloud_id") or ""
    apple_id = ask(f"애플 ID{f' [{current_id}]' if current_id else ''}: ") or current_id
    if not apple_id:
        print("애플 ID 를 입력해 주세요.")
        return 1

    print("\n앱전용 암호를 붙여넣으세요. 화면에는 보이지 않습니다.")
    print("(붙여넣기는 마우스 오른쪽 클릭 또는 Ctrl+V)")
    password = ask("앱전용 암호: ", secret=True)
    if not password:
        if config.get("icloud_password"):
            password = config["icloud_password"]
            print("  (기존에 저장된 암호를 그대로 씁니다)")
        else:
            print("앱전용 암호를 입력해 주세요.")
            return 1

    # 화면 폭 때문에 두 줄로 표시된 암호를 복사하면 공백이 딸려옵니다.
    # 애플이 보여주는 형식에는 하이픈이 들어 있는데 그건 그대로 둡니다.
    password = "".join(password.split())

    print("\n1) iCloud 에 연결해 봅니다...")
    try:
        calendars = icloud_cal.discover(apple_id, password)
    except icloud_cal.CalDAVError as exc:
        print(f"   실패: {exc}")
        return 1
    print(f"   연결됐습니다. 캘린더 {len(calendars)}개를 찾았습니다.")

    print("\n2) 어느 캘린더에 넣을까요?")
    for i, (name, _url) in enumerate(calendars, 1):
        print(f"   {i}) {name}")

    previous = (config.get("icloud_calendar_url") or "").rstrip("/")
    default = 1
    for i, (_name, url) in enumerate(calendars, 1):
        if url.rstrip("/") == previous:
            default = i

    choice = ask(f"\n번호를 고르세요 [{default}]: ") or str(default)
    try:
        index = int(choice)
        name, url = calendars[index - 1]
    except (ValueError, IndexError):
        print("   번호가 올바르지 않습니다.")
        return 1

    config.update({
        "icloud_id": apple_id,
        "icloud_password": password,
        "icloud_calendar_name": name,
        "icloud_calendar_url": url,
    })
    icloud_cal.save_config(config)
    print(f"\n3) config.json 에 저장했습니다. → [{name}]")

    print("\n4) 시험 삼아 일정을 하나 넣어 볼까요?")
    print("   ('오늘 밤 11시 55분 연결 테스트' 를 넣고, 폰에서 확인한 뒤 지우시면 됩니다)")
    if (ask("   넣어 볼까요? [y/N]: ") or "n").lower().startswith("y"):
        import nlp_date
        try:
            event = nlp_date.parse("오늘 23시 55분 연결 테스트 5분")
            icloud_cal.add_event(event, config)
            print(f"   넣었습니다: {nlp_date.describe(event)}")
            print("   아이폰 캘린더를 열어 확인해 보세요. 몇 초 안에 뜹니다.")
        except (icloud_cal.CalDAVError, nlp_date.ParseError) as exc:
            print(f"   실패: {exc}")
            return 1

    print("\n다음 단계:")
    print("    python quickadd.py --install-startup   # 부팅 때 자동 실행 등록")
    print("    python quickadd.py                     # 지금 바로 켜기 (Alt+Space)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
