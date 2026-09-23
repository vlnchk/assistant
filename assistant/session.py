"""Время в диалоге: где кончается сессия и когда ожидание просрочено.

Чистые функции без Telegram и БД, поэтому покрываются юнит-тестами.
Telegram-адаптер (`bot/handlers.py`) только подаёт им записи и время.

- Граница сессии — ADR-0008.
- Просроченное ожидание менеджера — ADR-0009.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Sequence

# Пауза, после которой клиент начинает с чистого листа. Считается по паузе,
# а не по возрасту первого сообщения: пока клиент пишет хотя бы раз в три дня,
# контекст сделки жив.
SESSION_GAP = timedelta(days=3)

# Бот обещает клиенту, что менеджер подключится «в течение нескольких часов».
# Через этот срок обещание считается нарушенным и менеджеру уходит напоминание.
MANAGER_REMINDER_AFTER = timedelta(hours=4)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: datetime | None) -> datetime | None:
    """SQLite отдаёт время без часового пояса; пишется оно в UTC."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def current_session(
    records: Sequence[Any],
    now: datetime,
    gap: timedelta = SESSION_GAP,
) -> list[Any]:
    """Return the tail of ``records`` that belongs to the ongoing session.

    ``records`` go oldest to newest and carry ``created_at``. Walking back from
    ``now``, the session ends at the first pause longer than ``gap``. Without
    this, a vacancy discussed in July steered an advertising question asked in
    September: the product guard reads history but not its age.
    """
    kept: list[Any] = []
    boundary = as_utc(now)
    for record in reversed(records):
        created = as_utc(getattr(record, "created_at", None))
        if created is None or boundary - created > gap:
            break
        kept.append(record)
        boundary = created
    kept.reverse()
    return kept


def reminder_due(
    escalated_at: datetime | None,
    now: datetime,
    after: timedelta = MANAGER_REMINDER_AFTER,
) -> bool:
    """Is the manager overdue for a client who is still waiting?

    An unknown escalation time counts as overdue: such rows predate the
    column, and a client stuck in one of them is exactly who needs rescuing.
    """
    escalated = as_utc(escalated_at)
    if escalated is None:
        return True
    return as_utc(now) - escalated > after
