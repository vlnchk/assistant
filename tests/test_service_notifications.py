import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import SendMessage

from bot import handlers


class RecoveringBot:
    def __init__(self):
        self.thread_ids = []

    async def send_message(self, **kwargs):
        self.thread_ids.append(kwargs["message_thread_id"])
        if len(self.thread_ids) == 1:
            raise TelegramBadRequest(
                method=SendMessage(chat_id=kwargs["chat_id"], text=kwargs["text"]),
                message="Bad Request: message thread not found",
            )
        return SimpleNamespace(message_id=1)


class ServiceNotificationTests(unittest.IsolatedAsyncioTestCase):
    async def test_deleted_service_topic_is_recreated_and_retried_once(self):
        bot = RecoveringBot()

        with (
            patch.object(handlers, "SUPERGROUP_CHAT_ID", -100123),
            patch.object(
                handlers,
                "get_or_create_escalation_topic",
                AsyncMock(side_effect=[72, 73]),
            ) as get_topic,
        ):
            sent = await handlers._send_to_service_topic(bot, "test", "Тест")

        self.assertTrue(sent)
        self.assertEqual(bot.thread_ids, [72, 73])
        get_topic.assert_any_await(bot, stale_topic_id=72)

    async def test_document_alert_uses_service_topic_without_handover(self):
        rep = SimpleNamespace(
            telegram_id=42,
            first_name="Анна",
            username="anna",
        )
        admin_topic = SimpleNamespace(topic_id=77)

        with (
            patch.object(handlers, "SUPERGROUP_CHAT_ID", -100123),
            patch.object(handlers, "ADMIN_TELEGRAM_ID", "99"),
            patch.object(
                handlers,
                "_send_to_service_topic",
                AsyncMock(return_value=True),
            ) as send_alert,
        ):
            await handlers._notify_document_task(
                AsyncMock(),
                rep,
                "Подготовить документ: счёт; компания: ООО Ромашка; ИНН: 7701234567",
                admin_topic,
            )

        text = send_alert.await_args.args[1]
        self.assertIn("НОВАЯ ЗАДАЧА ПО ДОКУМЕНТАМ", text)
        self.assertIn("ООО Ромашка", text)
        self.assertIn("7701234567", text)
        self.assertEqual(send_alert.await_args.args[2], "Документы")


if __name__ == "__main__":
    unittest.main()
