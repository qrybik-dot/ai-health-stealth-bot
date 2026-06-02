const MSK_TZ = "Europe/Moscow";
const KEY_METRICS = ["sleep", "body_battery", "rhr", "stress"];
const METRIC_LABELS = {
  sleep: "сон",
  body_battery: "Body Battery",
  rhr: "RHR",
  stress: "стресс",
  steps: "шаги",
  daily_activity: "активность",
  hrv_status: "HRV",
};

const SLOT_FOCUS = {
  morning: "восстановление после сна и запас на первую половину дня",
  midday: "сдвиг ресурса с утра и короткая коррекция курса",
  evening: "закрытие дня, снижение шума и подготовка восстановления",
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
    const slot = currentSlotId();
    await sendMessage(env, chatId, buildTodayMessage(cache, slot), todayKeyboard(currentDayKey(), slot));
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
  if (lower === "/debug_health") {
    const cache = await loadCache(env);
    await sendMessage(env, chatId, buildDebugHealthMessage(cache));
    return { action: "debug_health", chat_id: chatId };
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
    await sendMessage(env, chatId, buildFactsMessage(snapshot, day, slot), null);
    return { action: "facts", chat_id: chatId };
  }
  if (action === "roast") {
    await answerCallback(env, callback.id);
    await sendMessage(env, chatId, buildRoastMessage(snapshot, day, slot), null);
    return { action: "roast", chat_id: chatId };
  }
  if (action === "what15") {
    await answerCallback(env, callback.id);
    await sendMessage(env, chatId, buildWhat15Message(slot, snapshot), null);
    return { action: "what15", chat_id: chatId };
  }
  if (action === "why") {
    await answerCallback(env, callback.id);
    await sendMessage(env, chatId, buildWhyMessage(snapshot, day, slot), null);
    return { action: "why", chat_id: chatId };
  }
  if (action === "color_vote") {
    const weekId = parts[1] || currentIsoWeekId();
    const vote = parts[2] || "partial";
    const result = await storeColorVote(env, cache, chatId, weekId, vote);
    await answerCallback(env, callback.id, result.saved ? "Голос учтён" : `Уже учтено: ${voteLabel(result.existing || vote)}`);
    await editMessageReplyMarkup(env, chatId, callback.message?.message_id, votedKeyboard(result.existing || vote));
    return { action: "color_vote", chat_id: chatId, saved: result.saved };
  }
  if (action === "today_vote") {
    const voteDay = parts[1] || currentDayKey();
    const vote = parts[2] || "partial";
    const result = await storeTodayVote(env, cache, chatId, voteDay, vote);
    await answerCallback(env, callback.id, result.saved ? "Голос учтён" : `Уже учтено: ${voteLabel(result.existing || vote)}`);
    await editMessageReplyMarkup(env, chatId, callback.message?.message_id, votedKeyboard(result.existing || vote));
    return { action: "today_vote", chat_id: chatId, saved: result.saved };
  }
  if (action === "noop") {
    await answerCallback(env, callback.id);
    return { action: "noop", chat_id: chatId };
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

async function editMessageReplyMarkup(env, chatId, messageId, replyMarkup) {
  if (!messageId) return;
  const response = await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/editMessageReplyMarkup`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ chat_id: chatId, message_id: messageId, reply_markup: replyMarkup }),
  });
  if (!response.ok) {
    console.warn(JSON.stringify({ event: "telegram_edit_markup_failed", status: response.status }));
  }
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
  const file = gist.files?.["cache.json"];
  let content = file?.content;
  if (file?.truncated && file?.raw_url) {
    const rawResponse = await fetch(file.raw_url, {
      headers: {
        authorization: `Bearer ${env.GIST_TOKEN}`,
        "user-agent": "coach-potato-cloudflare-worker",
      },
    });
    if (!rawResponse.ok) {
      throw new Error(`Gist raw fetch failed ${rawResponse.status}`);
    }
    content = await rawResponse.text();
  }
  if (!content) return {};
  return JSON.parse(content);
}

async function saveCache(env, cache) {
  const response = await fetch(`https://api.github.com/gists/${env.CACHE_GIST_ID}`, {
    method: "PATCH",
    headers: {
      accept: "application/vnd.github+json",
      authorization: `Bearer ${env.GIST_TOKEN}`,
      "content-type": "application/json",
      "user-agent": "coach-potato-cloudflare-worker",
    },
    body: JSON.stringify({
      files: {
        "cache.json": {
          content: JSON.stringify(cache, null, 2),
        },
      },
    }),
  });
  if (!response.ok) {
    throw new Error(`Gist cache save failed ${response.status}`);
  }
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
  return "Команды:\n/today\n/color\n/week\n/stats\n/refresh\n/debug_sync\n/debug_health\n/debug_sent\n/help";
}

function currentDayKey() {
  return formatMskDate(new Date());
}

function currentIsoWeekId() {
  return isoWeekIdFromDay(currentDayKey());
}

function currentSlotId(date = new Date()) {
  const parts = new Intl.DateTimeFormat("en-GB", {
    timeZone: MSK_TZ,
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).formatToParts(date);
  const values = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  const minutes = Number(values.hour || 0) * 60 + Number(values.minute || 0);
  if (minutes < 12 * 60) return "morning";
  if (minutes < 18 * 60) return "midday";
  return "evening";
}

function isoWeekIdFromDay(day) {
  const date = new Date(`${day}T12:00:00Z`);
  const target = new Date(Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), date.getUTCDate()));
  const dayNr = (target.getUTCDay() + 6) % 7;
  target.setUTCDate(target.getUTCDate() - dayNr + 3);
  const firstThursday = new Date(Date.UTC(target.getUTCFullYear(), 0, 4));
  const firstDayNr = (firstThursday.getUTCDay() + 6) % 7;
  firstThursday.setUTCDate(firstThursday.getUTCDate() - firstDayNr + 3);
  const week = 1 + Math.round((target - firstThursday) / (7 * 24 * 60 * 60 * 1000));
  return `${target.getUTCFullYear()}-W${String(week).padStart(2, "0")}`;
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

