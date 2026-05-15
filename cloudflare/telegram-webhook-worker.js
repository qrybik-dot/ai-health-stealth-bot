const MSK_TZ = "Europe/Moscow";
const KEY_METRICS = ["sleep", "body_battery", "rhr", "stress"];
const METRIC_LABELS = {
  sleep: "сон",
  body_battery: "Body Battery",
  rhr: "RHR",
  stress: "стресс",
  steps: "шаги",
};

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    if (request.method === "GET" && url.pathname === "/health") {
      return json({
        ok: true,
        runtime: "cloudflare-worker",
        has_webhook_secret: Boolean(env.WEBHOOK_SECRET),
        has_cache: Boolean(env.CACHE_GIST_ID && env.GIST_TOKEN),
      });
    }
    if (!env.WEBHOOK_SECRET || request.method !== "POST" || url.pathname !== `/telegram/${env.WEBHOOK_SECRET}`) {
      return new Response("not found", { status: 404 });
    }

    let update;
    try {
      update = await request.json();
    } catch (_err) {
      return json({ ok: false, error: "bad_json" }, 400);
    }

    try {
      const result = await processTelegramUpdate(update, env, ctx);
      console.log(JSON.stringify({
        event: "telegram_update_processed",
        update_id: update.update_id || "",
        action: result.action || "",
        chat_id: result.chat_id || "",
      }));
      return json({ ok: true, result });
    } catch (err) {
      console.error(JSON.stringify({ event: "telegram_update_failed", error: String(err) }));
      return json({ ok: true, swallowed_error: String(err) });
    }
  },
};

async function processTelegramUpdate(update, env, ctx) {
  const callback = update.callback_query;
  if (callback) {
    return handleCallback(callback, env, ctx);
  }
  const message = update.message;
  if (!message || !message.text) {
    return { action: "ignored" };
  }
  return handleMessage(message, env, ctx);
}

async function handleMessage(message, env, ctx) {
  const chatId = String(message.chat?.id || env.TELEGRAM_CHAT_ID || "");
  const text = String(message.text || "").trim();
  const lower = text.toLowerCase();

  if (!isAllowedChat(env, chatId)) {
    console.warn(JSON.stringify({ event: "telegram_chat_rejected", chat_id: chatId }));
    return { action: "rejected", chat_id: chatId };
  }

  if (lower === "/help" || lower === "/start") {
    await sendMessage(env, chatId, helpMessage());
    return { action: "help", chat_id: chatId };
  }
  if (lower === "/today") {
    const cache = await loadCache(env);
    await sendMessage(env, chatId, buildTodayMessage(cache), todayKeyboard(currentDayKey()));
    return { action: "today", chat_id: chatId };
  }
  if (lower === "/color") {
    const cache = await loadCache(env);
    await sendMessage(env, chatId, buildColorMessage(cache));
    return { action: "color", chat_id: chatId };
  }
  if (lower === "/stats") {
    const cache = await loadCache(env);
    await sendMessage(env, chatId, buildStatsMessage(cache, chatId));
    return { action: "stats", chat_id: chatId };
  }
  if (lower === "/week") {
    const cache = await loadCache(env);
    await sendMessage(env, chatId, buildWeekMessage(cache));
    return { action: "week", chat_id: chatId };
  }
  if (lower === "/debug_sync") {
    const cache = await loadCache(env);
    await sendMessage(env, chatId, buildDebugSyncMessage(cache));
    return { action: "debug_sync", chat_id: chatId };
  }
  if (lower === "/debug_sent") {
    const cache = await loadCache(env);
    await sendMessage(env, chatId, buildDebugSentMessage(cache, chatId));
    return { action: "debug_sent", chat_id: chatId };
  }
  if (lower === "/refresh") {
    const started = await triggerRecoverySync(env);
    await sendMessage(
      env,
      chatId,
      started
        ? "Запустил обновление данных. Итог появится после ближайшего sync."
        : "Refresh сейчас доступен через GitHub Actions -> Recovery Controls -> sync."
    );
    return { action: "refresh", chat_id: chatId };
  }

  const cache = await loadCache(env);
  await sendMessage(env, chatId, routeTextQuestion(text, cache));
  return { action: "structured_reply", chat_id: chatId };
}

