# ai-health-stealth-bot


Личный Telegram-бот: берёт данные из Garmin Connect, на их основе формирует короткие дневные инсайты на русском (через Gemini) и присылает тебе в Telegram **три раза в день** — в 08:30, 13:00 и 19:30 по Парижу. Сервер не нужен, всё работает на GitHub Actions.

---

## Что нужно настроить (GitHub Secrets)

В репозитории: **Settings → Secrets and variables → Actions** — добавь секреты:

| Секрет | Что подставить |
|--------|-----------------|
| `TELEGRAM_BOT_TOKEN` | Токен бота от [@BotFather](https://t.me/BotFather) |
| `TELEGRAM_CHAT_ID` | Твой chat ID (можно узнать у [@userinfobot](https://t.me/userinfobot)) |
| `GARMIN_EMAIL` | Логин Garmin Connect |
| `GARMIN_PASSWORD` | Пароль Garmin Connect |
| `GEMINI_API_KEY` | Ключ Google AI (Gemini) |
| `GEMINI_MODEL` | Модель, например `gemini-1.5-flash` |
| `GEMINI_SYSTEM_PROMPT` | Полный системный промпт для бота (инструкция, как он должен себя вести и что писать) |

После этого включи Actions для репозитория — расписание само будет подтягивать данные Garmin и отправлять сообщения.

---

## Запуск на своём компьютере (по желанию)

1. Установи зависимости:  
   `pip install -r requirements.txt`
2. Задай те же переменные окружения (через `.env` или `export`).
3. Синхронизация с Garmin:  
   `python main.py sync`
4. Отправить утреннее сообщение в Telegram:  
   `python main.py push morning`  
   (вместо `morning` можно `midday` или `evening`).

Ручной тест в GitHub: вкладка **Actions** → workflow **Push Daily Insights** → **Run workflow**.

---

## Как выглядят сообщения

- Коротко и по делу, без «мотивационного» тона.
- Никакой вины и стыда; лёгкий подкол допустим.
- Никаких диагнозов и дозировок добавок.
- Если данных мало или они с ошибками — бот пишет об этом и снижает уверенность.
- Формат: эмодзи-матрица (до 3 строк), до 3 причин, поведенческий фрейм 🟢🟡🔴, одна строка «цена игнора», маркер уверенности, одна человечная фраза в конце.