function boundedMetricValue(snapshot, metric, keys, minValue, maxValue) {
  const value = metricValue(snapshot, metric, keys);
  if (typeof value !== "number" || !Number.isFinite(value)) return null;
  if (value < minValue || value > maxValue) return null;
  return value;
}

function availableMetrics(snapshot) {
  return Object.keys(METRIC_LABELS).filter((key) => {
    const value = snapshot?.[key];
    return value && typeof value === "object" && Object.keys(value).length > 0;
  });
}

function stringMetric(snapshot, metric, keys) {
  const node = snapshot?.[metric];
  if (!node || typeof node !== "object") return null;
  for (const key of keys) {
    const value = node[key];
    if (typeof value === "string" && value.trim()) return value.trim();
  }
  return null;
}

function snapshotMetrics(snapshot) {
  const activeSeconds = boundedMetricValue(snapshot, "daily_activity", ["activeSeconds", "activeTimeSeconds"], 0, 24 * 3600);
  const activeMinutes = typeof activeSeconds === "number" ? Math.round(activeSeconds / 60) : null;
  const rawSteps = boundedMetricValue(snapshot, "steps", ["totalSteps", "steps"], 0, 120000);
  const stepsReliable = !(rawSteps === 0 && typeof activeMinutes === "number" && activeMinutes >= 10);
  return {
    bb: boundedMetricValue(snapshot, "body_battery", ["mostRecentValue", "currentValue", "chargedValue"], 0, 100),
    bbCharged: boundedMetricValue(snapshot, "body_battery", ["chargedValue"], 0, 100),
    stress: boundedMetricValue(snapshot, "stress", ["avgStressLevel", "overallStressLevel"], 0, 100),
    maxStress: boundedMetricValue(snapshot, "stress", ["maxStressLevel"], 0, 100),
    sleepSeconds: boundedMetricValue(snapshot, "sleep", ["sleepTimeSeconds", "totalSleepSeconds"], 1, 24 * 3600),
    rhr: boundedMetricValue(snapshot, "rhr", ["restingHeartRate"], 30, 130),
    steps: stepsReliable ? rawSteps : null,
    stepsRaw: rawSteps,
    stepsReliable,
    activeMinutes,
    hrvStatus: stringMetric(snapshot, "hrv_status", ["status", "hrvStatus"]),
  };
}

function hasUsableSnapshot(snapshot) {
  return availableMetrics(snapshot).length > 0;
}

function keyMetricsPresentCount(snapshot) {
  const available = availableMetrics(snapshot);
  return KEY_METRICS.filter((metric) => available.includes(metric)).length;
}

function dayStatus(snapshot) {
  if (!snapshot || Object.keys(snapshot).length === 0) return "no_data";
  return keyMetricsPresentCount(snapshot) >= 4 ? "ready" : "partial";
}

function dataQuality(snapshot) {
  const available = availableMetrics(snapshot);
  const present = KEY_METRICS.filter((metric) => available.includes(metric)).length;
  if (present >= 4) return "высокая";
  if (present >= 2) return "средняя";
  if (present >= 1) return "низкая";
  return "нет данных";
}

function metricChips(snapshot, limit = 4, slot = "midday") {
  const metrics = snapshotMetrics(snapshot);
  const pool = {
    sleep: metrics.sleepSeconds !== null ? `сон ${formatHours(metrics.sleepSeconds)}` : null,
    bbCharged: metrics.bbCharged !== null ? `стартовый BB ${Math.round(metrics.bbCharged)}` : null,
    bb: metrics.bb !== null ? `BB ${Math.round(metrics.bb)}` : null,
    bbDelta: metrics.bb !== null && metrics.bbCharged !== null ? `с утра ${Math.round(metrics.bbCharged)} → ${Math.round(metrics.bb)}` : null,
    stress: metrics.stress !== null ? `стресс ${Math.round(metrics.stress)}` : null,
    maxStress: metrics.maxStress !== null ? `пик стресса ${Math.round(metrics.maxStress)}` : null,
    rhr: metrics.rhr !== null ? `RHR ${Math.round(metrics.rhr)}` : null,
    steps: metrics.steps !== null ? `${Math.round(metrics.steps)} шагов` : null,
    hrv: metrics.hrvStatus ? `HRV ${escapeHtml(metrics.hrvStatus)}` : null,
  };
  const order = {
    morning: ["sleep", "bbCharged", "hrv", "rhr", "bb", "stress", "steps"],
    midday: ["bb", "bbDelta", "stress", "maxStress", "steps", "sleep", "rhr"],
    evening: ["bb", "bbDelta", "stress", "steps", "maxStress", "sleep", "rhr"],
  }[slot] || ["bb", "stress", "sleep", "rhr", "steps"];
  return order.map((key) => pool[key]).filter(Boolean).slice(0, limit);
}

