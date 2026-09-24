# Выгрузка бэкапа в Google Drive падает на общем лимите rclone

- **Найдено:** 24.09.2026, при деплое M2–M4 (шаг 0 README)
- **Наблюдалось на сервере:** ночные запуски 31.08, 04–05.09, 09–11.09,
  14–16.09 и 21.09, два ручных 24.09
- **Статус:** воспроизведено
- **Закрывает:** — (кандидат в `../ROADMAP.md`)

## Симптом

`systemctl start edujobs-bot-backup.service` завершается `Result=exit-code`.
В журнале:

```
rclone: Failed to create file system for "gdrive:edujobs-bot-backups/assistant/daily":
googleapi: Error 403: Quota exceeded for quota metric 'Queries' and limit
'Requests per minute' of service 'drive.googleapis.com'
for consumer 'project_number:202264815644' — rateLimitExceeded
```

## Воспроизведение

На сервере:

```
systemctl start edujobs-bot-backup.service
journalctl -u edujobs-bot-backup.service -n 20 --no-pager
```

24.09.2026 — два запуска подряд с интервалом в минуту, оба 403. По журналу
сервиса (ведётся с 31.08) ночной таймер упал 10 раз из 25, из них три ночи
подряд — 09–11.09. Причина везде одна — 403 `rateLimitExceeded` от Drive.

## Механизм

`rclone config show gdrive` не содержит собственного `client_id`: rclone
ходит в Drive через общий OAuth-клиент по умолчанию. Его поминутный лимит
делят все пользователи rclone, поэтому отказы случайны по времени и
повторами не лечатся.

Скрипт `/usr/local/sbin/edujobs-bot-backup.sh` работает под
`set -Eeuo pipefail` и обрабатывает базы по очереди: сначала assistant, потом
cv_bot. Порядок внутри одной базы: `.backup` → `integrity_check` → `gzip` →
`gzip -t` → `rclone copy`.

## Последствия

- **Локальная копия assistant при этом создаётся и проверяется** —
  выгрузка идёт последним шагом. На деплое 24.09 локальный архив прошёл
  `integrity_check` и совпал с живой базой один в один: 128 тем,
  929 сообщений.
- **cv_bot в такие ночи не бэкапится вовсе, даже локально**: после сбоя
  выгрузки assistant скрипт завершается раньше, чем доходит до него.
  Локальных архивов cv_bot нет за 9, 10, 11, 14, 15, 16 и 21 сентября,
  хотя архивы assistant за те же дни лежат и срок хранения у них общий.
- Шаг 0 README («ожидается `Result=success`») формально не проходит.
  Деплой 24.09 выполнен на проверенной локальной копии по решению владельца.

## Как чинить

Завести собственный OAuth client_id в Google Cloud Console и прописать его
в remote `gdrive` (документация rclone: «Making your own client_id»).
Учётные данные вводит владелец аккаунта. Отдельно стоит развести бэкапы
двух баз, чтобы сбой выгрузки одной не отменял бэкап другой.