async function handleCallback(callback, env, _ctx) {
  const chatId = String(callback.message?.chat?.id || env.TELEGRAM_CHAT_ID || "");
  const data = String(callback.data || "");

  if (!isAllowedChat(env, chatId)) {
    await answerCallback(env, callback.id, "Недоступно");
    console.warn(JSON.stringify({ event: "telegram_callback_rejected", chat_id: chatId }));
    return { action: "rejected", chat_id: chatId };
  }

  const cache = await loadCache(env);
  const parts = data.split(":");
  const action = parts[0] || "unknown";
  const slot = parts[1] || "midday";
  const day = parts[2] || currentDayKey();
  const snapshot = getSnapshot(cache, day);

  if (action === "facts") {
    await answerCallback(env, callback.id);
    await sendMessage(env, chatId, buildFactsMessage(snapshot, day), null);
    return { action: "facts", chat_id: chatId };
  }
  if (action === "roast") {
    await answerCallback(env, callback.id);
    await sendMessage(env, chatId, buildRoastMessage(snapshot, day), null);
    return { action: "roast", chat_id: chatId };
  }
  if (action === "what15") {
    await answerCallback(env, callback.id);
    await sendMessage(env, chatId, buildWhat15Message(slot, snapshot), null);
    return { action: "what15", chat_id: chatId };
  }
  if (action === "why") {
    await answerCallback(env, callback.id);
    await sendMessage(env, chatId, buildWhyMessage(snapshot, day), null);
    return { action: "why", chat_id: chatId };
  }

  await answerCallback(env, callback.id, "Пока недоступно в Cloudflare runtime");
  return { action, chat_id: chatId };
}

function isAllowedChat(env, chatId) {
  const allowed = String(env.TELEGRAM_CHAT_ID || "")
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
  return allowed.length === 0 || allowed.includes(String(chatId));
}

async function sendMessage(env, chatId, text, replyMarkup = null) {
  const payload = {
    chat_id: chatId,
    text,
    parse_mode: "HTML",
    disable_web_page_preview: true,
  };
  if (replyMarkup) payload.reply_markup = replyMarkup;
  const response = await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/sendMessage`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(payload),
  });
  const body = await response.json();
  console.log(JSON.stringify({
    event: "telegram_send",
    ok: body.ok,
    status: response.status,
    chat_id: chatId,
    message_id: body.result?.message_id || "",
    text_len: text.length,
  }));
  if (!response.ok || !body.ok) {
    throw new Error(`Telegram send failed ${response.status}: ${JSON.stringify(body)}`);
  }
}

async function answerCallback(env, callbackId, text = "") {
  if (!callbackId) return;
  await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/answerCallbackQuery`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ callback_query_id: callbackId, text }),
  });
}

async function loadCache(env) {
  const response = await fetch(`https://api.github.com/gists/${env.CACHE_GIST_ID}`, {
    headers: {
      accept: "application/vnd.github+json",
      authorization: `Bearer ${env.GIST_TOKEN}`,
      "user-agent": "coach-potato-cloudflare-worker",
    },
  });
  if (!response.ok) {
    throw new Error(`Gist fetch failed ${response.status}`);
  }
  const gist = await response.json();
  const content = gist.files?.["cache.json"]?.content;
  if (!content) return {};
  return JSON.parse(content);
}

async function triggerRecoverySync(env) {
  if (!env.GITHUB_DISPATCH_TOKEN || !env.GITHUB_REPO) return false;
  const response = await fetch(`https://api.github.com/repos/${env.GITHUB_REPO}/actions/workflows/recovery.yml/dispatches`, {
    method: "POST",
    headers: {
      accept: "application/vnd.github+json",
      authorization: `Bearer ${env.GITHUB_DISPATCH_TOKEN}`,
      "content-type": "application/json",
      "user-agent": "coach-potato-cloudflare-worker",
    },
    body: JSON.stringify({
      ref: "main",
      inputs: { operation: "sync", backfill_days: "7" },
    }),
  });
  return response.status === 204;
}

function helpMessage() {
  return "Команды:\n/today\n/color\n/week\n/stats\n/refresh\n/debug_sync\n/debug_sent\n/help";
}

function currentDayKey() {
  return formatMskDate(new Date());
}

function formatMskDate(date) {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: MSK_TZ,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(date);
  const values = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  return `${values.year}-${values.month}-${values.day}`;
}

function getSnapshot(cache, day = currentDayKey()) {
  const snapshot = cache?.[day];
  return snapshot && typeof snapshot === "object" ? snapshot : {};
}

function metricValue(snapshot, metric, keys) {
  const node = snapshot?.[metric];
  if (!node || typeof node !== "object") return null;
  for (const key of keys) {
    const value = node[key];
    if (typeof value === "number") return value;
  }
  return null;
}

