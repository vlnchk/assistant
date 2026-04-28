# EduJobs Assistant Bot

Telegram-бот ассистент, использующий Google Gemini для общения с пользователями, SQLite для хранения данных и Google Sheets для синхронизации/логирования. 

## Технологический стек
- **Python 3.10+**
- **aiogram 3.x** (Telegram Bot API)
- **SQLAlchemy & aiosqlite** (Асинхронная работа с БД SQLite)
- **Alembic** (Миграции базы данных)
- **google-generativeai** (Google Gemini LLM)
- **gspread** (Интеграция с Google Sheets)
- **Docker & Docker Compose** (Контейнеризация и деплой)

## Требования
Для запуска проекта вам понадобятся:
- Токен Telegram бота (получить у [@BotFather](https://t.me/BotFather))
- API ключ Google Gemini
- JSON файл с ключами сервисного аккаунта Google (для работы с таблицами)
- Установленный Docker и Docker Compose (для запуска на сервере/VPS)

## Настройка окружения
1. Создайте файл `.env` в корне проекта (рядом с `main.py`).
2. Заполните необходимые переменные (пример):
```ini
# Telegram Configuration
TELEGRAM_BOT_TOKEN="ВАШ_ТОКЕН"
SUPERGROUP_CHAT_ID="-100... ID группы для эскалации"
OWNER_ID="ВАШ_ID"

# Database
DATABASE_URL="sqlite+aiosqlite:///db.sqlite3" # для локальной разработки
# DATABASE_URL="sqlite+aiosqlite:///data/db.sqlite3" # для VPS (Docker)

# Google Gemini API
GEMINI_API_KEY="ВАШ_КЛЮЧ"

# Google Sheets
GOOGLE_SHEETS_CREDENTIALS_FILE="имя_файла_с_ключами.json"
GOOGLE_SHEETS_DOCUMENT_URL="ссылка_на_таблицу"

# Escalation
ADMIN_TELEGRAM_ID="ВАШ_ID"
ESCALATION_TOPIC_ID="ID_ТОПИКА"
```

## Локальный запуск (без Docker)
1. Создайте виртуальное окружение:
```bash
python -m venv .venv
source .venv/bin/activate  # Для macOS/Linux
# .venv\Scripts\activate  # Для Windows
```
2. Установите зависимости:
```bash
pip install -r requirements.txt
```
3. Примените миграции базы данных:
```bash
alembic upgrade head
```
4. Запустите бота:
```bash
python main.py
```

---

## 🚀 Как обновить бота на VPS после изменений в коде

Этот процесс описывает шаги, которые нужно выполнить на вашем сервере (VPS), если вы внесли изменения в код на локальном компьютере.

### Шаг 1: Доставьте новый код на сервер
Самый правильный способ — использовать `git`. 
1. Сделайте push ваших изменений с локального компьютера в репозиторий (GitHub/GitLab/Bitbucket):
   ```bash
   git add .
   git commit -m "Опишите ваши изменения"
   git push origin main
   ```
2. Подключитесь к VPS по SSH, перейдите в папку проекта и скачайте изменения:
   ```bash
   cd /путь/к/вашему/проекту  # перейдите в директорию с проектом
   git pull origin main
   ```
*(Если вы не используете git, вы можете скопировать обновленные файлы через `scp` или любой SFTP клиент, просто заменив старые файлы новыми на сервере).*

### Шаг 2: Пересоберите Docker-образ
Так как код изменился, нужно пересобрать образ, чтобы новые файлы (ваши изменения) попали внутрь контейнера бота. Выполните команду находясь в директории с `docker-compose.yml`:
```bash
docker compose build
```
*(Или `docker-compose build`, если у вас старая версия docker-compose).*

### Шаг 3: Перезапустите контейнер с ботом
Запустите команду для поднятия контейнера. Docker Compose сам поймет, что образ изменился, остановит старый контейнер, удалит его и создаст новый.
```bash
docker compose up -d
```
> **Важно:** Ваша база данных не пострадает при этом процессе, так как папка `data/` сохраняется на сервере и прокидывается в контейнер через `volumes`.

### Шаг 4: Примените миграции БД (только если меняли структуру таблиц)
Если в ваших изменениях были затронуты таблицы базы данных (например, вы добавили новую колонку и создали новую миграцию Alembic), нужно обновить базу данных внутри запущенного контейнера:
```bash
docker compose exec bot alembic upgrade head
```
*(Эту команду нужно выполнять уже после того, как новый контейнер запущен в Шаге 3).*

### Шаг 5: Проверьте логи
Убедитесь, что бот успешно запустился, подхватил новые изменения и нет ошибок:
```bash
docker compose logs -f
```
*(Для выхода из просмотра логов нажмите `Ctrl+C`).*
