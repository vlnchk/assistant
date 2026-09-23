import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock
from unittest.mock import patch

from google.genai import types

from assistant.engine import AssistantEngine
from assistant.models import TurnRequest
from bot import replies
from llm.gemini_client import gemini_client
from llm.tool_executor import ProductionToolExecutor
from llm.tools import (
    answer_information,
    create_document_task,
    request_mutual_pr_support,
    request_publication_support,
)


class InformationComposerTests(unittest.TestCase):
    def test_paid_vacancy_has_its_own_price_and_format_rules(self):
        result = answer_information(["vacancy_options"])

        self.assertIn("от 3 000 ₽", result)
        self.assertIn("прямые контакты можно не указывать", result)
        self.assertIn("можно добавить изображение", result)
        self.assertNotIn("15 000 ₽", result)
        self.assertNotIn("в топе 24 часа", result)

    def test_advertising_price_remains_separate_from_paid_vacancy(self):
        self.assertIn("15 000 ₽", replies.FAQ_PAID_POST)

    def test_combines_all_requested_canonical_blocks(self):
        result = answer_information(["paid_post", "ad_formats", "stats"])

        self.assertIn(replies.FAQ_PAID_POST, result)
        self.assertIn(replies.FAQ_AD_FORMATS, result)
        self.assertIn(replies.FAQ_STATS, result)
        self.assertEqual(result.count("\n\n"), 2)

    def test_deduplicates_topics(self):
        result = answer_information(["paid_post", "paid_post"])

        self.assertEqual(result, replies.FAQ_PAID_POST)

    @patch("llm.tools.sheets_service")
    def test_combines_static_answer_with_month_slots(self, sheets):
        sheets.get_free_slots.return_value = "Свободные даты в сентябре: 5 сентября."

        result = answer_information(
            ["paid_post", "free_slots"],
            month="сентябрь",
        )

        self.assertIn(replies.FAQ_PAID_POST, result)
        self.assertIn("5 сентября", result)
        sheets.get_free_slots.assert_called_once_with("сентябрь")

    def test_unknown_topic_becomes_handover(self):
        result = answer_information(["invented-by-model"])

        self.assertTrue(result.startswith("__HANDOVER__:"))

    def test_unknown_topic_cannot_be_silently_dropped_from_composite(self):
        result = answer_information(["paid_post", "invented-by-model"])

        self.assertTrue(result.startswith("__HANDOVER__:"))


class DocumentTaskTests(unittest.TestCase):
    @patch("llm.tools.sheets_service")
    def test_requests_missing_company_and_inn_without_writing(self, sheets):
        result = create_document_task("счёт", "123")

        self.assertEqual(result, replies.DOC_NEED_DETAILS)
        sheets.create_document_task.assert_not_called()

    @patch("llm.tools.sheets_service")
    def test_requests_only_inn_when_company_is_already_known(self, sheets):
        result = create_document_task("счёт", "123", company="Ессеншиаллуки")

        self.assertEqual(result, replies.DOC_NEED_INN)
        sheets.create_document_task.assert_not_called()

    @patch("llm.tools.sheets_service")
    def test_requests_only_company_when_inn_is_already_known(self, sheets):
        result = create_document_task("счёт", "123", inn="7701234567")

        self.assertEqual(result, replies.DOC_NEED_COMPANY)
        sheets.create_document_task.assert_not_called()

    @patch("llm.tools.sheets_service")
    def test_rejects_invalid_inn(self, sheets):
        result = create_document_task("счёт", "123", company="ООО Тест", inn="123")

        self.assertEqual(result, replies.DOC_BAD_INN)
        sheets.create_document_task.assert_not_called()

    @patch("llm.tools.sheets_service")
    def test_preserves_company_inn_and_requisites(self, sheets):
        sheets.create_document_task.return_value = replies.DOC_CREATED.format(
            doc_type="счёт"
        )

        result = create_document_task(
            "счёт",
            "123",
            company="ООО Ромашка",
            inn="7701234567",
            requisites="БИК 044525000",
            contact="@anna",
            link="https://t.me/c/1/2",
        )

        self.assertTrue(result.startswith("__ADMIN_NOTIFY_JSON__:"))
        payload = json.loads(result.split(":", 1)[1])
        self.assertEqual(
            payload["client_reply"],
            replies.DOC_CREATED.format(doc_type="счёт"),
        )
        self.assertIn("ООО Ромашка", payload["notification"])
        self.assertIn("7701234567", payload["notification"])
        sheets.create_document_task.assert_called_once_with(
            "ООО Ромашка",
            "7701234567",
            "счёт",
            "@anna",
            "https://t.me/c/1/2",
            telegram_id="123",
            requisites="БИК 044525000",
        )