function availableMetrics(snapshot) {
  return Object.keys(METRIC_LABELS).filter((key) => {
    const value = snapshot?.[key];
    return value && typeof value === "object" && Object.keys(value).length > 0;
  });
}

function buildTodayMessage(cache) {
  const day = currentDayKey();
  const snapshot = getSnapshot(cache, day);
  const available = availableMetrics(snapshot);
  if (available.length === 0) {
    return "🟡 <b>Сигнал дня</b>\n\nДанных за сегодня пока нет. Держим ровный режим без резких решений.";
  }

  const bb = metricValue(snapshot, "body_battery", ["mostRecentValue", "chargedValue"]);
  const stress = metricValue(snapshot, "stress", ["avgStressLevel", "overallStressLevel"]);
  const sleepSeconds = metricValue(snapshot, "sleep", ["sleepTimeSeconds", "totalSleepSeconds"]);
  const rhr = metricValue(snapshot, "rhr", ["restingHeartRate"]);
  const status = statusLabel(bb, stress);
  const chips = [];
  if (bb !== null) chips.push(`Body Battery ${Math.round(bb)}`);
  if (stress !== null) chips.push(`стресс ${Math.round(stress)}`);
  if (sleepSeconds !== null) chips.push(`сон ${formatHours(sleepSeconds)}`);
  if (rhr !== null) chips.push(`RHR ${Math.round(rhr)}`);

  return [
    `🟡 <b>Сигнал дня</b>`,
    "",
    `<i>${status}</i>`,
    `Факты: ${chips.join(" · ") || "данные частичные"}.`,
    "Лучшее действие: один спокойный блок и короткая пауза.",
    `Надёжность: ${KEY_METRICS.every((m) => available.includes(m)) ? "высокая" : "средняя/низкая"}.`,
  ].join("\n");
}

function buildFactsMessage(snapshot, day) {
  const available = availableMetrics(snapshot);
  if (available.length === 0) return `По фактам за ${day}: данных пока нет.`;
  return [
    `📌 <b>По фактам</b> ${day}`,
    `• Есть: ${available.map((m) => METRIC_LABELS[m] || m).join(", ")}`,
    `• Body Battery: ${valueOrDash(metricValue(snapshot, "body_battery", ["mostRecentValue", "chargedValue"]))}`,
    `• Стресс: ${valueOrDash(metricValue(snapshot, "stress", ["avgStressLevel", "overallStressLevel"]))}`,
    `• Сон: ${formatMaybeHours(metricValue(snapshot, "sleep", ["sleepTimeSeconds", "totalSleepSeconds"]))}`,
    "Вывод: держать ровный темп, без резких добивок.",
  ].join("\n");
}

function buildRoastMessage(snapshot, day) {
  const facts = buildFactsMessage(snapshot, day);
  return `🔥 <b>Пожарь</b>\n${facts}\n\nКолкость: режим просит меньше героизма, больше последовательности.`;
}

function buildWhyMessage(snapshot, day) {
  const bb = metricValue(snapshot, "body_battery", ["mostRecentValue", "chargedValue"]);
  const stress = metricValue(snapshot, "stress", ["avgStressLevel", "overallStressLevel"]);
  return [
    `Почему так (${day})`,
    `Причины: ${bb !== null ? `Body Battery ${Math.round(bb)}` : "Body Battery нет"}, ${stress !== null ? `стресс ${Math.round(stress)}` : "стресс нет"}.`,
    "Рычаг: один фокус-блок, потом пауза.",
  ].join("\n");
}

function buildWhat15Message(slot, snapshot) {
  const bb = metricValue(snapshot, "body_battery", ["mostRecentValue", "chargedValue"]);
  if (typeof bb === "number" && bb < 35) {
    return "🎯 <b>Что делать за 15 минут</b>\n1) 4 мин — тишина без экрана.\n2) 6 мин — мягкая ходьба.\n3) 5 мин — вода и завершение новых задач.";
  }
  if (slot === "morning") {
    return "🎯 <b>Что делать за 15 минут</b>\n1) 2 мин — вода.\n2) 10 мин — один приоритет.\n3) 3 мин — пауза и следующий шаг.";
  }
  return "🎯 <b>Что делать за 15 минут</b>\n1) 5 мин — пройтись.\n2) 7 мин — закрыть один хвост.\n3) 3 мин — вернуться к главной задаче.";
}

