import assert from "node:assert/strict";

import {
  buildColorMessage,
  buildTodayMessage,
  collectColorVoteStats,
  collectTodayVoteStats,
  buildWeekMessage,
  buildWhat15Message,
  isoWeekIdFromDay,
  routeTextQuestion,
  storeColorVote,
  storeTodayVote,
  votedKeyboard,
} from "../cloudflare/telegram-webhook-worker.js";

const env = { CACHE_GIST_ID: "gist-id", GIST_TOKEN: "token" };
let patchCalls = 0;

globalThis.fetch = async (url, options = {}) => {
  if (String(url).includes("/gists/") && options.method === "PATCH") {
    patchCalls += 1;
    return { ok: true, status: 200, json: async () => ({ ok: true }) };
  }
  throw new Error(`unexpected fetch: ${url}`);
};

assert.equal(isoWeekIdFromDay("2026-01-01"), "2026-W01");
assert.equal(isoWeekIdFromDay("2026-12-31"), "2026-W53");

const cache = {};
let result = await storeColorVote(env, cache, "chat-1", "2026-W10", "yes");
assert.equal(result.saved, true);
const colorVoteKey = Object.keys(cache._daily_votes)[0];
assert.ok(colorVoteKey.endsWith("|chat-1"));
assert.equal(cache._daily_votes[colorVoteKey]?.vote_value, "yes");
assert.equal(cache._weekly_state["2026-W10"].votes_by_date_chat[colorVoteKey], "yes");

result = await storeColorVote(env, cache, "chat-1", "2026-W10", "no");
assert.equal(result.saved, false);
assert.equal(result.existing, "yes");

const colorStats = collectColorVoteStats(cache, "chat-1", "2026-W10");
assert.deepEqual(
  {
    yes_count: colorStats.yes_count,
    partial_count: colorStats.partial_count,
    no_count: colorStats.no_count,
    total: colorStats.total,
  },
  { yes_count: 1, partial_count: 0, no_count: 0, total: 1 }
);

result = await storeTodayVote(env, cache, "chat-1", "2026-03-08", "partial");
assert.equal(result.saved, true);
assert.equal(cache._today_votes["2026-03-08|chat-1"]?.vote, "partial");
assert.equal(cache._today_state["2026-03-08|chat-1"]?.week_id, "2026-W10");

const todayStats = collectTodayVoteStats(cache, "chat-1", "2026-W10");
assert.equal(todayStats.partial_count, 1);
assert.equal(todayStats.total, 1);

assert.deepEqual(votedKeyboard("no"), {
  inline_keyboard: [[{ text: "🗳 Ваш выбор: ❌ Мимо", callback_data: "noop" }]],
});

assert.equal(patchCalls, 2);

const today = new Intl.DateTimeFormat("en-CA", {
  timeZone: "Europe/Moscow",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
}).format(new Date());
const yesterday = new Date(`${today}T12:00:00Z`);
yesterday.setUTCDate(yesterday.getUTCDate() - 1);
const yesterdayKey = yesterday.toISOString().slice(0, 10);

const richCache = {
  [yesterdayKey]: {
    body_battery: { mostRecentValue: 32, chargedValue: 48 },
    stress: { avgStressLevel: 61 },
    sleep: { sleepTimeSeconds: 19800 },
    steps: { totalSteps: 3200 },
  },
  [today]: {
    body_battery: { mostRecentValue: 72, chargedValue: 82 },
    stress: { avgStressLevel: 34 },
    sleep: { sleepTimeSeconds: 27000 },
    rhr: { restingHeartRate: 55 },
    steps: { totalSteps: 9100 },
  },
  _weekly_state: {
    "2026-W10": { name_ru: "мягкий зелёный", hex: "#2FAE68" },
  },
};

const colorMessage = buildColorMessage(richCache);
assert.match(colorMessage, /Цвет дня/);
assert.match(colorMessage, /ресурс|стресс|движения/);
assert.doesNotMatch(colorMessage, /пока не сохран/);

const weekMessage = buildWeekMessage(richCache);
assert.match(weekMessage, /Вердикт недели/);
assert.match(weekMessage, /Лучший день/);
assert.match(weekMessage, /Сложный день/);
assert.match(weekMessage, /Фокус/);

const compareMessage = routeTextQuestion("сравни сегодня со вчера", richCache);
assert.match(compareMessage, /Сравнение/);
assert.match(compareMessage, new RegExp(today));
assert.match(compareMessage, new RegExp(yesterdayKey));

const foodMessage = routeTextQuestion("что мне лучше поесть", richCache);
assert.match(foodMessage, /Еда сейчас/);
assert.doesNotMatch(foodMessage, /Сравнение/);

const what15 = buildWhat15Message("midday", richCache[yesterdayKey]);
assert.match(what15, /тишина|экран|ходьба/);

const morningToday = buildTodayMessage(richCache, "morning");
const middayToday = buildTodayMessage(richCache, "midday");
const eveningToday = buildTodayMessage(richCache, "evening");
assert.match(morningToday, /восстановление после сна/);
assert.match(morningToday, /стартовый BB|сон/);
assert.match(middayToday, /короткая коррекция курса/);
assert.match(middayToday, /с утра|стресс|шагов/);
assert.match(eveningToday, /подготовка восстановления/);

const stressedCache = {
  [today]: {
    body_battery: { mostRecentValue: 48, chargedValue: 78 },
    stress: { avgStressLevel: 68, maxStressLevel: 92 },
    steps: { totalSteps: 1400 },
  },
};
assert.match(buildTodayMessage(stressedCache, "midday"), /7 минут без экрана|коррекц/);
assert.match(buildTodayMessage(stressedCache, "evening"), /не тащить дневной стресс|приглушить/);
