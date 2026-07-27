// 로스쿨 비서 챗봇 — Cloudflare Worker
//
// 텔레그램에 낱말을 보내면 아직 안 지난 관련 공지를 찾아 돌려줍니다.
// 노트북이 꺼져 있어도 동작하고, 1초 안에 답이 오고, 돈이 들지 않습니다.
//
// 여기서 하는 일은 두 가지뿐입니다.
//   1) 오늘 날짜와 견주어 아직 유효한 글만 고르기
//   2) 낱말이 들어 있는지 맞춰보기
//
// '이 글은 언제까지 유효한가' 같은 어려운 판단은 전부 파이썬(build_index.py)이
// 미리 해서 notices.json 에 적어 둡니다. 그래야 규칙이 한 곳에만 있습니다.
// 여기에 규칙을 또 적으면 한쪽만 고쳐져서 노트북과 챗봇의 대답이 달라집니다.
//
// 답장을 보내는 방식: 텔레그램이 보낸 요청에 그대로 답하면(webhook reply)
// 봇 토큰이 필요 없습니다. 토큰을 이 Worker 에 저장하지 않아도 됩니다.

const WEEKDAYS = ["월", "화", "수", "목", "금", "토", "일"];
const MAX_RESULTS = 12;
const CACHE_SECONDS = 300;

const HELP = [
  "<b>사용법</b>",
  "",
  "낱말을 그냥 보내면 그것과 관련된 <b>아직 안 지난</b> 공지를 찾아 줘.",
  "예: <code>장학금</code> · <code>실무수습</code> · <code>접수</code> · <code>수업</code>",
  "",
  "새 공지가 올라오면 묻지 않아도 먼저 알려 주고, 매일 아침 브리핑도 가.",
].join("\n");

// --- 한국 날짜 -------------------------------------------------------------
// Worker 는 세계 어디서 돌지 모릅니다. 서버가 미국에 있으면 한국은 이미
// 내일인데 어제로 계산합니다. 그래서 9시간을 더해 한국 날짜를 만듭니다.
function seoulToday() {
  const now = new Date(Date.now() + 9 * 3600 * 1000);
  return {
    iso: now.toISOString().slice(0, 10),
    year: now.getUTCFullYear(),
    month: now.getUTCMonth() + 1,
    day: now.getUTCDate(),
  };
}

function dayInfo(iso) {
  const [y, m, d] = iso.split("-").map(Number);
  const at = Date.UTC(y, m - 1, d);
  // getUTCDay() 는 일요일이 0 입니다. 우리 표는 월요일이 0 이라 하나 밉니다.
  const weekday = (new Date(at).getUTCDay() + 6) % 7;
  return { y, m, d, weekday, at };
}

function daysBetween(fromIso, toIso) {
  return Math.round((dayInfo(toIso).at - dayInfo(fromIso).at) / 86400000);
}

// --- 글자 다루기 -----------------------------------------------------------
// 한국어는 띄어쓰기가 사람마다 다릅니다. '장학금 신청'과 '장학금신청'이
// 같은 말인데 못 찾으면 안 됩니다.
function norm(text) {
  return (text || "").replace(/\s+/g, "").toLowerCase();
}

