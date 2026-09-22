"""Обе опции на любой вакансионный интент (ADR-0004).

Закрывает dev/findings/2026-09-17-vacancy-hides-paid-option.md: на слово
«вакансия» бот называл только бесплатный вариант и скрывал платный.
"""

import unittest

from bot import replies
from llm.tools import INFORMATION_REPLIES, answer_information, faq_free_posting


class BothOptionsAlwaysNamedTests(unittest.TestCase):
    def test_free_posting_and_vacancy_options_are_one_text(self):
        """Один объект, а не две копии: разойтись они не могут в принципе."""
        self.assertIs(replies.FAQ_FREE_POSTING, replies.FAQ_VACANCY_OPTIONS)

    def test_both_information_topics_resolve_to_the_same_text(self):
        self.assertIs(
            INFORMATION_REPLIES["free_posting"], INFORMATION_REPLIES["vacancy_options"]
        )

    def test_vacancy_tool_names_the_paid_option(self):
        reply = faq_free_posting()
        self.assertIn("3 000", reply)
        self.assertIn("бесплатно", reply.lower())

    def test_free_option_comes_first(self):
        """Бесплатная форма первой — платную не навязываем."""
        reply = faq_free_posting()
        self.assertLess(reply.index("бесплатно"), reply.index("платно — от"))

    def test_form_link_survived(self):
        self.assertIn("clck.ru/3Qxfvq", faq_free_posting())

    def test_no_duplicate_block_when_both_topics_requested(self):
        """Обе темы в одном вызове не должны печатать текст дважды."""
        reply = answer_information(topics=["free_posting", "vacancy_options"])
        self.assertEqual(reply.count("Есть два варианта"), 1)


class NoRegressionTests(unittest.TestCase):
    def test_ad_price_is_not_in_the_vacancy_reply(self):
        """Главный риск ADR-0004: смешать прайсы вакансии и рекламы."""
        self.assertNotIn("15 000", faq_free_posting())

    def test_clarifying_question_still_exists(self):
        """Общий вопрос без продукта по-прежнему уточняется (ADR-0003)."""
        self.assertIn("вакансии или рекламный", replies.ASK_PLACEMENT_TYPE)

    def test_paid_post_still_names_its_product(self):
        """ADR-0006 не сломан."""
        self.assertIn("не вакансия", replies.FAQ_PAID_POST)


if __name__ == "__main__":
    unittest.main()
