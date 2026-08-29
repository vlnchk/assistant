import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock
from unittest.mock import patch

from google.genai import types

from bot import replies
from llm.gemini_client import gemini_client
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
        args = gemini_client._inject_server_context(
            "create_document_task",
            {
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


class DispatcherIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_dispatcher_always_requires_a_tool_call(self):
        config = gemini_client._config(None)

        self.assertEqual(
            config.tool_config.function_calling_config.mode,
            types.FunctionCallingConfigMode.ANY,
        )

    async def test_composite_tool_result_is_returned_as_client_reply(self):
        response = SimpleNamespace(
            function_calls=[
                SimpleNamespace(
                    name="answer_information",
                    args={"topics": ["paid_post", "ad_formats"]},
                )
            ]
        )
        fake_client = SimpleNamespace(
            aio=SimpleNamespace(
                models=SimpleNamespace(
                    generate_content=AsyncMock(return_value=response),
                )
            )
        )

        with patch.object(gemini_client, "client", fake_client):
            reply, reason, notification = await gemini_client.generate_response(
                [], "цена и форматы"
            )

        self.assertIsNone(reason)
        self.assertIsNone(notification)
        self.assertIn(replies.FAQ_PAID_POST, reply)
        self.assertIn(replies.FAQ_AD_FORMATS, reply)

    async def test_categorized_handover_keeps_canonical_client_reply(self):
        response = SimpleNamespace(
            function_calls=[
                SimpleNamespace(
                    name="request_publication_support",
                    args={
                        "issue": "delete",
                        "publication_link": "https://t.me/edujobs/123",
                    },
                )
            ]
        )
        fake_client = SimpleNamespace(
            aio=SimpleNamespace(
                models=SimpleNamespace(
                    generate_content=AsyncMock(return_value=response),
                )
            )
        )

        with patch.object(gemini_client, "client", fake_client):
            reply, reason, notification = await gemini_client.generate_response(
                [], "удалите пост"
            )

        self.assertEqual(reply, replies.PUBLICATION_SUPPORT_SENT)
        self.assertIn("удалить публикацию", reason)
        self.assertIsNone(notification)

    async def test_mutual_pr_routes_to_manager_handover(self):
        response = SimpleNamespace(
            function_calls=[
                SimpleNamespace(name="request_mutual_pr_support", args={})
            ]
        )
        fake_client = SimpleNamespace(
            aio=SimpleNamespace(
                models=SimpleNamespace(
                    generate_content=AsyncMock(return_value=response),
                )
            )
        )

        with patch.object(gemini_client, "client", fake_client):
            reply, reason, notification = await gemini_client.generate_response(
                [], "Предлагаем ВП"
            )

        self.assertEqual(reply, replies.MUTUAL_PR_HANDOVER)
        self.assertIn("взаимопиару", reason)
        self.assertIsNone(notification)

    @patch("llm.tools.sheets_service")
    async def test_document_task_returns_non_blocking_admin_notification(self, sheets):
        sheets.create_document_task.return_value = replies.DOC_CREATED.format(
            doc_type="счёт"
        )
        response = SimpleNamespace(
            function_calls=[
                SimpleNamespace(
                    name="create_document_task",
                    args={
                        "doc_type": "счёт",
                        "telegram_id": "42",
                        "company": "ООО Ромашка",
                        "inn": "7701234567",
                    },
                )
            ]
        )
        fake_client = SimpleNamespace(
            aio=SimpleNamespace(
                models=SimpleNamespace(
                    generate_content=AsyncMock(return_value=response),
                )
            )
        )

        with patch.object(gemini_client, "client", fake_client):
            reply, reason, notification = await gemini_client.generate_response(
                [],
                "Нужен счёт для ООО Ромашка, ИНН 7701234567",
                user_context={
                    "telegram_id": 42,
                    "username": "anna",
                    "dialog_link": "https://t.me/c/1/2",
                },
            )

        self.assertEqual(reply, replies.DOC_CREATED.format(doc_type="счёт"))
        self.assertIsNone(reason)
        self.assertIn("ООО Ромашка", notification)
        self.assertIn("7701234567", notification)


if __name__ == "__main__":
    unittest.main()
