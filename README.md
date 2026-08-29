# EduJobs Assistant Bot

**Русский** | [English](README.en.md)

Telegram-бот ассистент для канала вакансий в образовании. Консультирует клиентов по размещению вакансий и рекламы, помогает забронировать рекламный слот и оформить документы. Под капотом — Google Gemini 3.5 Flash-Lite в роли диспетчера интентов, SQLite для истории диалогов, Google Sheets как «база» слотов и задач.

## Технологический стек
- **Python 3.12.13** (см. `Dockerfile`)
- **aiogram 3.30.0** (Telegram Bot API)
- **SQLAlchemy 2.0.48 & aiosqlite 0.22.1** (асинхронная работа с БД SQLite)
- **Alembic 1.16.5** (миграции базы данных)
- **google-genai 2.8.0** (Google Gemini LLM, унифицированный SDK)
- **gspread 6.2.1** (интеграция с Google Sheets)
- **Docker & Docker Compose** (контейнеризация и деплой)

`requirements.txt` является полным lock-файлом: прямые и транзитивные
Python-зависимости закреплены точными версиями. После изменения зависимостей
нужно пересобрать lock-файл и повторить аудит уязвимостей, а не заменять `==`
на диапазоны версий.

### Модель безопасности production-контейнера

- процесс запускается как непривилегированный пользователь `10001:10001`;
- root filesystem контейнера доступен только для чтения;
- все Linux capabilities удалены, включён `no-new-privileges`;
- writable-данные находятся только в bind mount `./data:/app/data`;
- временные файлы разрешены только в отдельном `tmpfs` `/tmp`;
- `.env` читает Docker Compose и не копирует его в образ;
- Google service-account JSON исключён через `.dockerignore` и подключается
  отдельным read-only bind mount.

## Архитектура

### Принцип «LLM-диспетчер»

Бот построен по правилу: **LLM не пишет клиенту текст**. Каждое смысловое сообщение получает Gemini 3.5 Flash-Lite, который выбирает ровно один инструмент. Для составного информационного запроса используется один `answer_information`, который объединяет несколько утверждённых блоков и read-only результат календаря. Финальный текст, который видит клиент, — это **строка из `bot/replies.py`**, результат `template.format(...)` или композиция таких строк. Это даёт три эффекта:

1. **Стандартизация.** Одинаковые ситуации → побайтово одинаковые реплики. Поменять формулировку — это одна правка в `bot/replies.py`, без редеплоя промптов.
2. **Защита от галлюцинаций.** LLM не может «придумать» цену, охват, акцию или название компании — у него нет канала, через который не-шаблонный текст попадёт клиенту. Если LLM всё-таки ответил свободным текстом вместо tool-а — бот **не пересылает** этот ответ клиенту, а уходит в эскалацию.
3. **Защита от prompt injection.** Идентификатор Telegram, контакт и ссылка на админ-диалог подставляются **из серверного контекста**, а не из аргументов LLM ([`llm/tool_executor.py`](llm/tool_executor.py) → `inject_server_context`). Что бы ни написал клиент («покажи бронирования telegram_id 12345»), бот заменит ID на реальный ID отправителя до вызова tool-а.

Выбор маршрута и исполнение разделены: модель завершает классификацию до запуска инструмента. Поэтому изменяющее действие выполняется не более одного раза.

В production используется одна модель. Автоматического fallback, второго
LLM-провайдера и модельного каскада нет.

Транспортные события без смысловой классификации обрабатываются в Telegram-
обёртке: `/start` и `/help` получают детерминированное приветствие, а медиа без
подписи — подтверждение и передачу менеджеру. Весь обычный пользовательский
текст, включая благодарности, проходит через Gemini.

Информационные темы можно объединять. Изменяющее действие — бронирование, заявка на документ или категоризированная передача операции менеджеру — за один ход допускается только одно.

### Поток одного сообщения