function buildColorMessage(cache) {
  const state = cache?._weekly_state || {};
  const latest = Object.keys(state).sort().pop();
  const color = latest ? state[latest] : null;
  if (!color) return "🎨 Цвет недели пока не сохранён. После ближайшего утреннего push появится тема.";
  return `🎨 <b>Тема недели</b>\n\n${escapeHtml(color.name_ru || "цвет")} · ${escapeHtml(color.hex || "")}\nФокус: ровный темп без дробления внимания.`;
}

function buildStatsMessage(cache, chatId) {
  const todayVotes = cache?._today_votes || {};
  const colorVotes = cache?._daily_votes || {};
  const todayTotal = Object.keys(todayVotes).filter((k) => k.includes(`|${chatId}`)).length;
  const colorTotal = Object.keys(colorVotes).filter((k) => k.includes(`|${chatId}`)).length;
  return `Статистика:\nЦветовые отклики: ${colorTotal}\nСтатус дня: ${todayTotal}`;
}

function buildWeekMessage(cache) {
  const days = [];
  const now = new Date();
  for (let index = 6; index >= 0; index -= 1) {
    days.push(formatMskDate(new Date(now.getTime() - index * 24 * 60 * 60 * 1000)));
  }

  const lines = days.map((day) => {
    const snapshot = getSnapshot(cache, day);
    const bb = metricValue(snapshot, "body_battery", ["mostRecentValue", "chargedValue"]);
    const stress = metricValue(snapshot, "stress", ["avgStressLevel", "overallStressLevel"]);
    const sleepSeconds = metricValue(snapshot, "sleep", ["sleepTimeSeconds", "totalSleepSeconds"]);
    const parts = [];
    if (bb !== null) parts.push(`BB ${Math.round(bb)}`);
    if (stress !== null) parts.push(`стресс ${Math.round(stress)}`);
    if (sleepSeconds !== null) parts.push(`сон ${formatHours(sleepSeconds)}`);
    return `• ${day}: ${parts.join(" · ") || "данных нет"}`;
  });

  return [
    "Неделя:",
    ...lines,
    "",
    "Вывод: смотри на ритм, не на один день.",
  ].join("\n");
}

function buildDebugSyncMessage(cache) {
  const day = currentDayKey();
  const snapshot = getSnapshot(cache, day);
  const available = availableMetrics(snapshot);
  return [
    "Debug sync:",
    "• runtime: cloudflare-worker",
    `• date key: ${day}`,
    `• has today: ${Object.keys(snapshot).length > 0 ? "true" : "false"}`,
    `• available: ${available.join(", ") || "-"}`,
    `• last sync: ${snapshot.last_sync_time || snapshot.fetched_at_utc || "-"}`,
  ].join("\n");
}

function buildDebugSentMessage(cache, chatId) {
  const day = currentDayKey();
  const pushState = cache?._push_state || {};
  const prefix = `${day}|${chatId}|`;
  const keys = Object.keys(pushState).filter((key) => key.startsWith(prefix));
  if (keys.length === 0) return `sent-registry ${day}: пусто`;
  return [`sent-registry ${day}:`, ...keys.map((key) => `• ${key}`)].join("\n");
}

function routeTextQuestion(text, cache) {
  const q = text.toLowerCase();
  if (q.includes("какое число") || q.includes("какая дата")) {
    return `Сегодня ${currentDayKey()}.`;
  }
  if (q.includes("данные") || q.includes("метрик")) {
    return buildDebugSyncMessage(cache);
  }
  return buildTodayMessage(cache);
}

function todayKeyboard(day) {
  return {
    inline_keyboard: [[
      { text: "Почему?", callback_data: `why:midday:${day}` },
      { text: "По фактам", callback_data: `facts:midday:${day}` },
      { text: "Пожарь", callback_data: `roast:midday:${day}` },
      { text: "Что делать (15м)", callback_data: `what15:midday:${day}` },
    ]],
  };
}

function statusLabel(bb, stress) {
  if (typeof bb === "number" && bb < 35) return "Бережный режим.";
  if (typeof stress === "number" && stress > 62) return "День лучше вести мягче.";
  if (typeof bb === "number" && bb > 70) return "Собранный темп.";
  return "Ровный режим.";
}

function formatHours(seconds) {
  const totalMinutes = Math.round(seconds / 60);
  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;
  return `${hours}ч ${String(minutes).padStart(2, "0")}м`;
}

function formatMaybeHours(value) {
  return typeof value === "number" ? formatHours(value) : "—";
}

function valueOrDash(value) {
  return typeof value === "number" ? String(Math.round(value)) : "—";
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#039;",
  }[char]));
}

function json(payload, status = 200) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "content-type": "application/json; charset=utf-8" },
  });
}
