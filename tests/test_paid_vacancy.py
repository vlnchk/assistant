"""Guard тесты для платной вакансии (ADR-0005).

Смысл файла: бизнес-правило живёт в коде, а не в промпте, и поэтому его
вообще можно проверить юнит-тестом. Правило в промпте так проверить нельзя —
это и было причиной вынести его сюда.
"""

import unittest

from bot import replies
from llm.tool_executor import (
    AD_MARKER,
    CALENDAR_TOOLS,
    TOOL_FUNCTIONS,
    VACANCY_MARKERS,
    apply_product_guard,
    detect_product,
)
from llm.tools import TERMINAL_TOOLS, request_paid_vacancy


def _bot(text):
    return {"role": "assistant", "content": text}


def _user(text):
    return {"role": "user", "content": text}


class ProductMarkerTests(unittest.TestCase):
    """Маркеры обязаны оставаться подстроками своих констант.

    Без этого теста правка текста в replies.py молча отключает guard.
    """

    def test_ad_marker_is_in_paid_post_reply(self):
        self.assertIn(AD_MARKER, replies.FAQ_PAID_POST)

    def test_vacancy_markers_are_in_their_replies(self):
        self.assertIn(VACANCY_MARKERS[0], replies.FAQ_VACANCY_OPTIONS)
        self.assertIn(VACANCY_MARKERS[1], replies.PAID_VACANCY_NEED_TEXT)


class DetectProductTests(unittest.TestCase):
    def test_no_history_means_unknown(self):
        self.assertIsNone(detect_product([]))
        self.assertIsNone(detect_product(None))

    def test_vacancy_options_sets_vacancy(self):
        self.assertEqual(
            detect_product([_bot(replies.FAQ_VACANCY_OPTIONS)]), "vacancy"
        )

    def test_paid_post_sets_ad(self):
        self.assertEqual(detect_product([_bot(replies.FAQ_PAID_POST)]), "ad")

    def test_latest_signal_wins(self):
        """Клиент вправе переключиться с вакансии на рекламу."""
        history = [_bot(replies.FAQ_VACANCY_OPTIONS), _bot(replies.FAQ_PAID_POST)]
        self.assertEqual(detect_product(history), "ad")
        self.assertEqual(detect_product(list(reversed(history))), "vacancy")

    def test_client_words_do_not_set_product(self):
        """Продукт определяется только по репликам БОТА, не по тексту клиента."""
        self.assertIsNone(detect_product([_user(replies.FAQ_VACANCY_OPTIONS)]))


class CalendarGuardTests(unittest.TestCase):
    VACANCY = [_bot(replies.FAQ_VACANCY_OPTIONS)]
    AD = [_bot(replies.FAQ_PAID_POST)]

    def test_every_calendar_tool_is_blocked_for_vacancy(self):
        for tool in CALENDAR_TOOLS:
            with self.subTest(tool=tool):
                name, args = apply_product_guard(tool, {"month": "сентябрь"}, self.VACANCY)
                self.assertEqual(name, "request_paid_vacancy")
                self.assertEqual(args, {})

    def test_calendar_stays_available_for_advertising(self):
        for tool in CALENDAR_TOOLS:
            with self.subTest(tool=tool):
                name, _ = apply_product_guard(tool, {}, self.AD)
                self.assertEqual(name, tool)

    def test_calendar_untouched_when_product_unknown(self):
        name, _ = apply_product_guard("get_free_slots", {}, [])
        self.assertEqual(name, "get_free_slots")

    def test_non_calendar_tools_pass_through(self):
        for tool in ("faq_ord", "faq_docs", "handover_to_admin"):
            with self.subTest(tool=tool):
                name, args = apply_product_guard(tool, {"reason": "x"}, self.VACANCY)
                self.assertEqual((name, args), (tool, {"reason": "x"}))

    def test_information_keeps_topics_but_drops_dates(self):
        name, args = apply_product_guard(
            "answer_information",
            {"topics": ["vacancy_options", "free_slots"], "month": "сентябрь"},
            self.VACANCY,
        )
        self.assertEqual(name, "answer_information")
        self.assertEqual(args["topics"], ["vacancy_options"])
        self.assertNotIn("month", args)

    def test_information_with_only_dates_becomes_escalation(self):
        name, _ = apply_product_guard(
            "answer_information", {"topics": ["free_slots"]}, self.VACANCY
        )
        self.assertEqual(name, "request_paid_vacancy")

    def test_information_without_calendar_is_untouched(self):
        args = {"topics": ["vacancy_options", "ord"]}
        name, guarded = apply_product_guard("answer_information", dict(args), self.VACANCY)
        self.assertEqual((name, guarded), ("answer_information", args))


class RequestPaidVacancyTests(unittest.TestCase):
    def test_registered_everywhere(self):
        self.assertIn("request_paid_vacancy", TOOL_FUNCTIONS)
        self.assertIn("request_paid_vacancy", TERMINAL_TOOLS)

    def test_without_text_asks_for_it_and_does_not_escalate(self):
        reply = request_paid_vacancy()
        self.assertEqual(reply, replies.PAID_VACANCY_NEED_TEXT)
        self.assertNotIn("__HANDOVER", reply)

    def test_blank_text_is_treated_as_missing(self):
        self.assertEqual(request_paid_vacancy("   "), replies.PAID_VACANCY_NEED_TEXT)

    def test_with_text_escalates_and_carries_it(self):
        reply = request_paid_vacancy("Ищем преподавателя математики")
        self.assertTrue(reply.startswith("__HANDOVER_JSON__:"))
        self.assertIn("Ищем преподавателя математики", reply)

    def test_bot_never_quotes_a_final_price(self):
        for text in (replies.PAID_VACANCY_NEED_TEXT, replies.PAID_VACANCY_HANDOVER):
            with self.subTest(text=text[:30]):
                self.assertNotIn("₽", text)

    def test_vacancy_options_invites_the_next_step(self):
        """Без призыва ответ клиента «давайте платно» остаётся без адресата."""
        self.assertIn("менеджер", replies.FAQ_VACANCY_OPTIONS.lower())


if __name__ == "__main__":
    unittest.main()


class OptionsBeforeEscalationTests(unittest.TestCase):
    """Нельзя собирать текст под платный вариант, который не назвали."""

    def test_without_options_shown_it_answers_with_options(self):
        history = [_bot(replies.FAQ_FREE_POSTING), _user("нужно платно")]
        name, args = apply_product_guard("request_paid_vacancy", {}, history)
        self.assertEqual(name, "answer_information")
        self.assertEqual(args, {"topics": ["vacancy_options"]})

    def test_after_options_shown_escalation_is_allowed(self):
        history = [_bot(replies.FAQ_VACANCY_OPTIONS), _user("давайте платно")]
        name, args = apply_product_guard(
            "request_paid_vacancy", {"vacancy_text": "Ищем методиста"}, history
        )
        self.assertEqual(name, "request_paid_vacancy")
        self.assertEqual(args, {"vacancy_text": "Ищем методиста"})

    def test_price_is_always_named_before_the_text_request(self):
        """Гарантия сквозная: цена «от 3 000 ₽» звучит раньше сбора текста."""
        self.assertIn("3 000", replies.FAQ_VACANCY_OPTIONS)
        name, _ = apply_product_guard("request_paid_vacancy", {}, [])
        self.assertEqual(name, "answer_information")
