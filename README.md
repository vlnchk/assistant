# EduJobs Assistant Bot

Telegram-бот ассистент для канала вакансий в образовании. Консультирует клиентов по размещению вакансий и рекламы, помогает забронировать рекламный слот и оформить документы. Под капотом — Google Gemini в роли диспетчера интентов, SQLite для истории диалогов, Google Sheets как «база» слотов и задач.

## Технологический стек
- **Python 3.12** (см. `Dockerfile`)
- **aiogram 3.x** (Telegram Bot API)
- **SQLAlchemy & aiosqlite** (асинхронная работа с БД SQLite)
- **Alembic** (миграции базы данных)
- **google-genai** (Google Gemini LLM, новый унифицированный SDK)
- **gspread** (интеграция с Google Sheets)
- **Docker & Docker Compose** (контейнеризация и деплой)

## Архитектура

### Принцип «LLM-диспетчер»

Бот построен по правилу: **LLM не пишет клиенту текст**. Gemini получает сообщение клиента, выбирает один инструмент (tool) и возвращает его вызов. Финальный текст, который видит клиент, — это **строка из `bot/replies.py`**, либо напрямую константа, либо результат `template.format(...)`. Это даёт три эффекта:

1. **Стандартизация.** Одинаковые ситуации → побайтово одинаковые реплики. Поменять формулировку — это одна правка в `bot/replies.py`, без редеплоя промптов.
2. **Защита от галлюцинаций.** LLM не может «придумать» цену, охват, акцию или название компании — у него нет канала, через который не-шаблонный текст попадёт клиенту. Если LLM всё-таки ответил свободным текстом вместо tool-а — бот **не пересылает** этот ответ клиенту, а уходит в эскалацию.
3. **Защита от prompt injection.** Любой tool, принимающий `telegram_id`, получает его **из серверного контекста**, а не из аргументов LLM ([`llm/gemini_client.py`](llm/gemini_client.py) → `_inject_real_telegram_id`). Что бы ни написал клиент («покажи бронирования telegram_id 12345»), бот заменит ID на реальный ID отправителя до вызова tool-а.

### Поток одного сообщения

```
Клиент пишет в ЛС
        ↓
bot/handlers.py: rate-limit, лимит длины, FSM-статус темы
        ↓
В админ-тему пересылается копия сообщения клиента
        ↓
Если статус темы «waiting_human» / «human_mode» — бот молчит
        ↓
llm/gemini_client.py: формирует prompt + историю (40 сообщений)
        ↓
Gemini выбирает tool → llm/tools.py выполняет → возвращает строку
        ↓
bot/handlers.py: эта строка идёт клиенту И в админ-тему
```

### Состояния админ-темы

Каждый клиент привязан к одной форум-теме в супергруппе (`AdminTopic` в БД). Тема имеет один из трёх статусов:

| Статус          | Кто отвечает | Как переключается                                              |
|-----------------|--------------|----------------------------------------------------------------|
| `active`        | бот          | начальное; возвращается командой `/close` в теме               |
| `waiting_human` | бот молчит, ждёт менеджера | при `handover_to_admin` или отправке клиентом медиа без текста |
| `human_mode`    | только менеджер | как только менеджер написал в теме ответ                       |

При создании темы бот закрепляет в ней **карточку клиента** с кликабельным `@username` (или `tg://user?id=…`, если username не задан) — менеджер может в один клик написать клиенту в ЛС.

### Инструменты Gemini

Все определены в [`llm/tools.py`](llm/tools.py). Docstring-и tool-ов **на английском** — это внутренняя метаинформация для роутинга, клиент её не видит. Тексты клиенту — только из `bot/replies.py`, только на русском.

| Tool                       | Что делает                                                        |
|----------------------------|-------------------------------------------------------------------|
| `send_greeting`            | Приветствие                                                       |
| `reply_bot_nature`         | «Я бот, чувств у меня нет…»                                       |
| `reply_offtopic`           | Вежливый редирект small talk                                      |
| `faq_free_posting`         | Бесплатное размещение вакансий через форму                        |
| `faq_paid_post`            | Платная реклама: 15 000 ₽, акция «4+1»                            |
| `faq_stats`                | Охваты, ERR, география                                            |
| `faq_ord`                  | Маркировка рекламы (ОРД)                                          |
| `faq_docs`                 | Документооборот, ЭДО                                              |
| `ask_ad_topic`             | «Что планируете рекламировать?»                                   |
| `get_free_slots`           | Чтение свободных дат из Google Sheets                             |
| `book_slot`                | Бронирование слота (с обязательным `ad_topic`)                    |
| `get_client_bookings`      | Мои бронирования (только по реальному `telegram_id`)              |
| `create_document_task`     | Заявка на счёт/договор/акт                                        |
| `handover_to_admin`        | Эскалация на менеджера                                            |