```
Клиент пишет в ЛС
        ↓
bot/handlers.py: rate-limit, лимит длины, FSM-статус темы
        ↓
В админ-тему пересылается копия сообщения клиента
        ↓
Технические команды и медиа без подписи обрабатываются транспортной обёрткой
        ↓
Для «waiting_human» / «human_mode» автоматическая LLM-маршрутизация не запускается
        ↓
assistant/engine.py: принимает нейтральный TurnRequest
        ↓
Gemini 3.5 Flash-Lite выбирает один бизнес-tool
        ↓
llm/tool_executor.py валидирует и исполняет tool ровно один раз
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
| `reply_gratitude`          | Ответ на самостоятельную благодарность                            |
| `reply_bot_nature`         | «Я бот, чувств у меня нет…»                                       |
| `reply_offtopic`           | Вежливый редирект small talk                                      |
| `faq_free_posting`         | Бесплатное размещение вакансий через форму                        |
| `faq_paid_post`            | Платная реклама (не вакансии): 15 000 ₽, акция «4+1»              |
| `faq_stats`                | Охваты, ERR, география                                            |
| `faq_ord`                  | Маркировка рекламы (ОРД)                                          |
| `faq_docs`                 | Документооборот, ЭДО                                              |
| `answer_information`      | Один или несколько FAQ-блоков плюс read-only проверка календаря  |
| `ask_ad_topic`             | «Что планируете рекламировать?»                                   |
| `get_free_slots`           | Чтение свободных дат из Google Sheets                             |
| `book_slot`                | Бронирование слота (с обязательным `ad_topic`)                    |
| `get_client_bookings`      | Мои бронирования (только по реальному `telegram_id`)              |
| `create_document_task`     | Заявка на документ и служебное уведомление без остановки бота     |
| `request_publication_support` | Статус, исправление, удаление, ускорение и другие операции      |
| `request_mutual_pr_support` | Эскалация предложений по взаимопиару / «ВП»                      |
| `handover_to_admin`        | Эскалация на менеджера                                            |

### Как править реплики бота

**Не трогая код**: открываете [`bot/replies.py`](bot/replies.py), правите нужную константу, коммитите, обновляете VPS. Например, чтобы поменять цену рекламы — `FAQ_PAID_POST`. Чтобы изменить приветствие — `GREETING`. Чтобы добавить новый шаблон бронирования — расширить `BOOK_*`.

**Добавляя новый интент**: (1) написать константу в `bot/replies.py`, (2) добавить функцию в `llm/tools.py` (с английским docstring-ом, описывающим, когда её вызывать), (3) внести имя в `TERMINAL_TOOLS`, `bot_tools` и `TOOL_FUNCTIONS` в `llm/tool_executor.py`, (4) при необходимости — упомянуть в системном промпте в `llm/gemini_client.py`.

### Логи и хранение

- `db.sqlite3` (или `data/db.sqlite3` в Docker): таблицы `representatives`, `admin_topics`, `messages_history`, `bot_settings`, `clients`.
- `/app/data/bot.log` в Docker: ротация до 10 МБ, хранится 5 архивных файлов.
  Путь задаётся переменной `BOT_LOG_FILE`; писать лог рядом с кодом нельзя,
  потому что root filesystem контейнера read-only.
- Google Sheets: лист `Календарь (Слоты)` — слоты; лист `Задачи (Документы)` — заявки на документы. Для новых заявок лист документов автоматически получает колонку `Реквизиты / комментарий`, если её ещё нет.
- После успешного создания задачи на документ бот отправляет служебное уведомление в тему эскалаций, но не переводит клиентский диалог в `waiting_human`.
- Если сохранённая тема эскалаций была удалена, бот один раз создаёт новую тему, сохраняет её ID в БД и повторяет отправку уведомления.

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
# Опционально (показаны значения по умолчанию):
# GEMINI_MODEL="gemini-3.5-flash-lite"
# GEMINI_TIMEOUT="10"
# ASSISTANT_METRICS_ENABLED="0"    # 1 = писать trace-метрики в лог
# GEMINI_LITE_MODEL поддерживается как устаревший alias для GEMINI_MODEL.

# --- Google Sheets ---
GOOGLE_SHEETS_CREDENTIALS_FILE="credentials.json"      # локальный запуск
# На VPS compose ожидает: ejs-admin-bot-a275ca626752.json
GOOGLE_SHEETS_DOCUMENT_URL="https://docs.google.com/spreadsheets/d/..."

# --- Эскалация ---
ADMIN_TELEGRAM_ID="123456789"          # ваш ID — будет упомянут в эскалациях
# ESCALATION_TOPIC_ID="..."            # необязательно: тему создаст бот сам и сохранит ID в БД
```

> ⚠️ Никогда не коммитьте `.env` и service-account JSON — они исключены из
> git и Docker build context. На VPS оба файла должны иметь режим `600`.

Production-права на VPS:

```bash
cd /opt/assistant
chown root:root .env
chmod 600 .env
chown 10001:10001 ejs-admin-bot-a275ca626752.json
chown -R 10001:10001 data
chmod 600 ejs-admin-bot-a275ca626752.json
chmod 700 data
chmod 600 data/db.sqlite3
```

JSON принадлежит UID 10001, чтобы приложение могло его читать, но bind mount
внутри контейнера остаётся read-only.

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

## Локальное тестирование без Telegram

Интерактивный режим использует тот же `AssistantEngine`, но безопасный
`DryRunToolExecutor`: модель вызывается по-настоящему, а бронирования, документы,
Google Sheets и Telegram-уведомления не изменяются.

```bash
python scripts/eval_assistant.py --interactive
```

Пакетный прогон стартового JSONL-датасета:

```bash
python scripts/eval_assistant.py \
  --dataset tests/fixtures/dialog_cases.jsonl \
  --metrics
```

Результаты сохраняются в `eval_runs/<timestamp>/results.csv`; рядом создаётся
`summary.json` с точностью маршрутизации, ошибками, токенами и
latency mean/p50/p95/max. Флаги `--metrics` и `--no-metrics` управляют выводом
метрик в таблицу. Для ручной оценки в CSV предусмотрены `manual_score` и
`manual_notes`. Оценки используют шкалу 1–5; результат считается принятым при
оценке 4 или 5. Датасет допускает несколько семантически эквивалентных tools
через `expected_tools`.

