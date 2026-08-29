import json
import unittest
from pathlib import Path

from bot import handlers
from llm.gemini_client import _build_system_prompt


class SemanticRoutingConfigurationTests(unittest.TestCase):
    def test_semantic_regex_shortcuts_are_removed(self):
        removed_names = {
            "GRATITUDE_PATTERN",
            "PLACEMENT_REQUEST_PATTERN",
            "PROMOTIONAL_CONTENT_PATTERN",
            "VACANCY_SIGNAL_PATTERN",
            "is_gratitude_only",
            "is_non_vacancy_promotion",
        }
        self.assertTrue(removed_names.isdisjoint(vars(handlers)))

    def test_model_prompt_keeps_vacancy_and_promotion_rule(self):
        prompt = _build_system_prompt(None)
        self.assertIn("программах, курсах", prompt)
        self.assertIn("настоящей", prompt)
        self.assertNotIn("delegate_to_flash", prompt)

    def test_eval_dataset_covers_both_routes(self):
        fixture = Path(__file__).parent / "fixtures" / "dialog_cases.jsonl"
        expected = {}
        for line in fixture.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            case = json.loads(line)
            if "_meta" in case:
                continue
            expected[case["id"]] = case["turns"][0].get("expected_tool")
        self.assertEqual(expected["paid_program_promotion"], "faq_paid_post")
        self.assertEqual(expected["real_vacancy"], "faq_free_posting")


if __name__ == "__main__":
    unittest.main()