function scoreSnapshot(snapshot) {
  const metrics = snapshotMetrics(snapshot);
  let score = 50;
  if (typeof metrics.bb === "number") score += (metrics.bb - 50) * 0.45;
  if (typeof metrics.stress === "number") score -= (metrics.stress - 35) * 0.35;
  if (typeof metrics.sleepSeconds === "number") {
    const hours = metrics.sleepSeconds / 3600;
    score += Math.max(-12, Math.min(12, (hours - 7) * 4));
  }
  return Math.max(0, Math.min(100, Math.round(score)));
}

function statusForSnapshot(snapshot) {
  const metrics = snapshotMetrics(snapshot);
  if (!hasUsableSnapshot(snapshot)) return "данных пока нет";
  if (typeof metrics.bb === "number" && metrics.bb < 30) return "ресурс просит бережный режим";
  if (typeof metrics.stress === "number" && metrics.stress >= 60) return "стресс тянет день вверх";
  if (typeof metrics.sleepSeconds === "number" && metrics.sleepSeconds < 6 * 3600) return "сон не добрал восстановление";
  if (typeof metrics.bb === "number" && metrics.bb >= 65 && (metrics.stress === null || metrics.stress <= 45)) return "можно держать собранный темп";
  return "ровный день без резких добивок";
}

function slotHead(slot) {
  return {
    morning: "Старт дня",
    midday: "Сверка в середине дня",
    evening: "Финал дня",
  }[slot] || "Сигнал дня";
}

function actionForSnapshot(slot, snapshot) {
  const metrics = snapshotMetrics(snapshot);
  if (!hasUsableSnapshot(snapshot)) return "держать базовый режим и дождаться следующей синхронизации";
  if (typeof metrics.bb === "number" && metrics.bb < 30) return "снять лишнюю нагрузку, закрывать только обязательное";
  if (slot === "morning") {
    if (typeof metrics.sleepSeconds === "number" && metrics.sleepSeconds < 6 * 3600) return "свет, вода и короткий фокус-блок вместо тяжёлого старта";
    return "первым блоком взять один главный приоритет";
  }
  if (slot === "midday") {
    if (typeof metrics.stress === "number" && metrics.stress >= 60) return "7 минут без экрана, вода, затем одна простая задача";
    if (typeof metrics.steps === "number" && metrics.steps < 2500) return "10-15 минут спокойной ходьбы и возврат к одному блоку";
    return "один фокус-блок, потом короткая пауза";
  }
  if (slot === "evening") {
    if (typeof metrics.stress === "number" && metrics.stress >= 60) return "закрыть входящие, приглушить стимулы, оставить только бытовое";
    return "не разгонять вечер, готовить спокойное завершение дня";
  }
  return "один фокус-блок, потом короткая пауза";
}

function meaningForSlot(slot, snapshot) {
  const metrics = snapshotMetrics(snapshot);
  if (!hasUsableSnapshot(snapshot)) return "вывод предварительный, фактов мало";
  if (slot === "morning") {
    if (typeof metrics.sleepSeconds === "number" && metrics.sleepSeconds < 6 * 3600) return "восстановление слабое, утро лучше вести в экономии";
    return "смотрим сон и стартовый ресурс, не общий шум дня";
  }
  if (slot === "midday") {
    if (typeof metrics.stress === "number" && metrics.stress >= 60) return "середина дня просит коррекцию, не ещё один рывок";
    return "смотрим, как просел ресурс с утра и нужен ли reset";
  }
  if (slot === "evening") {
    if (typeof metrics.stress === "number" && metrics.stress >= 60) return "главное не тащить дневной стресс в ночь";
    return "закрываем день и защищаем восстановление завтра";
  }
  return statusForSnapshot(snapshot);
}

function buildTodayMessage(cache, slot = "midday") {
  const day = currentDayKey();
  const snapshot = getSnapshot(cache, day);
  if (!hasUsableSnapshot(snapshot)) {
    return buildNoDataTodayMessage(slot);
  }

  const chips = metricChips(snapshot, 4, slot);

  return [
    `🟡 <b>${slotHead(slot)}</b>`,
    "",
    `<b>Вердикт:</b> ${statusForSnapshot(snapshot)}.`,
    `<b>Фокус слота:</b> ${SLOT_FOCUS[slot] || SLOT_FOCUS.midday}.`,
    `<b>Факты:</b> ${chips.join(" · ") || "данные частичные"}.`,
    `<b>Смысл:</b> ${meaningForSlot(slot, snapshot)}.`,
    `<b>Действие:</b> ${actionForSnapshot(slot, snapshot)}.`,
    `<b>Надёжность:</b> ${dataQuality(snapshot)}.`,
  ].join("\n");
}

function buildNoDataTodayMessage(slot = "midday") {
  return [
    `🟡 <b>${slotHead(slot)}</b>`,
    "",
    "Данные за сегодня ещё не приехали.",
    "Пока держим ровный режим без резких решений.",
    "<b>Действие:</b> один спокойный блок и короткая пауза.",
    "<b>Надёжность:</b> нет данных.",
  ].join("\n");
}