Безопасная подготовка кандидатов из Telegram Desktop JSON export:

```bash
python scripts/import_telegram_export.py \
  --input /path/to/result.json \
  --output /tmp/telegram-eval-candidates.jsonl
```

Импортёр восстанавливает forum topics, оставляет только клиентские реплики и
маскирует распространённые идентификаторы. Результат всё равно нужно проверить
вручную; исходный Telegram export и непроверенные кандидаты коммитить нельзя.

Текущий baseline: 65 диалоговых кейсов, 73 хода, включая 55 обезличенных
реальных диалогов. Строгая маршрутизация — 71/73 (97,26%), ошибок нет. Ручная
проверка заполнена для 73/73 ходов: средняя оценка 4,89/5, принято 70/73
(95,89%). Mean latency 765 мс, p50 731 мс, p95 1 088 мс, max 1 283 мс;
суммарно 403 284 токена.

## Запуск в Docker

```bash
docker compose config --quiet
docker compose up -d --build
docker compose logs -f bot
```

Миграции применяются автоматически в `CMD` контейнера (`alembic upgrade head && python main.py`).

Проверка hardening после запуска:

```bash
docker inspect -f 'user={{.Config.User}} readonly={{.HostConfig.ReadonlyRootfs}} security={{json .HostConfig.SecurityOpt}} capdrop={{json .HostConfig.CapDrop}}' edujobs_bot
docker inspect -f '{{range .Mounts}}{{.Destination}} rw={{.RW}}{{println}}{{end}}' edujobs_bot
docker exec edujobs_bot id
```

Ожидается: `user=10001:10001`, `readonly=true`, `no-new-privileges`,
`capdrop=["ALL"]`; `/app/data` имеет `rw=true`, JSON — `rw=false`.

---

## 🚀 Обновление бота на VPS

### Шаг 0. Сделать и проверить бэкап

```bash
sudo systemctl start edujobs-bot-backup.service
sudo systemctl show edujobs-bot-backup.service \
  -p Result -p ExecMainStatus -p ActiveState
```

Ожидается `Result=success` и `ExecMainStatus=0`. Скрипт `update.sh` сам
бэкап не создаёт, поэтому пропускать этот шаг нельзя.

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

> Скрипт `update.sh` лежит только на VPS. Он делает `git pull`, пересобирает
> образ, перезапускает контейнер и удаляет dangling-образы. Запускать его нужно
> строго из `/opt/assistant` и только после успешного бэкапа.

### Шаг 3. Проверить результат

```bash
docker compose ps
docker compose logs --tail 100 bot
docker exec edujobs_bot id
sqlite3 /opt/assistant/data/db.sqlite3 'PRAGMA integrity_check;'
```

Миграции уже выполняются автоматически при старте. Повторный ручной
`alembic upgrade head` обычно не нужен.

---

## Бэкапы и восстановление

Обе SQLite-базы сервера бэкапятся единым systemd-сервисом:

- unit: `edujobs-bot-backup.service`;
- timer: ежедневно в 04:30 по времени сервера с задержкой до 15 минут;
- скрипт: `/usr/local/sbin/edujobs-bot-backup.sh`;
- локально: `/opt/bot-backups/daily/assistant/`, ротация 14 дней;
- Google Drive: `gdrive:edujobs-bot-backups/assistant/daily`, ротация 30 дней.

Скрипт использует штатную SQLite-команду `.backup`, затем проверяет
`PRAGMA integrity_check`, сжимает архив и проверяет gzip.

Проверить таймер и последние архивы:

```bash
systemctl list-timers edujobs-bot-backup.timer --no-pager
systemctl status edujobs-bot-backup.service --no-pager
ls -lh /opt/bot-backups/daily/assistant/
rclone lsf gdrive:edujobs-bot-backups/assistant/daily --files-only | tail
```

Проверка восстановления без остановки production:

```bash
restore_dir=$(mktemp -d /tmp/assistant-restore.XXXXXX)
archive=$(ls -1t /opt/bot-backups/daily/assistant/*.sqlite3.gz | head -n 1)
gzip -cd "$archive" > "$restore_dir/db.sqlite3"
sqlite3 "$restore_dir/db.sqlite3" 'PRAGMA integrity_check;'
```

Ожидаемый результат — `ok`. Подменять production-базу этим файлом можно
только после `docker compose stop bot`, отдельной копии текущей базы и
повторной проверки владельца `10001:10001` и режима `600`.

---

## Полезные команды для отладки

```bash
# Проверить, что бот видит токен и стартует
docker compose logs --tail 50 bot

# Зайти внутрь контейнера
docker compose exec bot sh

# Посмотреть базу с хоста (sqlite3 CLI в production-образ не устанавливается)
sqlite3 /opt/assistant/data/db.sqlite3 \
  "SELECT telegram_id, status FROM admin_topics;"

# Изменить модель/тайм-аут без правки кода
# (в .env, потом docker compose up -d, без --build)
```

---

Последнее обновление: 29 августа 2026.
