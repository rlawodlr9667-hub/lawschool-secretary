# 챗봇 켜기 (15분)

텔레그램에 `장학금` 이라고 보내면 아직 안 지난 관련 공지를 찾아 주는 기능입니다.

전부 웹사이트에서 클릭으로 하는 일입니다. 순서대로 따라 하시면 됩니다.

---

## 왜 이렇게 하나요

세 가지를 다 만족해야 했습니다. **노트북을 꺼도 되고, 즉시 답이 오고, 돈이 안 들 것.**

- **GitHub Actions** 로는 안 됩니다. 예약 실행이 자주 걸러져서 몇 시간씩 답이 없습니다.
- **노트북에서 켜 두는 방식**도 안 됩니다. 노트북을 꺼야 하니까요.
- 그래서 **Cloudflare Workers** 를 씁니다. 항상 켜져 있고, 요청이 올 때만 잠깐
  돌고, 하루 10만 번까지 공짜입니다. 신용카드도 필요 없습니다.
  우리가 쓸 양은 하루 몇십 번이라 한도의 0.1% 도 안 됩니다.

어려운 판단(마감일이 언제인지)은 파이썬이 미리 해서 `docs/notices.json` 에
적어 둡니다. 챗봇은 그 파일을 읽어 오늘 날짜와 견주기만 합니다.

---

## A. 대화방 번호와 봇 토큰 준비 (1분)

이미 갖고 계신 값입니다.

- **대화방 번호**: `6802358211`
- **봇 토큰**: BotFather 에게 받은 그 값. GitHub Secrets 에 넣으신 것과 같습니다.
  모르겠으면 텔레그램에서 `@BotFather` → `/mybots` → 봇 선택 → **API Token**

> ⚠️ 봇 토큰은 채팅창에 붙여넣지 마세요. 아래 D 단계에서 주소창에만 씁니다.

**비밀 열쇠말**을 하나 정하세요. 아무 영문·숫자나 20자 정도면 됩니다.
예: `lawsecret2026abcd`. 이건 텔레그램이 보낸 요청이 맞는지 확인하는 데 씁니다.
메모장에 적어 두세요.

---

## B. Cloudflare 가입 (3분)

1. [dash.cloudflare.com/sign-up](https://dash.cloudflare.com/sign-up) 에서 가입합니다.
   이메일과 비밀번호만 있으면 됩니다. **카드 등록 화면은 나오지 않습니다.**
2. 메일로 온 링크를 눌러 인증합니다.

---

## C. Worker 만들기 (5분)

1. 왼쪽 메뉴에서 **Compute (Workers)** → **Workers & Pages** 로 갑니다.
2. **Create** → **Start with Hello World!** → **Get started** 를 누릅니다.
3. 이름을 `lawschool-bot` 으로 하고 **Deploy** 를 누릅니다.
   (한 번 배포해야 다음 화면이 열립니다. 내용은 곧 바꿉니다)
4. **Edit code** 를 누릅니다. 코드 편집기가 열립니다.
5. 왼쪽에 있던 내용을 **전부 지우고**, 이 저장소의 [`worker/bot.js`](bot.js)
   내용을 통째로 붙여넣습니다.
   → https://github.com/rlawodlr9667-hub/lawschool-secretary/blob/main/worker/bot.js
     에서 오른쪽 위 복사 단추를 누르면 편합니다.
6. 오른쪽 위 **Deploy** 를 누릅니다.
7. 주소가 나옵니다. `https://lawschool-bot.<계정이름>.workers.dev`
   **이 주소를 메모장에 복사해 두세요.** D 단계에서 씁니다.

### C-2. 설정값 3개 넣기

Worker 화면에서 **Settings** → **Variables and Secrets** 로 갑니다.
**+ Add** 를 눌러 아래 3개를 하나씩 넣습니다.

| Type | Name | Value |
|---|---|---|
| Text | `INDEX_URL` | `https://rlawodlr9667-hub.github.io/lawschool-secretary/notices.json` |
| Text | `CHAT_ID` | `6802358211` |
| Secret | `WEBHOOK_SECRET` | A 단계에서 정한 비밀 열쇠말 |

다 넣고 **Deploy** 를 눌러 저장합니다.

> `CHAT_ID` 를 넣는 이유: 봇 주소는 누구나 찾아낼 수 있습니다. 이걸 넣어 두면
> 다른 사람이 말을 걸어도 "주인만 쓸 수 있어"라고만 답합니다.

---

## D. 텔레그램에게 주소 알려주기 (2분)

브라우저 주소창에 아래 주소를 **한 줄로** 만들어 넣고 엔터를 칩니다.
`<봇토큰>`, `<Worker주소>`, `<비밀열쇠말>` 세 군데를 바꿔야 합니다.

```
https://api.telegram.org/bot<봇토큰>/setWebhook?url=<Worker주소>&secret_token=<비밀열쇠말>
```

예시 (값은 본인 것으로):

```
https://api.telegram.org/bot123456:AAF.../setWebhook?url=https://lawschool-bot.abc.workers.dev&secret_token=lawsecret2026abcd
```

화면에 이렇게 나오면 성공입니다:

```json
{"ok":true,"result":true,"description":"Webhook was set"}
```

---

## E. 확인 (1분)

텔레그램에서 봇에게 **`장학금`** 이라고 보내 보세요. 1초 안에 이렇게 옵니다.

```
현재 2026년 07월 27일 기준 '장학금'에 대해 유효한 정보는 다음과 같아.

· 2026학년도 2학기 법학전문대학원 장학금 신청 안내
   오늘  7/27(월) — 2학기 장학금 신청 시작 · 학사공지
```

`/help` 를 보내면 사용법이 옵니다.

---

## 문제가 생겼을 때

**아무 답이 없습니다**
→ 주소창에 `https://api.telegram.org/bot<봇토큰>/getWebhookInfo` 를 넣어 보세요.
   `last_error_message` 에 이유가 적혀 있습니다.
→ `url` 이 비어 있으면 D 단계가 안 된 것입니다.

**"이 봇은 주인만 쓸 수 있어" 라고만 옵니다**
→ `CHAT_ID` 값이 틀렸습니다. `6802358211` 이 맞는지 확인해 주세요.

**"지금은 공지 목록을 읽지 못했어" 라고 옵니다**
→ `INDEX_URL` 을 브라우저에서 직접 열어 보세요. `{"generated":...` 로 시작하는
   글이 보여야 합니다. 404 가 나오면 아직 `notices.json` 이 올라가지 않은 것이니
   저장소 Actions 에서 **공지 수집** 을 한 번 돌려 주세요.

**검색 결과가 옛날 것 같습니다**
→ 챗봇은 파일을 5분간 재사용합니다. 5분 뒤에 다시 물어봐 주세요.

---

## 끄고 싶을 때

주소창에 `https://api.telegram.org/bot<봇토큰>/deleteWebhook` 를 넣으면
챗봇이 멈춥니다. Cloudflare 쪽은 그대로 둬도 요금이 붙지 않습니다.