function buildFactsMessage(snapshot, day, slot = "midday") {
  const available = availableMetrics(snapshot);
  if (available.length === 0) return `По фактам за ${day}: данных пока нет.`;
  const metrics = snapshotMetrics(snapshot);
  const stepsLine = metrics.stepsReliable
    ? valueOrDash(metrics.steps)
    : `нет корректных данных${typeof metrics.activeMinutes === "number" ? `; активность ${metrics.activeMinutes} мин, но Garmin отдал 0 шагов` : ""}`;
  return [
    `📌 <b>По фактам</b> ${day}`,
    `• Фокус: ${SLOT_FOCUS[slot] || SLOT_FOCUS.midday}`,
    `• Есть: ${available.map((m) => METRIC_LABELS[m] || m).join(", ")}`,
    `• Body Battery: ${valueOrDash(metrics.bb)}${metrics.bbCharged !== null ? ` / заряд ${Math.round(metrics.bbCharged)}` : ""}`,
    `• Стресс: ${valueOrDash(metrics.stress)}${metrics.maxStress !== null ? ` / пик ${Math.round(metrics.maxStress)}` : ""}`,
    `• Сон: ${formatMaybeHours(metrics.sleepSeconds)}`,
    `• Шаги: ${stepsLine}`,
    metrics.stepsReliable ? "" : "• Вывод по шагам: блок steps есть, но значение похоже на неполную синхронизацию.",
    `• Надёжность: ${dataQuality(snapshot)}`,
    `Вывод: ${statusForSnapshot(snapshot)}.`,
  ].filter(Boolean).join("\n");
}

function buildRoastMessage(snapshot, day, slot = "midday") {
  if (!hasUsableSnapshot(snapshot)) return `🔥 <b>Пожарь</b>\nПо фактам за ${day}: данных пока нет. Без данных не жарю.`;
  const metrics = snapshotMetrics(snapshot);
  const jab = typeof metrics.stress === "number" && metrics.stress >= 60
    ? "стресс уже сделал презентацию без спроса"
    : typeof metrics.bb === "number" && metrics.bb < 35
      ? "ресурс не батарейка из рекламы, чудес не обещал"
      : "режим просит меньше героизма, больше последовательности";
  return [
    "🔥 <b>Пожарь</b>",
    `По фактам за ${day}: ${metricChips(snapshot, 3, slot).join(" · ")}.`,
    `Колкость: ${jab}.`,
    `Дело: ${actionForSnapshot(slot, snapshot)}.`,
  ].join("\n");
}

function buildWhyMessage(snapshot, day, slot = "midday") {
  if (!hasUsableSnapshot(snapshot)) return `Почему так (${day})\nДанных за день пока нет, поэтому вывод предварительный.`;
  const metrics = snapshotMetrics(snapshot);
  const reasons = [];
  if (metrics.bb !== null) reasons.push(`ресурс: BB ${Math.round(metrics.bb)}`);
  if (metrics.stress !== null) reasons.push(`нагрузка: стресс ${Math.round(metrics.stress)}`);
  if (metrics.sleepSeconds !== null) reasons.push(`восстановление: сон ${formatHours(metrics.sleepSeconds)}`);
  if (metrics.steps !== null) reasons.push(`движение: ${Math.round(metrics.steps)} шагов`);
  if (!metrics.stepsReliable && metrics.stepsRaw === 0) reasons.push("движение: шаги не считаю, Garmin отдал 0 при признаках активности");
  return [
    `Почему так (${day})`,
    `Фокус: ${SLOT_FOCUS[slot] || SLOT_FOCUS.midday}.`,
    `Причины: ${reasons.join("; ")}.`,
    `Логика: ${meaningForSlot(slot, snapshot)}.`,
    `Рычаг: ${actionForSnapshot(slot, snapshot)}.`,
  ].join("\n");
}

function buildWhat15Message(slot, snapshot) {
  const metrics = snapshotMetrics(snapshot);
  if (!hasUsableSnapshot(snapshot)) {
    return "🎯 <b>Что делать за 15 минут</b>\n1) 5 мин — пройтись.\n2) 5 мин — вода и тишина.\n3) 5 мин — выбрать один следующий шаг.";
  }
  if (typeof metrics.bb === "number" && metrics.bb < 35) {
    return "🎯 <b>Что делать за 15 минут</b>\n1) 4 мин — тишина без экрана.\n2) 6 мин — мягкая ходьба.\n3) 5 мин — вода и завершение новых задач.";
  }
  if (typeof metrics.stress === "number" && metrics.stress >= 60) {
    return "🎯 <b>Что делать за 15 минут</b>\n1) 3 мин — убрать экран.\n2) 7 мин — спокойная ходьба или дыхание.\n3) 5 мин — вернуться к одной простой задаче.";
  }
  if (typeof metrics.sleepSeconds === "number" && metrics.sleepSeconds < 6 * 3600) {
    return "🎯 <b>Что делать за 15 минут</b>\n1) 5 мин — свет и вода.\n2) 7 мин — лёгкое движение.\n3) 3 мин — убрать лишнее из плана.";
  }
  if (slot === "morning") {
    return "🎯 <b>Что делать за 15 минут</b>\n1) 2 мин — вода.\n2) 10 мин — один приоритет.\n3) 3 мин — пауза и следующий шаг.";
  }
  if (slot === "evening") {
    return "🎯 <b>Что делать за 15 минут</b>\n1) 5 мин — закрыть бытовой хвост.\n2) 5 мин — приглушить стимулы.\n3) 5 мин — план на завтра одной строкой.";
  }
  return "🎯 <b>Что делать за 15 минут</b>\n1) 5 мин — пройтись.\n2) 7 мин — закрыть один хвост.\n3) 3 мин — вернуться к главной задаче.";
}

