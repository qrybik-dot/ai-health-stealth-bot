import assert from "node:assert/strict";
import worker from "../cloudflare/telegram-webhook-worker.js";

// These are synthetic Telegram updates with in-memory Gist and Telegram mocks.
// No production tokens, messages or user votes are touched.
const env = {
  WEBHOOK_SECRET: "test-secret",
  TELEGRAM_BOT_TOKEN: "fake-bot-token",
  TELEGRAM_CHAT_ID: "12345",
  CACHE_GIST_ID: "fake-gist",
  GIST_TOKEN: "fake-gist-token",
};
const day = "2026-10-19";
const week = "2026-W43";
const todayActions = ["why", "facts", "roast", "what15"].map((kind) => ({
  text: kind, callback_data: `${kind}:morning:${day}`,
}));
const todayKeyboard = { inline_keyboard: [
  todayActions,
  ["yes", "partial", "no"].map((vote) => ({ text: vote, callback_data: `today_vote:${day}:${vote}` })),
]};
const colorKeyboard = { inline_keyboard: [
  [{ text: "История цвета", callback_data: `color_story:${week}` }],
  ["yes", "partial", "no"].map((vote) => ({ text: vote, callback_data: `color_vote:${week}:${vote}` })),
]};
const seed = () => ({
  [day]: {
    body_battery: { mostRecentValue: 65 },
    stress: { avgStressLevel: 31 },
    sleep: { sleepTimeSeconds: 27000 },
    steps: { totalSteps: 5000 },
  },
  _weekly_state: {
    [week]: { week_id: week, name_ru: "Тестовый оттенок", hex: "#123456", rarity_level: "rare" },
  },
});

function setup() {
  let state = seed();
  const telegram = [];
  let gistWrites = 0;
  globalThis.fetch = async (url, options = {}) => {
    const address = String(url);
    const method = options.method || "GET";
    if (address === "https://api.github.com/gists/fake-gist") {
      if (method === "GET") {
        return new Response(JSON.stringify({
          files: { "cache.json": { content: JSON.stringify(state), truncated: false } },
        }), { status: 200 });
      }
      assert.equal(method, "PATCH");
      state = JSON.parse(JSON.parse(options.body).files["cache.json"].content);
      gistWrites++;
      return new Response(JSON.stringify({ ok: true }), { status: 200 });
    }
    assert.match(address, /^https:\/\/api\.telegram\.org\/botfake-bot-token\//);
    const action = address.split("/").pop();
    const body = JSON.parse(options.body);
    telegram.push({ action, body });
    return new Response(JSON.stringify({ ok: true, result: { message_id: 987 } }), { status: 200 });
  };
  return { get state() { return state; }, telegram, get gistWrites() { return gistWrites; } };
}

async function click(data, keyboard, id = 555) {
  const update = {
    update_id: 100,
    callback_query: {
      id: "unit-callback-100",
      from: { id: 12345, is_bot: false, first_name: "Test" },
      message: {
        message_id: id, date: 1, chat: { id: 12345, type: "private" },
        reply_markup: keyboard,
      },
      data,
    },
  };
  const response = await worker.fetch(new Request("https://example.test/telegram/test-secret", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(update),
  }), env, {});
  assert.equal(response.status, 200);
  const body = await response.json();
  assert.equal(body.ok, true);
  assert.equal(body.swallowed_error, undefined, JSON.stringify(body));
  return body.result;
}

for (const vote of ["yes", "partial", "no"]) {
  const test = setup();
  const result = await click(`today_vote:${day}:${vote}`, todayKeyboard);
  assert.equal(result.action, "today_vote");
  assert.equal(result.saved, true);
  assert.equal(test.state._today_votes[`${day}|12345`].vote, vote);
  assert.equal(test.gistWrites, 1);
  assert.equal(test.telegram.find((x) => x.action === "answerCallbackQuery")?.body.text, "Голос учтён");
  const edit = test.telegram.find((x) => x.action === "editMessageReplyMarkup");
  assert.equal(edit.body.message_id, 555);
  assert.deepEqual(edit.body.reply_markup.inline_keyboard[0], todayActions);
  assert.match(edit.body.reply_markup.inline_keyboard[1][0].text, /Ваш выбор/);
  // Clicking again does not overwrite the first choice.
  const again = await click(`today_vote:${day}:no`, edit.body.reply_markup);
  assert.equal(again.saved, false);
  assert.equal(test.gistWrites, 1);
  assert.equal(test.state._today_votes[`${day}|12345`].vote, vote);
}

{
  const test = setup();
  const result = await click(`color_vote:${week}:partial`, colorKeyboard);
  assert.equal(result.action, "color_vote");
  assert.equal(result.saved, true);
  const saved = Object.values(test.state._daily_votes);
  assert.equal(saved.length, 1);
  assert.equal(saved[0].vote_value, "partial");
  assert.equal(test.state._weekly_state[week].votes_by_date_chat !== undefined, true);
  const edit = test.telegram.find((x) => x.action === "editMessageReplyMarkup");
  assert.deepEqual(edit.body.reply_markup.inline_keyboard[0], colorKeyboard.inline_keyboard[0]);
  assert.match(edit.body.reply_markup.inline_keyboard[1][0].text, /Ваш выбор/);
  await click(`color_story:${week}`, edit.body.reply_markup);
  assert.match(test.telegram.find((x) => x.action === "sendMessage").body.text, /История цвета недели/);
  assert.match(test.telegram.find((x) => x.action === "sendMessage").body.text, /Тестовый оттенок/);
}

for (const action of ["why", "facts", "roast", "what15"]) {
  const test = setup();
  const result = await click(`${action}:morning:${day}`, todayKeyboard);
  assert.equal(result.action, action);
  assert.equal(test.gistWrites, 0);
  assert.equal(test.telegram.filter((x) => x.action === "sendMessage").length, 1);
  assert.equal(test.telegram.filter((x) => x.action === "answerCallbackQuery").length, 1);
}

console.log("All Garmin callback actions and feedback persistence passed");