class PublicationSupportTests(unittest.TestCase):
    def test_asks_for_missing_post_link_before_escalation(self):
        self.assertEqual(
            request_publication_support("edit", details="исправить должность"),
            replies.PUBLICATION_NEED_LINK,
        )

    def test_ready_request_contains_structured_handover(self):
        result = request_publication_support(
            "edit",
            publication_link="https://t.me/edujobs/123",
            details="исправить название",
        )

        self.assertTrue(result.startswith("__HANDOVER_JSON__:"))
        payload = json.loads(result.split(":", 1)[1])
        self.assertIn("исправить публикацию", payload["reason"])
        self.assertEqual(payload["client_reply"], replies.PUBLICATION_SUPPORT_SENT)


class MutualPrSupportTests(unittest.TestCase):
    def test_mutual_pr_creates_categorized_handover(self):
        result = request_mutual_pr_support()

        self.assertTrue(result.startswith("__HANDOVER_JSON__:"))
        payload = json.loads(result.split(":", 1)[1])
        self.assertIn("взаимопиару", payload["reason"])
        self.assertEqual(payload["client_reply"], replies.MUTUAL_PR_HANDOVER)


class TrustedContextTests(unittest.TestCase):
    def test_document_identity_contact_and_link_are_server_controlled(self):
        """Через prepare() — тот же вызов, что делает движок в проде."""
        args = ProductionToolExecutor().prepare(
            "create_document_task",
            {
                "doc_type": "счёт",
                "telegram_id": "attacker-id",
                "contact": "@attacker",
                "link": "https://attacker.invalid",
            },
            {
                "telegram_id": 42,
                "username": "real_user",
                "first_name": "Анна",
                "dialog_link": "https://t.me/c/10/20",
            },
        )

        self.assertEqual(args["telegram_id"], "42")
        self.assertEqual(args["contact"], "@real_user")
        self.assertEqual(args["link"], "https://t.me/c/10/20")


def _model_returns(tool_name, args):
    """A genai client whose model answers with exactly one function call."""
    response = SimpleNamespace(
        function_calls=[SimpleNamespace(name=tool_name, args=args)]
    )
    return SimpleNamespace(
        aio=SimpleNamespace(
            models=SimpleNamespace(
                generate_content=AsyncMock(return_value=response),
            )
        )
    )