function buildColorMessage(cache) {
  const snapshot = getSnapshot(cache, currentDayKey());
  const state = cache?._weekly_state || {};
  const latest = Object.keys(state).sort().pop();
  const color = latest ? state[latest] : null;
  if (!hasUsableSnapshot(snapshot)) {
    if (!color) return "🎨 Цвет дня пока не считаю: данных за сегодня нет.";
    return `🎨 <b>Тема недели</b>\n\n${escapeHtml(color.name_ru || "цвет")} · ${escapeHtml(color.hex || "")}\nСегодня данных мало, поэтому это только недельный фон.`;
  }
  const signal = deriveColorSignal(snapshot);
  const weekly = color ? `\nНедельный фон: ${escapeHtml(color.name_ru || "цвет")} · ${escapeHtml(color.hex || "")}.` : "";
  return [
    `🎨 <b>Цвет дня: ${signal.name}</b>`,
    "",
    `Сигнал: ${signal.reason}.`,
    `Фокус: ${signal.focus}.`,
    `Факты: ${metricChips(snapshot, 3, "midday").join(" · ")}.`,
    weekly,
  ].filter(Boolean).join("\n");
}

function buildStatsMessage(cache, chatId) {
  const weekId = currentIsoWeekId();
  const colorStats = collectColorVoteStats(cache, chatId, weekId);
  const todayStats = collectTodayVoteStats(cache, chatId, weekId);
  const total = colorStats.total + todayStats.total;
  const summary = total > 0 ? `Всего откликов: ${total}.` : "Откликов за неделю пока нет.";
  return [
    `📊 <b>Статистика ${escapeHtml(weekId)}</b>`,
    "",
    voteStatsLine("Цвет недели", colorStats),
    voteStatsLine("Статус дня", todayStats),
    "",
    summary,
  ].join("\n");
}

function emptyVoteStats() {
  return { yes_count: 0, partial_count: 0, no_count: 0, total: 0, accuracy: 0 };
}

function addVote(stats, value) {
  if (value === "yes") stats.yes_count += 1;
  if (value === "partial") stats.partial_count += 1;
  if (value === "no") stats.no_count += 1;
  stats.total = stats.yes_count + stats.partial_count + stats.no_count;
  stats.accuracy = stats.total > 0 ? (stats.yes_count + 0.5 * stats.partial_count) / stats.total : 0;
}

function collectColorVoteStats(cache, chatId, weekId) {
  const stats = emptyVoteStats();
  const votes = cache?._daily_votes || {};
  for (const [key, payload] of Object.entries(votes)) {
    if (!key.endsWith(`|${chatId}`) || !payload || typeof payload !== "object") continue;
    if (payload.week_id !== weekId) continue;
    addVote(stats, payload.vote_value);
  }
  return stats;
}

function collectTodayVoteStats(cache, chatId, weekId) {
  const stats = emptyVoteStats();
  const votes = cache?._today_votes || {};
  const states = cache?._today_state || {};
  for (const [key, payload] of Object.entries(votes)) {
    if (!key.endsWith(`|${chatId}`) || !payload || typeof payload !== "object") continue;
    const state = states[key];
    if (!state || state.week_id !== weekId) continue;
    addVote(stats, payload.vote);
  }
  return stats;
}

function voteStatsLine(label, stats) {
  if (!stats.total) return `<b>${label}:</b> пока нет откликов`;
  const index = Math.round(stats.accuracy * 100);
  return `<b>${label}:</b> ✅ ${stats.yes_count} · 🤷 ${stats.partial_count} · ❌ ${stats.no_count} · индекс ${index}%`;
}

function deriveColorSignal(snapshot) {
  const metrics = snapshotMetrics(snapshot);
  if (typeof metrics.bb === "number" && metrics.bb >= 65 && (metrics.stress === null || metrics.stress <= 45)) {
    return {
      name: "зелёный стабильный",
      reason: "ресурс есть, стресс не давит",
      focus: "держать темп без лишнего разгона",
    };
  }
  if (typeof metrics.bb === "number" && metrics.bb < 35 && typeof metrics.stress === "number" && metrics.stress >= 55) {
    return {
      name: "красный перегруз",
      reason: "ресурс низкий, стресс высокий",
      focus: "снять добивки и оставить обязательное",
    };
  }
  if (typeof metrics.sleepSeconds === "number" && metrics.sleepSeconds < 6 * 3600 && (metrics.stress === null || metrics.stress <= 45)) {
    return {
      name: "синий recovery",
      reason: "сон короткий, но перегруза по стрессу не видно",
      focus: "восстановить ритм, не доказывать продуктивность",
    };
  }
  if (typeof metrics.steps === "number" && metrics.steps >= 9000 && (metrics.bb === null || metrics.bb >= 50)) {
    return {
      name: "яркий активный",
      reason: "движения много, ресурс держится",
      focus: "не превращать активность в поздний разгон",
    };
  }
  return {
    name: "янтарный смешанный",
    reason: "сигналы неплохие, но без полного запаса",
    focus: "один нормальный блок, потом короткая пауза",
  };
}

function utcNowIso() {
  return new Date().toISOString().replace(/\.\d{3}Z$/, "Z");
}

function validVote(value) {
  return ["yes", "partial", "no"].includes(value) ? value : "partial";
}

function voteLabel(value) {
  return {
    yes: "✅ Попало",
    partial: "➖ Частично",
    no: "❌ Мимо",
  }[value] || "➖ Частично";
}

function votedKeyboard(vote) {
  return { inline_keyboard: [[{ text: `🗳 Ваш выбор: ${voteLabel(vote)}`, callback_data: "noop" }]] };
}

