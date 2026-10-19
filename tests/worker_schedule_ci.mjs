import assert from "node:assert/strict";
import worker from "../cloudflare/telegram-webhook-worker.js";

const cronCases = [
  ["30 6 * * *", "30 9 * * *"],
  ["7 11 * * *", "7 14 * * *"],
  ["7 17 * * *", "7 20 * * *"],
];
const dispatched = [];
globalThis.fetch = async (url, options) => {
  assert.equal(url, "https://api.github.com/repos/qrybik-dot/ai-health-stealth-bot/actions/workflows/push.yml/dispatches");
  assert.equal(options.method, "POST");
  assert.equal(options.headers.authorization, "Bearer test-secret");
  dispatched.push(JSON.parse(options.body));
  return { status: 204 };
};
for (const [utcCron, moscowCron] of cronCases) {
  await worker.scheduled({ cron: utcCron }, {
    GITHUB_DISPATCH_TOKEN: "test-secret",
    GITHUB_REPO: "qrybik-dot/ai-health-stealth-bot",
  });
  assert.deepEqual(dispatched.at(-1), {
    ref: "main", inputs: { schedule_cron: moscowCron },
  });
}
assert.equal(dispatched.length, 3);
await assert.rejects(
  worker.scheduled({ cron: "0 0 * * *" }, { GITHUB_DISPATCH_TOKEN: "test-secret" }),
  /Unknown scheduled push cron/,
);
await assert.rejects(
  worker.scheduled({ cron: cronCases[0][0] }, {}),
  /GITHUB_DISPATCH_TOKEN missing/,
);
await assert.rejects(
  worker.scheduled({ cron: cronCases[0][0] }, { GITHUB_DISPATCH_TOKEN: "test-secret", GITHUB_REPO: "other/repo" }),
  /Unexpected GitHub repository/,
);
globalThis.fetch = async () => ({ status: 403 });
await assert.rejects(
  worker.scheduled({ cron: cronCases[0][0] }, { GITHUB_DISPATCH_TOKEN: "test-secret" }),
  /HTTP 403/,
);
console.log("worker schedule dispatch tests OK");
