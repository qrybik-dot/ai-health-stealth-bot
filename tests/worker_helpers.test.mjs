import assert from "node:assert/strict";

import {
  collectColorVoteStats,
  collectTodayVoteStats,
  isoWeekIdFromDay,
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
