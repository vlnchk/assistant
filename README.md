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

Этот процесс описывает шаги для обновления бота, когда вы внесли изменения на локальном компьютере.

### Шаг 1: Отправьте изменения на GitHub
На локальном компьютере добавьте файлы, сделайте коммит и отправьте их:
```bash
git add .
git commit -m "Опишите ваши изменения"
git push origin main
```

### Шаг 2: Запустите скрипт обновления на VPS
Подключитесь к серверу по SSH, перейдите в папку с проектом (например, `/opt/assistant`) и запустите скрипт:
```bash
cd /opt/assistant
./update.sh
```

> **Что делает этот скрипт?** Он автоматически скачает новые изменения с GitHub (`git pull`), пересоберёт Docker-образ и перезапустит контейнер (`docker compose up -d --build`), а также очистит старые временные файлы сборки. Ваша база данных при этом не пострадает!

### Шаг 3: Примените миграции БД (только если меняли структуру таблиц)
Если вы меняли таблицы базы данных, обновите БД внутри уже запущенного контейнера:
```bash
docker compose exec bot alembic upgrade head
```

### Шаг 4: Проверьте логи
Убедитесь, что бот успешно запустился и подхватил новую версию:
```bash
docker compose logs -f
```
*(Для выхода из просмотра логов нажмите `Ctrl+C`).*