async function storeColorVote(env, cache, chatId, weekId, rawVote) {
  const vote = validVote(rawVote);
  const today = currentDayKey();
  const key = `${today}|${chatId}`;
  if (!cache._daily_votes || typeof cache._daily_votes !== "object") cache._daily_votes = {};
  const existing = cache._daily_votes[key];
  if (existing && typeof existing === "object" && validVote(existing.vote_value) === existing.vote_value) {
    return { saved: false, existing: existing.vote_value };
  }
  cache._daily_votes[key] = { vote_value: vote, ts: utcNowIso(), week_id: weekId };

  if (!cache._weekly_state || typeof cache._weekly_state !== "object") cache._weekly_state = {};
  if (!cache._weekly_state[weekId] || typeof cache._weekly_state[weekId] !== "object") cache._weekly_state[weekId] = { week_id: weekId };
  if (!cache._weekly_state[weekId].votes_by_date_chat || typeof cache._weekly_state[weekId].votes_by_date_chat !== "object") {
    cache._weekly_state[weekId].votes_by_date_chat = {};
  }
  cache._weekly_state[weekId].votes_by_date_chat[key] = vote;
  await saveCache(env, cache);
  return { saved: true, existing: null };
}

async function storeTodayVote(env, cache, chatId, voteDay, rawVote) {
  const vote = validVote(rawVote);
  const key = `${voteDay}|${chatId}`;
  if (!cache._today_votes || typeof cache._today_votes !== "object") cache._today_votes = {};
  const existing = cache._today_votes[key];
  if (existing && typeof existing === "object" && validVote(existing.vote) === existing.vote) {
    return { saved: false, existing: existing.vote };
  }
  cache._today_votes[key] = { vote, ts: utcNowIso() };
  if (!cache._today_state || typeof cache._today_state !== "object") cache._today_state = {};
  if (!cache._today_state[key] || typeof cache._today_state[key] !== "object") {
    cache._today_state[key] = { week_id: isoWeekIdFromDay(voteDay) };
  } else if (!cache._today_state[key].week_id) {
    cache._today_state[key].week_id = isoWeekIdFromDay(voteDay);
  }
  await saveCache(env, cache);
  return { saved: true, existing: null };
}

function recentDayKeys(daysBack = 7) {
  const days = [];
  const now = new Date();
  for (let index = daysBack - 1; index >= 0; index -= 1) {
    days.push(formatMskDate(new Date(now.getTime() - index * 24 * 60 * 60 * 1000)));
  }
  return days;
}

function historyDayKeys(cache) {
  return Object.keys(cache || {})
    .filter((key) => /^\d{4}-\d{2}-\d{2}$/.test(key))
    .sort();
}

function average(values) {
  const clean = values.filter((value) => typeof value === "number");
  if (!clean.length) return null;
  return clean.reduce((sum, value) => sum + value, 0) / clean.length;
}

function rangeText(values, formatter = (value) => String(Math.round(value))) {
  const clean = values.filter((value) => typeof value === "number");
  if (!clean.length) return "нет данных";
  return `${formatter(Math.min(...clean))}–${formatter(Math.max(...clean))}`;
}

