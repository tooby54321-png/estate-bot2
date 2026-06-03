# 🏠 Estate Bot Львів

## Запуск на Railway (безплатно) — 10 хвилин

### Крок 1 — Токен бота
1. Telegram → @BotFather → /newbot
2. Скопіюйте токен

### Крок 2 — Ваш ID
1. Telegram → @userinfobot → /start
2. Скопіюйте число

### Крок 3 — GitHub
1. github.com → New repository → estate-bot
2. Завантажте всі файли з цього ZIP

### Крок 4 — Railway
1. railway.app → Login with GitHub
2. New Project → Deploy from GitHub repo → estate-bot
3. Variables → додайте:
   BOT_TOKEN = ваш токен
   AGENT_CHAT_ID = ваш ID
4. Deploy → чекайте 60 секунд

### Як додавати об'єкти з CRM

**Спосіб 1 — файл objects.txt:**
- Кожен рядок = один об'єкт у форматі експорту RealtSoft
- Перезавантажте бота після оновлення файлу

**Спосіб 2 — через бота (для менеджера):**
- Напишіть боту команду /add_object
- Вставте рядок з CRM
- Бот запитає чи надіслати сповіщення підписникам

### Формат рядка CRM
active [num] [code] [Продаж/Оренда] [назва] [опис] [фото URL...] [координати]
