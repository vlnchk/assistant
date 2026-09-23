"""Граница сессии (ADR-0008) и просроченное ожидание менеджера (ADR-0009)."""

import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from assistant.session import (
    MANAGER_REMINDER_AFTER,
    SESSION_GAP,
    as_utc,
    current_session,
    reminder_due,
)
from bot import handlers, replies
from llm.tool_executor import apply_product_guard

NOW = datetime(2026, 9, 23, 12, 0, tzinfo=timezone.utc)


def _record(role, content, ago):
    # SQLite отдаёт время без пояса — воспроизводим именно это.
    created = (NOW - ago).replace(tzinfo=None)
    return SimpleNamespace(role=role, content=content, created_at=created)


class AsUtcTests(unittest.TestCase):
    def test_naive_sqlite_time_is_treated_as_utc(self):
        self.assertEqual(as_utc(datetime(2026, 9, 23, 12, 0)), NOW)

    def test_aware_time_is_converted(self):
        moscow = timezone(timedelta(hours=3))
        self.assertEqual(as_utc(datetime(2026, 9, 23, 15, 0, tzinfo=moscow)), NOW)


class CurrentSessionTests(unittest.TestCase):
    def test_continuous_dialogue_is_kept_whole(self):
        records = [
            _record("user", "a", timedelta(days=5)),
            _record("assistant", "b", timedelta(days=3)),
            _record("user", "c", timedelta(days=1)),
        ]
        self.assertEqual(current_session(records, NOW), records)

    def test_pause_longer_than_gap_cuts_older_messages(self):
        old = _record("user", "июль", timedelta(days=60))
        fresh = _record("user", "вчера", timedelta(days=1))
        self.assertEqual(current_session([old, fresh], NOW), [fresh])

    def test_pause_before_now_counts_too(self):
        """Клиент вернулся после долгой тишины — вся история в прошлом."""
        records = [_record("user", "a", timedelta(days=10))]
        self.assertEqual(current_session(records, NOW), [])

    def test_gap_is_measured_between_messages_not_from_the_start(self):
        """Клиент пишет раз в два дня неделю подряд — сессия жива."""
        records = [_record("user", str(d), timedelta(days=d)) for d in (8, 6, 4, 2)]
        self.assertEqual(current_session(records, NOW), records)

    def test_exactly_the_gap_is_still_the_same_session(self):
        records = [_record("user", "a", SESSION_GAP)]
        self.assertEqual(current_session(records, NOW), records)

    def test_missing_timestamp_ends_the_session(self):
        broken = SimpleNamespace(role="user", content="?", created_at=None)
        fresh = _record("user", "b", timedelta(hours=1))
        self.assertEqual(current_session([broken, fresh], NOW), [fresh])

    def test_july_vacancy_no_longer_steers_september_ad_question(self):
        """Сквозной сценарий бага, который внёс guard из M2.

        Без границы сессии июльская вакансия превращала сентябрьский вопрос
        о датах рекламы в просьбу прислать текст вакансии.
        """
        july = [
            _record("user", "вакансия", timedelta(days=70)),
            _record("assistant", replies.FAQ_VACANCY_OPTIONS, timedelta(days=70)),
        ]
        history = [
            {"role": r.role, "content": r.content}
            for r in current_session(july, NOW)
        ]

        name, _ = apply_product_guard("get_free_slots", {}, history)
        self.assertEqual(name, "get_free_slots")

        # Контроль: без границы сессии баг воспроизводится.
        raw = [{"role": r.role, "content": r.content} for r in july]
        name, _ = apply_product_guard("get_free_slots", {}, raw)
        self.assertEqual(name, "request_paid_vacancy")


class ReminderDueTests(unittest.TestCase):
    def test_within_window_no_reminder(self):
        self.assertFalse(reminder_due(NOW - timedelta(hours=1), NOW))

    def test_past_window_reminder(self):
        self.assertTrue(
            reminder_due(NOW - MANAGER_REMINDER_AFTER - timedelta(minutes=1), NOW)
        )

    def test_unknown_escalation_time_is_overdue(self):
        """Строки старше колонки: клиент, возможно, уже завис — спасаем."""
        self.assertTrue(reminder_due(None, NOW))

    def test_naive_sqlite_time_works(self):
        naive = (NOW - timedelta(hours=5)).replace(tzinfo=None)
        self.assertTrue(reminder_due(naive, NOW))


class AnswerWhileWaitingTests(unittest.IsolatedAsyncioTestCase):
    async def run_waiting(self, escalated_at):
        message = SimpleNamespace(answer=AsyncMock())
        session = SimpleNamespace(commit=AsyncMock())
        topic = SimpleNamespace(topic_id=7, escalated_at=escalated_at)
        rep = SimpleNamespace(telegram_id=42, first_name="Анна", username="anna")
        with (
            patch.object(handlers, "utc_now", return_value=NOW),
            patch.object(handlers, "_mirror_to_topic", AsyncMock()),
            patch.object(handlers, "_notify_escalation", AsyncMock()) as notify,
        ):
            await handlers._answer_while_waiting(None, message, session, rep, topic)
        return message, session, topic, notify

    async def test_within_window_client_gets_usual_reply_and_nobody_is_pinged(self):
        escalated = NOW - timedelta(hours=1)
        message, session, topic, notify = await self.run_waiting(escalated)

        message.answer.assert_awaited_once_with(replies.WAITING_HUMAN)
        notify.assert_not_awaited()
        self.assertEqual(topic.escalated_at, escalated)

    async def test_overdue_pings_managers_and_tells_client_honestly(self):
        message, session, topic, notify = await self.run_waiting(
            NOW - timedelta(hours=5)
        )

        message.answer.assert_awaited_once_with(replies.WAITING_HUMAN_REMINDED)
        notify.assert_awaited_once()
        self.assertIn("повторно", notify.await_args.args[2])
        # Окно перезапущено: следующее напоминание — не раньше чем через срок.
        self.assertEqual(topic.escalated_at, NOW)
        session.commit.assert_awaited()

    async def test_row_older_than_the_column_is_rescued(self):
        message, _, topic, notify = await self.run_waiting(None)

        message.answer.assert_awaited_once_with(replies.WAITING_HUMAN_REMINDED)
        notify.assert_awaited_once()
        self.assertEqual(topic.escalated_at, NOW)


if __name__ == "__main__":
    unittest.main()