function buildWeekMessage(cache) {
  const days = recentDayKeys(7);
  const rows = days.map((day) => {
    const snapshot = getSnapshot(cache, day);
    const metrics = snapshotMetrics(snapshot);
    return {
      day,
      snapshot,
      metrics,
      hasData: hasUsableSnapshot(snapshot),
      score: hasUsableSnapshot(snapshot) ? scoreSnapshot(snapshot) : null,
    };
  });
  const available = rows.filter((row) => row.hasData);
  if (!available.length) return "📊 <b>Вердикт недели</b>\n\nДанных за последние 7 дней пока нет.";

  const best = [...available].sort((a, b) => b.score - a.score)[0];
  const hard = [...available].sort((a, b) => a.score - b.score)[0];
  const sleepRange = rangeText(available.map((row) => row.metrics.sleepSeconds), formatHours);
  const stressAvg = average(available.map((row) => row.metrics.stress));
  const bbRange = rangeText(available.map((row) => row.metrics.bb));
  const status = available.length < 4 ? "черновик: истории мало" : "рабочая картина";
  const focus = hard.score < 40
    ? "в начале недели искать перегруз и не тащить его дальше"
    : "смотреть на повторяющийся ритм, не на один удачный день";

  return [
    "📊 <b>Вердикт недели</b>",
    "",
    `<b>Статус:</b> ${status}. Данных: ${available.length}/7 дней.`,
    `<b>Сон:</b> ${sleepRange}.`,
    `<b>Стресс:</b> ${stressAvg === null ? "нет данных" : `средний около ${Math.round(stressAvg)}`}.`,
    `<b>Ресурс:</b> ${bbRange}.`,
    "",
    `<b>Лучший день:</b> ${best.day} — ${metricChips(best.snapshot, 3, "day").join(" · ")}.`,
    `<b>Сложный день:</b> ${hard.day} — ${metricChips(hard.snapshot, 3, "day").join(" · ")}.`,
    "",
    `🎯 <b>Фокус:</b> ${focus}.`,
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

function buildDebugHealthMessage(cache) {
  const day = currentDayKey();
  const snapshot = getSnapshot(cache, day);
  const historyKeys = historyDayKeys(cache);
  const pushState = cache?._push_state && typeof cache._push_state === "object" ? cache._push_state : {};
  const weeklyState = cache?._weekly_state && typeof cache._weekly_state === "object" ? cache._weekly_state : {};
  const dailyVotes = cache?._daily_votes && typeof cache._daily_votes === "object" ? cache._daily_votes : {};
  const todayVotes = cache?._today_votes && typeof cache._today_votes === "object" ? cache._today_votes : {};
  const todaySentPrefix = `${day}|`;
  const todaySentCount = Object.keys(pushState).filter((key) => key.startsWith(todaySentPrefix)).length;
  return [
    "Ops health:",
    "• runtime: cloudflare-worker",
    `• date key: ${day}`,
    `• today status: ${dayStatus(snapshot)}`,
    `• today key metrics: ${keyMetricsPresentCount(snapshot)}/${KEY_METRICS.length}`,
    `• history days: ${historyKeys.length}`,
    `• latest history day: ${historyKeys[historyKeys.length - 1] || "-"}`,
    `• today sent registry: ${todaySentCount}`,
    `• weekly state: ${Object.keys(weeklyState).length}`,
    `• votes: color=${Object.keys(dailyVotes).length} today=${Object.keys(todayVotes).length}`,
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
    return buildDataAnswer(cache);
  }
  if (q.includes("недел")) {
    return buildWeekMessage(cache);
  }
  if (q.includes("месяц") || q.includes("30")) {
    return buildMonthAnswer(cache);
  }
  if (q.includes("шаг") || q.includes("ходьб")) {
    return buildStepsAnswer(cache);
  }
  if (q.includes("поесть") || q.includes("еда") || q.includes("есть ") || q.includes("завтрак") || q.includes("обед")) {
    return buildFoodAnswer(cache);
  }
  if (q.includes("сравни") || q.includes("вчера") || q.includes("лучше чем") || q.includes("хуже")) {
    return buildCompareAnswer(cache);
  }
  if (q.includes("трен") || q.includes("нагруз") || q.includes("спорт")) {
    return buildLoadAnswer(cache);
  }
  if (q.includes("режим") || q.includes("план") || q.includes("что делать") || q.includes("15")) {
    return buildModeAnswer(cache);
  }
  return buildTodayMessage(cache);
}

function buildStepsAnswer(cache) {
  const day = currentDayKey();
  const snapshot = getSnapshot(cache, day);
  const metrics = snapshotMetrics(snapshot);
  if (!hasUsableSnapshot(snapshot)) return "🚶 <b>Шаги</b>\nДанных за сегодня пока нет.";
  if (metrics.steps !== null) {
    return [
      "🚶 <b>Шаги</b>",
      `Сейчас: <b>${Math.round(metrics.steps)}</b>.`,
      typeof metrics.activeMinutes === "number" ? `Активность: ${metrics.activeMinutes} мин.` : "",
      "Смысл: движение учитываю как контекст нагрузки, не как цель само по себе.",
    ].filter(Boolean).join("\n");
  }
  if (metrics.stepsRaw === 0 && typeof metrics.activeMinutes === "number" && metrics.activeMinutes >= 10) {
    return [
      "🚶 <b>Почему нет шагов</b>",
      "Garmin отдал steps=0, но активность за день уже есть.",
      `Активность: ${metrics.activeMinutes} мин.`,
      "Вывод: это похоже на неполную синхронизацию блока шагов. В вердикте шаги не считаю как факт.",
    ].join("\n");
  }
  return [
    "🚶 <b>Шаги</b>",
    "Блок шагов есть, но пригодного значения сейчас нет.",
    "Вывод: не делаю вывод по ходьбе до следующей синхронизации.",
  ].join("\n");
}

function buildDataAnswer(cache) {
  const day = currentDayKey();
  const snapshot = getSnapshot(cache, day);
  const keys = historyDayKeys(cache);
  const available = availableMetrics(snapshot);
  return [
    "📌 <b>Что есть по данным</b>",
    `Сегодня: ${available.length ? available.map((key) => METRIC_LABELS[key] || key).join(", ") : "пока пусто"}.`,
    `История: ${keys.length} дней, последний ${keys[keys.length - 1] || "—"}.`,
    `Надёжность сегодня: ${dataQuality(snapshot)}.`,
  ].join("\n");
}

function buildCompareAnswer(cache) {
  const keys = historyDayKeys(cache).filter((day) => hasUsableSnapshot(getSnapshot(cache, day)));
  if (keys.length < 2) return "Сравнение пока слабое: нужно минимум два дня с данными.";
  const current = keys[keys.length - 1];
  const previous = keys[keys.length - 2];
  const currentSnapshot = getSnapshot(cache, current);
  const previousSnapshot = getSnapshot(cache, previous);
  const currentScore = scoreSnapshot(currentSnapshot);
  const previousScore = scoreSnapshot(previousSnapshot);
  const delta = currentScore - previousScore;
  const direction = delta > 4 ? "лучше" : delta < -4 ? "тяжелее" : "примерно так же";
  return [
    "↔️ <b>Сравнение</b>",
    `${current} против ${previous}: ${direction}.`,
    `Индекс режима: ${currentScore} против ${previousScore}.`,
    `Сегодня: ${metricChips(currentSnapshot, 3, "day").join(" · ")}.`,
    `Вчера: ${metricChips(previousSnapshot, 3, "day").join(" · ")}.`,
  ].join("\n");
}

function buildMonthAnswer(cache) {
  const keys = historyDayKeys(cache).slice(-30);
  const available = keys.filter((day) => hasUsableSnapshot(getSnapshot(cache, day)));
  if (!available.length) return "Месяц пока не собрать: нет дней с данными.";
  const rows = available.map((day) => {
    const snapshot = getSnapshot(cache, day);
    return { day, snapshot, metrics: snapshotMetrics(snapshot), score: scoreSnapshot(snapshot) };
  });
  const scores = rows.map((row) => row.score);
  const best = [...rows].sort((a, b) => b.score - a.score)[0];
  const hard = [...rows].sort((a, b) => a.score - b.score)[0];
  const stressAvg = average(rows.map((row) => row.metrics.stress));
  const sleepAvg = average(rows.map((row) => row.metrics.sleepSeconds));
  const stepsAvg = average(rows.map((row) => row.metrics.steps));
  const bbRange = rangeText(rows.map((row) => row.metrics.bb));
  const status = rows.length >= 14 ? "рабочая картина" : "черновик: истории мало";
  const focus = rows.length >= 14
    ? "искать повторяющийся паттерн: сон → стресс → ресурс"
    : "накопить хотя бы 14 дней, вывод пока без лишней уверенности";
  return [
    "🗓 <b>Месяц</b>",
    "",
    `<b>Статус:</b> ${status}. Данных: ${available.length}/30 дней.`,
    `<b>Диапазон:</b> ${keys[0]} — ${keys[keys.length - 1]}.`,
    `<b>Индекс режима:</b> ${rangeText(scores)}.`,
    `<b>Сон:</b> ${sleepAvg === null ? "нет данных" : `средний ${formatHours(sleepAvg)}`}.`,
    `<b>Стресс:</b> ${stressAvg === null ? "нет данных" : `средний около ${Math.round(stressAvg)}`}.`,
    `<b>Ресурс:</b> ${bbRange}.`,
    `<b>Шаги:</b> ${stepsAvg === null ? "нет данных" : `средние ${Math.round(stepsAvg)}/день`}.`,
    "",
    `<b>Лучший день:</b> ${best.day} — ${metricChips(best.snapshot, 3, "day").join(" · ")}.`,
    `<b>Сложный день:</b> ${hard.day} — ${metricChips(hard.snapshot, 3, "day").join(" · ")}.`,
    "",
    `🎯 <b>Фокус:</b> ${focus}.`,
  ].join("\n");
}

function buildFoodAnswer(cache) {
  const snapshot = getSnapshot(cache, currentDayKey());
  const metrics = snapshotMetrics(snapshot);
  if (!hasUsableSnapshot(snapshot)) {
    return "🍽 <b>Еда сейчас</b>\nДанных за день пока нет. Базово: простой приём еды, вода, без экспериментов на пустом баке.";
  }
  const stressPart = typeof metrics.stress === "number" && metrics.stress >= 60
    ? "стресс высокий — не усложнять"
    : "стресс не главный шум";
  const resourcePart = typeof metrics.bb === "number" && metrics.bb < 35
    ? "ресурс низкий — лучше ровная еда, не героизм на кофе"
    : "ресурс терпимый";
  return [
    "🍽 <b>Еда сейчас</b>",
    `По данным: ${resourcePart}, ${stressPart}.`,
    "Практично: нормальная простая еда + вода. Без тяжёлых экспериментов и без догоняться сладким как стратегией.",
  ].join("\n");
}

function buildLoadAnswer(cache) {
  const snapshot = getSnapshot(cache, currentDayKey());
  if (!hasUsableSnapshot(snapshot)) return "🏃 <b>Нагрузка</b>\nДанных нет. По режиму: только лёгкая активность, интенсивность не планировать.";
  const metrics = snapshotMetrics(snapshot);
  const soft = (typeof metrics.bb === "number" && metrics.bb < 40) || (typeof metrics.stress === "number" && metrics.stress >= 60);
  return [
    "🏃 <b>Нагрузка</b>",
    `По режиму: ${soft ? "лучше лёгкий формат" : "умеренный формат выглядит ок"}.`,
    `Факты: ${metricChips(snapshot, 3, "midday").join(" · ")}.`,
    soft ? "Лимит: без интенсивности и без добивки вечером." : "Лимит: не превращать нормальный день в тест на выживание.",
  ].join("\n");
}

function buildModeAnswer(cache) {
  const snapshot = getSnapshot(cache, currentDayKey());
  return [
    "🧭 <b>Режим сейчас</b>",
    statusForSnapshot(snapshot),
    `Фокус: ${SLOT_FOCUS.midday}.`,
    `Действие: ${actionForSnapshot("midday", snapshot)}.`,
    "",
    buildWhat15Message("midday", snapshot),
  ].join("\n");
}

function todayKeyboard(day, slot = "midday") {
  return {
    inline_keyboard: [
      [
        { text: "Почему?", callback_data: `why:${slot}:${day}` },
        { text: "По фактам", callback_data: `facts:${slot}:${day}` },
        { text: "Пожарь", callback_data: `roast:${slot}:${day}` },
        { text: "Что делать (15м)", callback_data: `what15:${slot}:${day}` },
      ],
      [
        { text: "✅ Попало", callback_data: `today_vote:${day}:yes` },
        { text: "➖ Частично", callback_data: `today_vote:${day}:partial` },
        { text: "❌ Мимо", callback_data: `today_vote:${day}:no` },
      ],
    ],
  };
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

export {
  buildTodayMessage,
  buildColorMessage,
  buildWeekMessage,
  buildWhat15Message,
  routeTextQuestion,
  buildStatsMessage,
  buildDebugHealthMessage,
  buildNoDataTodayMessage,
  collectColorVoteStats,
  collectTodayVoteStats,
  isoWeekIdFromDay,
  storeColorVote,
  storeTodayVote,
  todayKeyboard,
  votedKeyboard,
};

function json(payload, status = 200) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "content-type": "application/json; charset=utf-8" },
  });
}