class ProductionPathTests(unittest.IsolatedAsyncioTestCase):
    """Сквозной прод-путь без сети и Telegram.

    Настоящий роутер (разбор ответа модели), guard продукта, подстановка
    доверенного контекста, исполнение и разбор служебных маркеров — ровно
    то, что вызывает bot/handlers.py. Раньше эти проверки шли через
    GeminiClient.generate_response — фасад, которого прод не вызывал и в
    котором не было guard'а из M2: тесты были зелёными на пути, по которому
    клиент никогда не ходит.
    """

    CONTEXT = {
        "telegram_id": 42,
        "first_name": "Анна",
        "username": "anna",
        "dialog_link": "https://t.me/c/1/2",
    }

    async def turn(self, tool_name, args, message, history=None):
        engine = AssistantEngine(
            router=gemini_client,
            executor=ProductionToolExecutor(),
            metrics_enabled=False,
        )
        with patch.object(gemini_client, "client", _model_returns(tool_name, args)):
            return await engine.process_turn(
                TurnRequest(
                    message=message,
                    history=history or [],
                    user_context=self.CONTEXT,
                )
            )

    async def test_dispatcher_always_requires_a_tool_call(self):
        config = gemini_client._config(None)

        self.assertEqual(
            config.tool_config.function_calling_config.mode,
            types.FunctionCallingConfigMode.ANY,
        )

    async def test_composite_tool_result_is_returned_as_client_reply(self):
        result = await self.turn(
            "answer_information",
            {"topics": ["paid_post", "ad_formats"]},
            "цена и форматы",
        )

        self.assertIsNone(result.handover_reason)
        self.assertIsNone(result.admin_notification)
        self.assertIn(replies.FAQ_PAID_POST, result.reply_text)
        self.assertIn(replies.FAQ_AD_FORMATS, result.reply_text)

    async def test_categorized_handover_keeps_canonical_client_reply(self):
        result = await self.turn(
            "request_publication_support",
            {"issue": "delete", "publication_link": "https://t.me/edujobs/123"},
            "удалите пост",
        )

        self.assertEqual(result.reply_text, replies.PUBLICATION_SUPPORT_SENT)
        self.assertIn("удалить публикацию", result.handover_reason)
        self.assertIsNone(result.admin_notification)

    async def test_mutual_pr_routes_to_manager_handover(self):
        result = await self.turn("request_mutual_pr_support", {}, "Предлагаем ВП")

        self.assertEqual(result.reply_text, replies.MUTUAL_PR_HANDOVER)
        self.assertIn("взаимопиару", result.handover_reason)
        self.assertIsNone(result.admin_notification)

    @patch("llm.tools.sheets_service")
    async def test_document_task_returns_non_blocking_admin_notification(self, sheets):
        sheets.create_document_task.return_value = replies.DOC_CREATED.format(
            doc_type="счёт"
        )
        result = await self.turn(
            "create_document_task",
            {
                "doc_type": "счёт",
                "telegram_id": "attacker-id",
                "company": "ООО Ромашка",
                "inn": "7701234567",
            },
            "Нужен счёт для ООО Ромашка, ИНН 7701234567",
        )

        self.assertEqual(result.reply_text, replies.DOC_CREATED.format(doc_type="счёт"))
        self.assertIsNone(result.handover_reason)
        self.assertIn("ООО Ромашка", result.admin_notification)
        self.assertIn("7701234567", result.admin_notification)
        # Подмена telegram_id доехала до реального вызова Sheets, а не только
        # до словаря аргументов.
        self.assertEqual(sheets.create_document_task.call_args.kwargs["telegram_id"], "42")

    @patch("llm.tools.sheets_service")
    async def test_product_guard_is_on_the_production_path(self, sheets):
        """Guard из M2 действует там, где ходит клиент.

        Модель выбирает рекламный календарь посреди диалога о вакансии —
        в Sheets не уходит ни одного запроса, клиент получает просьбу
        прислать текст вакансии.
        """
        result = await self.turn(
            "get_free_slots",
            {},
            "Давайте платно",
            history=[
                {"role": "user", "content": "вакансия"},
                {"role": "assistant", "content": replies.FAQ_VACANCY_OPTIONS},
            ],
        )

        self.assertEqual(result.tool_name, "request_paid_vacancy")
        self.assertEqual(result.reply_text, replies.PAID_VACANCY_NEED_TEXT)
        sheets.get_free_slots.assert_not_called()


if __name__ == "__main__":
    unittest.main()


class ToolRegistryTests(unittest.TestCase):
    """bot_tools — единственный реестр; TOOL_FUNCTIONS выводится из него."""

    def test_tool_names_are_unique(self):
        """Словарь, собранный из списка, молча выкинет дубль имени —
        инструмент пропадёт у исполнителя, а модель будет его вызывать."""
        from llm.tools import bot_tools

        names = [fn.__name__ for fn in bot_tools]
        self.assertEqual(len(names), len(set(names)))

    def test_every_tool_has_routing_docstring(self):
        """Docstring — маршрутные метаданные для модели. Без него
        инструмент для роутера фактически невидим."""
        from llm.tools import bot_tools

        for fn in bot_tools:
            with self.subTest(tool=fn.__name__):
                self.assertTrue((fn.__doc__ or "").strip())
