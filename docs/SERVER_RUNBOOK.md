# Эксплуатация на сервере

Деплой, обновление, откат, бэкапы и диагностика бота на VPS. Устройство
системы и локальный запуск — в [README.ru.md](../README.ru.md).

## Права на файлы

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

## Обновление бота на VPS

### Шаг 0. Сделать и проверить бэкап

```bash
sudo systemctl start edujobs-bot-backup.service
sudo systemctl show edujobs-bot-backup.service \
  -p Result -p ExecMainStatus -p ActiveState
```

Ожидается `Result=success` и `ExecMainStatus=0`. Скрипт `update.sh` сам
бэкап не создаёт, поэтому пропускать этот шаг нельзя.

Если выгрузка в Google Drive упала (`Result=exit-code`, в журнале `rclone` и
`403 rateLimitExceeded`), локальный архив при этом обычно создан и проверен —
выгрузка идёт последним шагом. Проверить его можно рецептом из раздела
«Бэкапы и восстановление»; решение деплоить на локальной копии — за
владельцем. Причина и лечение — [`dev/findings/2026-09-24-offsite-backup-rate-limit.md`](../dev/findings/2026-09-24-offsite-backup-rate-limit.md).

### Шаг 1. Запушить изменения

Работа ведётся в `dev`, прод собирается из `main`. `main` должен быть предком
`dev` — тогда пуш является перемоткой вперёд, без `--force`:

```bash
git fetch origin
git merge-base --is-ancestor origin/main dev && git push origin dev dev:main
```

### Шаг 2. Запустить обновление на сервере

Сначала закрепить текущий образ отдельным тегом: `update.sh` заканчивается
`docker image prune -f`, и без тега старый образ будет удалён — а он самый
быстрый путь отката.

```bash
cd /opt/assistant
docker tag "$(docker inspect edujobs_bot --format '{{.Image}}')" \
  "assistant-bot:rollback-$(git rev-parse --short HEAD)"
./update.sh
```

Откат без пересборки (на проде пока не проверялся):

```bash
docker tag assistant-bot:rollback-<коммит> assistant-bot:latest
docker compose up -d --force-recreate
```

Код в `/opt/assistant` при этом остаётся новым, и следующий `update.sh`
соберёт его снова — поэтому после отката сначала исправить проблему в `dev`.
Миграции, которые только добавляют колонки, откат образа не ломает: старый
код их не замечает.

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

## Полезные команды для отладки

```bash
# Проверить, что бот видит токен и стартует
docker compose logs --tail 50 bot

# Зайти внутрь контейнера
docker compose exec bot sh

# Посмотреть базу с хоста (sqlite3 CLI в production-образ не устанавливается)
sqlite3 /opt/assistant/data/db.sqlite3 \
  "SELECT telegram_id, status FROM admin_topics;"

# Проверить docker-compose.yml
docker compose config --quiet

# Изменить модель/тайм-аут без правки кода
# (в .env, потом docker compose up -d, без --build)
```
