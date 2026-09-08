import json
import unittest
from pathlib import Path

from bot import replies
from llm.gemini_client import _build_system_prompt
from llm.tools import TERMINAL_TOOLS, ask_placement_type, bot_tools
from llm.tool_executor import TOOL_FUNCTIONS


def _load_cases() -> dict[str, dict]:
    fixture = Path(__file__).parent / "fixtures" / "dialog_cases.jsonl"
    cases: dict[str, dict] = {}
    for line in fixture.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        case = json.loads(line)
        if "_meta" in case:
            continue
        cases[case["id"]] = case
    return cases


def _expected(turn: dict) -> list[str]:
    alternatives = turn.get("expected_tools")
    if alternatives:
        return list(alternatives)
    return [turn["expected_tool"]]


class PaidPostWordingTests(unittest.TestCase):
    def test_paid_post_names_the_product_before_the_price(self):
        # Клиент, который весь диалог говорил про вакансию, не должен читать
        # 15 000 ₽ как цену своей вакансии.
        self.assertIn("не вакансия", replies.FAQ_PAID_POST)
        self.assertIn("15 000 ₽", replies.FAQ_PAID_POST)
        self.assertLess(
            replies.FAQ_PAID_POST.index("не вакансия"),
            replies.FAQ_PAID_POST.index("15 000 ₽"),
        )

    def test_vacancy_options_keeps_its_own_price(self):
        self.assertIn("от 3 000 ₽", replies.FAQ_VACANCY_OPTIONS)
        self.assertNotIn("15 000", replies.FAQ_VACANCY_OPTIONS)


class PlacementTypeToolTests(unittest.TestCase):
    def test_tool_returns_the_canonical_clarification(self):
        self.assertEqual(ask_placement_type(), replies.ASK_PLACEMENT_TYPE)

    def test_clarification_offers_exactly_two_products(self):
        self.assertIn("вакансии", replies.ASK_PLACEMENT_TYPE)
        self.assertIn("рекламный", replies.ASK_PLACEMENT_TYPE)

    def test_tool_is_registered_everywhere(self):
        self.assertIn("ask_placement_type", {tool.__name__ for tool in bot_tools})
        self.assertIn("ask_placement_type", TOOL_FUNCTIONS)
        self.assertIn("ask_placement_type", TERMINAL_TOOLS)

    def test_prompt_keeps_the_strict_boundary(self):
        prompt = _build_system_prompt(None)
        self.assertIn("ask_placement_type", prompt)
        self.assertIn("ЕДИНСТВЕННЫЙ случай для ask_placement_type", prompt)
        self.assertIn("ЗАПРЕЩЕНО вызывать ask_placement_type", prompt)


class PlacementTypeDatasetTests(unittest.TestCase):
    """Новый tool без фикстур не принимаем: он добавляет точку отказа роутера."""

    def setUp(self):
        self.cases = _load_cases()

    def test_dataset_triggers_the_clarification(self):
        for case_id in (
            "placement_type_general_conditions",
            "placement_type_tell_about_conditions",
            "placement_type_how_much",
        ):
            with self.subTest(case_id):
                self.assertEqual(
                    _expected(self.cases[case_id]["turns"][0]),
                    ["ask_placement_type"],
                )

    def test_dataset_covers_anti_regression_routes(self):
        expected_routes = {
            "placement_type_no_regress_vacancy": "faq_free_posting",
            "placement_type_no_regress_paid_post": "faq_paid_post",
            "placement_type_no_regress_vacancy_options": "answer_information",
            "placement_type_no_regress_both_products": "answer_information",
        }
        for case_id, tool in expected_routes.items():
            with self.subTest(case_id):
                turn = self.cases[case_id]["turns"][0]
                self.assertEqual(_expected(turn), [tool])
                self.assertNotIn("ask_placement_type", _expected(turn))

    def test_dataset_checks_that_clarification_is_asked_once(self):
        for case_id in (
            "placement_type_answered_vacancy",
            "placement_type_answered_ad",
        ):
            with self.subTest(case_id):
                turns = self.cases[case_id]["turns"]
                self.assertEqual(_expected(turns[0]), ["ask_placement_type"])
                self.assertNotIn("ask_placement_type", _expected(turns[1]))


if __name__ == "__main__":
    unittest.main()
