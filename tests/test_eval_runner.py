import csv
import tempfile
import unittest
from pathlib import Path

from assistant.models import TurnResult
from scripts.eval_assistant import _check_turn, _load_cases, _summary, _write_results


class EvalRunnerTests(unittest.TestCase):
    def test_fixture_supports_multiturn_dialogs(self):
        fixture = Path(__file__).parent / "fixtures" / "dialog_cases.jsonl"
        cases = _load_cases(fixture)

        document_case = next(case for case in cases if case["id"] == "document_dialog")
        self.assertEqual(len(document_case["turns"]), 2)
        self.assertEqual(document_case["turns"][0]["manual_score"], 5)

    def test_results_are_written_as_csv_and_summary(self):
        rows = [
            {
                "case_id": "gratitude",
                "turn": 1,
                "user_message": "Спасибо",
                "expected_tool": "reply_gratitude",
                "actual_tool": "reply_gratitude",
                "passed": True,
                "model_path": "gemini-3.5-flash-lite",
                "reply_text": "Спасибо",
                "handover_reason": "",
                "dry_run": True,
                "model_ms": 10.0,
                "tool_ms": 0.1,
                "total_ms": 10.1,
                "total_tokens": 12,
                "error": "",
                "manual_score": 5,
                "manual_notes": "Корректный маршрут и ответ.",
            }
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir)
            _write_results(rows, output, metrics=True)

            with (output / "results.csv").open(encoding="utf-8-sig") as source:
                written = list(csv.DictReader(source))
            self.assertEqual(written[0]["actual_tool"], "reply_gratitude")
            self.assertTrue((output / "summary.json").exists())
            self.assertEqual(_summary(rows)["route_accuracy"], 1.0)
            self.assertEqual(_summary(rows)["manual_review"]["mean_score"], 5.0)
            self.assertEqual(_summary(rows)["manual_review"]["acceptance_rate"], 1.0)

    def test_manual_score_must_be_between_one_and_five(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            fixture = Path(temp_dir) / "bad.jsonl"
            fixture.write_text(
                '{"id":"bad","turns":[{"user":"Привет","manual_score":6}]}\n',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "от 1 до 5"):
                _load_cases(fixture)

    def test_turn_can_accept_equivalent_tools(self):
        turn = {
            "user": "Какие условия рекламы?",
            "expected_tools": ["faq_paid_post", "answer_information"],
        }
        result = TurnResult(
            reply_text="Условия",
            tool_name="answer_information",
        )
        self.assertTrue(_check_turn(turn, result))


if __name__ == "__main__":
    unittest.main()