function esc(text) {
  return (text || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

// --- 찾기 ------------------------------------------------------------------
function search(index, keyword, todayIso) {
  const words = keyword.split(/\s+/).map(norm).filter(Boolean);
  if (!words.length) return [];

  const hits = [];
  for (const post of index.posts || []) {
    if (!post.d || post.d < todayIso) continue;      // 유효기간이 지났다

    const hay = norm(post.t + " " + (post.e || []).map((e) => e[2]).join(" "));
    let score = 0;
    for (const word of words) if (hay.includes(word)) score += 1;
    if (!score) continue;

    // 오늘 이후로 가장 가까운 일정을 대표로 보여 줍니다.
    const next = (post.e || []).find((e) => e[0] >= todayIso) || null;
    hits.push({ post, score, next });
  }

  hits.sort((a, b) => {
    // 마감일을 아는 글을 먼저, 그중에서도 임박한 것부터.
    const aKnown = a.post.k ? 0 : 1;
    const bKnown = b.post.k ? 0 : 1;
    if (aKnown !== bKnown) return aKnown - bKnown;
    if (a.score !== b.score) return b.score - a.score;
    if (aKnown === 0) return (a.next ? a.next[0] : "9") < (b.next ? b.next[0] : "9") ? -1 : 1;
    return (b.post.p || "").localeCompare(a.post.p || "");   // 최신 게시순
  });

  return hits.slice(0, MAX_RESULTS);
}

// --- 답장 만들기 -----------------------------------------------------------
function deadlineLabel(iso, clock, todayIso) {
  const info = dayInfo(iso);
  const left = daysBetween(todayIso, iso);
  const head = left === 0 ? "오늘" : left > 0 ? `D-${left}` : `${-left}일 지남`;
  return `${head}  ${info.m}/${info.d}(${WEEKDAYS[info.weekday]})` +
    (clock ? ` ${clock}` : "");
}

function render(keyword, hits, today) {
  const pad = (n) => String(n).padStart(2, "0");
  const head = `현재 ${today.year}년 ${pad(today.month)}월 ${pad(today.day)}일 기준 ` +
    `'${esc(keyword)}'에 대해 `;

  if (!hits.length) {
    return head + "유효한 정보가 없어.\n\n" +
      "다른 낱말로 해 볼래? 예: 장학금, 신청, 접수, 수업, 실무수습";
  }

  const lines = [head + "유효한 정보는 다음과 같아.", ""];
  for (const hit of hits) {
    const post = hit.post;
    lines.push(`· <a href="${esc(post.u)}">${esc(post.t)}</a>`);

    let detail;
    if (post.k && hit.next) {
      detail = deadlineLabel(hit.next[0], hit.next[1], today.iso);
      if (hit.next[2]) detail += ` — ${esc(hit.next[2])}`;
    } else {
      const posted = post.p ? dayInfo(post.p) : null;
      detail = "마감일 미상 · " +
        (posted ? `${posted.m}/${posted.d} 게시` : "게시일 미상");
    }
    lines.push(`    <i>${detail} · ${esc(post.b)}</i>`);
  }
  return lines.join("\n");
}

// --- 자료 가져오기 ---------------------------------------------------------
async function loadIndex(url) {
  // 같은 파일을 매번 새로 받을 이유가 없습니다. 5분간 재사용합니다.
  const response = await fetch(url, {
    cf: { cacheTtl: CACHE_SECONDS, cacheEverything: true },
  });
  if (!response.ok) throw new Error(`색인을 받지 못했습니다 (HTTP ${response.status})`);
  return response.json();
}

// --- 본체 ------------------------------------------------------------------
function reply(chatId, text) {
  // 텔레그램이 보낸 요청에 바로 답하면 봇 토큰이 필요 없습니다.
  return new Response(
    JSON.stringify({
      method: "sendMessage",
      chat_id: chatId,
      text,
      parse_mode: "HTML",
      disable_web_page_preview: true,
    }),
    { headers: { "content-type": "application/json" } },
  );
}

export default {
  async fetch(request, env) {
    if (request.method !== "POST") {
      return new Response("로스쿨 비서 챗봇이 살아 있습니다.", { status: 200 });
    }

    // 텔레그램이 보낸 것이 맞는지 확인합니다. 이 주소를 우연히 알아낸
    // 누군가가 아무 요청이나 밀어 넣지 못하게 합니다.
    if (env.WEBHOOK_SECRET &&
        request.headers.get("X-Telegram-Bot-Api-Secret-Token") !== env.WEBHOOK_SECRET) {
      return new Response("누구세요?", { status: 401 });
    }

    let update;
    try {
      update = await request.json();
    } catch {
      return new Response("ok");
    }

    const message = update.message || update.edited_message;
    const chatId = message && message.chat && message.chat.id;
    const raw = ((message && message.text) || "").trim();
    if (!chatId || !raw) return new Response("ok");

    // 내 대화방에서 온 것만 받습니다. 봇 주소는 누구나 찾을 수 있습니다.
    if (env.CHAT_ID && String(chatId) !== String(env.CHAT_ID)) {
      return reply(chatId, "이 봇은 주인만 쓸 수 있어.");
    }

    const lower = raw.toLowerCase();
    if (lower.startsWith("/start") || lower.startsWith("/help")) {
      return reply(chatId, HELP);
    }
    if (lower.startsWith("/")) {
      return reply(chatId, "모르는 명령이야. <code>/help</code> 를 보내 봐.");
    }

    const today = seoulToday();
    try {
      const index = await loadIndex(env.INDEX_URL);
      return reply(chatId, render(raw, search(index, raw, today.iso), today));
    } catch (err) {
      return reply(chatId,
        "지금은 공지 목록을 읽지 못했어. 잠시 뒤에 다시 보내 줄래?\n" +
        `<i>${esc(String(err.message || err))}</i>`);
    }
  },
};
