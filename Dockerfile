FROM python:3.12.13-slim

# Устанавливаем рабочую директорию внутри контейнера
WORKDIR /app

# Отключаем создание .pyc файлов и буферизацию вывода
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Создаём непривилегированного пользователя приложения с постоянным UID/GID.
RUN groupadd --system --gid 10001 bot \
    && useradd --system --uid 10001 --gid bot --home-dir /nonexistent --shell /usr/sbin/nologin bot

# Копируем файл с зависимостями и устанавливаем их
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем весь проект в контейнер
COPY . .

# Код и секреты недоступны приложению на запись; writable-каталог подключается volume-ом.
USER 10001:10001

# Команда, которая выполняется при старте контейнера
# Сначала применяем миграции БД, затем запускаем бота
CMD ["sh", "-c", "alembic upgrade head && python main.py"]