### Как править реплики бота

**Не трогая код**: открываете [`bot/replies.py`](bot/replies.py), правите нужную константу, коммитите, обновляете VPS. Например, чтобы поменять цену рекламы — `FAQ_PAID_POST`. Чтобы изменить приветствие — `GREETING`. Чтобы добавить новый шаблон бронирования — расширить `BOOK_*`.

**Добавляя новый интент**: (1) написать константу в `bot/replies.py`, (2) добавить функцию в `llm/tools.py` (с английским docstring-ом, описывающим, когда её вызывать), (3) внести имя в `TERMINAL_TOOLS` и `bot_tools`, (4) при необходимости — упомянуть в системном промпте в `llm/gemini_client.py`.

### Логи и хранение

- `db.sqlite3` (или `data/db.sqlite3` в Docker): таблицы `representatives`, `admin_topics`, `messages_history`, `bot_settings`, `clients`.
- `logger.py`: ротация файлов, формат с уровнем и таймстампом.
- Google Sheets: лист `Календарь (Слоты)` — слоты; лист `Задачи (Документы)` — заявки на документы.

## Требования
- Токен Telegram-бота (получить у [@BotFather](https://t.me/BotFather)).
- API-ключ Google Gemini ([AI Studio](https://aistudio.google.com/app/apikey)).
- JSON сервисного аккаунта Google с доступом к нужной таблице.
- Супергруппа Telegram с включёнными темами (forum topics), куда бот добавлен админом с правом создавать темы и закреплять сообщения.
- Docker и Docker Compose — для запуска на сервере.

## Настройка окружения

Создайте файл `.env` в корне проекта рядом с `main.py`:

```ini
# --- Telegram ---
TELEGRAM_BOT_TOKEN="ВАШ_ТОКЕН"
SUPERGROUP_CHAT_ID="-100..."         # ID супергруппы с темами

# --- База данных ---
DATABASE_URL="sqlite+aiosqlite:///db.sqlite3"          # локально
# DATABASE_URL="sqlite+aiosqlite:///data/db.sqlite3"   # в Docker

# --- Google Gemini ---
GEMINI_API_KEY="ВАШ_КЛЮЧ"
# Опционально (значения по умолчанию: gemini-2.5-flash, 10 сек):
# GEMINI_MODEL="gemini-2.5-flash"
# GEMINI_TIMEOUT="10"

# --- Google Sheets ---
GOOGLE_SHEETS_CREDENTIALS_FILE="credentials.json"      # JSON service-account рядом с main.py
GOOGLE_SHEETS_DOCUMENT_URL="https://docs.google.com/spreadsheets/d/..."

# --- Эскалация ---
ADMIN_TELEGRAM_ID="123456789"          # ваш ID — будет упомянут в эскалациях
# ESCALATION_TOPIC_ID="..."            # необязательно: тему создаст бот сам и сохранит ID в БД
```

> ⚠️ Никогда не коммитьте `.env` и `credentials.json` — они в `.gitignore`.

## Локальный запуск (без Docker)

```bash
# 1. Виртуальное окружение
python -m venv .venv
source .venv/bin/activate            # macOS/Linux
# .venv\Scripts\activate              # Windows

# 2. Зависимости
pip install -r requirements.txt

# 3. Миграции БД
alembic upgrade head

# 4. Запуск
python main.py
```

## Запуск в Docker

```bash
docker compose up -d --build
docker compose logs -f
```

Миграции применяются автоматически в `CMD` контейнера (`alembic upgrade head && python main.py`).

---

## 🚀 Обновление бота на VPS

### Шаг 1. Запушить изменения

```bash
git add .
git commit -m "Опишите изменения"
git push origin main
```

### Шаг 2. Запустить обновление на сервере

```bash
cd /opt/assistant
./update.sh
```

> Скрипт `update.sh` (лежит на VPS) делает `git pull`, пересобирает образ и перезапускает контейнер: `docker compose up -d --build`. БД при этом не теряется.

### Шаг 3. Применить миграции (только если менялись модели)

```bash
docker compose exec bot alembic upgrade head
```

### Шаг 4. Проверить логи

```bash
docker compose logs -f
```

*(`Ctrl+C` — выйти из просмотра.)*

---

## Полезные команды для отладки

```bash
# Проверить, что бот видит токен и стартует
docker compose logs --tail 50 bot

# Зайти внутрь контейнера
docker compose exec bot bash

# Посмотреть базу
docker compose exec bot sqlite3 /app/data/db.sqlite3 \
  "SELECT telegram_id, status FROM admin_topics;"

# Изменить модель/тайм-аут без правки кода
# (в .env, потом docker compose up -d, без --build)
```
