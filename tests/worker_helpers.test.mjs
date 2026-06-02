import assert from "node:assert/strict";

import {
  buildColorMessage,
  buildDebugHealthMessage,
  buildNoDataTodayMessage,
  buildTodayMessage,
  collectColorVoteStats,
  collectTodayVoteStats,
  buildWeekMessage,
  buildWhat15Message,
  isoWeekIdFromDay,
  routeTextQuestion,
  storeColorVote,
  storeTodayVote,
  todayKeyboard,
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

assert.deepEqual(todayKeyboard("2026-06-02", "midday").inline_keyboard[1], [
  { text: "✅ Попало", callback_data: "today_vote:2026-06-02:yes" },
  { text: "➖ Частично", callback_data: "today_vote:2026-06-02:partial" },
  { text: "❌ Мимо", callback_data: "today_vote:2026-06-02:no" },
]);

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

for (let index = 2; index <= 20; index += 1) {
  const day = new Date(`${today}T12:00:00Z`);
  day.setUTCDate(day.getUTCDate() - index);
  const key = day.toISOString().slice(0, 10);
  richCache[key] = {
    body_battery: { mostRecentValue: 40 + index, chargedValue: 55 + index },
    stress: { avgStressLevel: 60 - (index % 12) },
    sleep: { sleepTimeSeconds: 21600 + index * 240 },
    steps: { totalSteps: 2500 + index * 180 },
  };
}

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

const monthMessage = routeTextQuestion("как прошёл месяц", richCache);
assert.match(monthMessage, /Месяц/);
assert.match(monthMessage, /Лучший день/);
assert.match(monthMessage, /Сложный день/);
assert.match(monthMessage, /Фокус/);

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

const malformedCache = {
  [today]: {
    body_battery: { mostRecentValue: 79 },
    stress: { avgStressLevel: 18 },
    sleep: { sleepTimeSeconds: 26760 },
    rhr: { restingHeartRate: 129221211 },
    steps: { totalSteps: 0 },
    daily_activity: { activeSeconds: 5940 },
  },
};
const malformedToday = buildTodayMessage(malformedCache, "midday");
assert.doesNotMatch(malformedToday, /129221211/);
assert.doesNotMatch(malformedToday, /0 шагов/);

const malformedFacts = routeTextQuestion("почему нет шагов?", malformedCache);
assert.match(malformedFacts, /Почему нет шагов/);
assert.match(malformedFacts, /steps=0/);
assert.match(malformedFacts, /активность/);

const noDataToday = buildTodayMessage({}, "midday");
assert.match(noDataToday, /Данные за сегодня ещё не приехали/);
assert.match(noDataToday, /Надёжность:<\/b> нет данных/);
assert.doesNotMatch(noDataToday, /Пожар|шут|ха-ха|кофе/);
assert.equal(noDataToday, buildNoDataTodayMessage("midday"));

const healthCache = {
  [today]: {
    body_battery: { mostRecentValue: 70 },
    stress: { avgStressLevel: 31 },
    sleep: { sleepTimeSeconds: 27000 },
    rhr: { restingHeartRate: 55 },
    fetched_at_utc: "2026-06-02T06:00:00Z",
  },
  "2026-06-01": { sleep: { sleepTimeSeconds: 24000 } },
  _push_state: {
    [`${today}|chat-1|morning|verdict`]: { ts: "x" },
    "2026-06-01|chat-1|morning|verdict": { ts: "x" },
  },
  _weekly_state: { "2026-W23": { week_id: "2026-W23" } },
  _daily_votes: { [`${today}|chat-1`]: { vote_value: "yes" } },
  _today_votes: { [`${today}|chat-1`]: { vote: "partial" } },
};
const healthMessage = buildDebugHealthMessage(healthCache);
assert.match(healthMessage, /Ops health/);
assert.match(healthMessage, /today status: ready/);
assert.match(healthMessage, /today key metrics: 4\/4/);
assert.match(healthMessage, /history days: 2/);
assert.match(healthMessage, /today sent registry: 1/);
