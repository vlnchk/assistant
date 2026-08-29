import unittest

from assistant.engine import AssistantEngine
from assistant.models import ModelUsage, RouteDecision, TurnRequest
from bot import replies
from llm.gemini_client import MODEL_NAME
from llm.tool_executor import DryRunToolExecutor


class FakeRouter:
    def __init__(self, *decisions):
        self.decisions = list(decisions)
        self.calls = []

    async def route(
        self,
        history,
        message,
        user_context,
        *,
        model_name,
    ):
        self.calls.append(
            {
                "history": history,
                "message": message,
                "model_name": model_name,
            }
        )
        return self.decisions.pop(0)


class RecordingDryRunExecutor(DryRunToolExecutor):
    def __init__(self):
        self.executions = []

    async def execute_prepared(self, tool_name, prepared_args):
        self.executions.append((tool_name, prepared_args))
        return await super().execute_prepared(tool_name, prepared_args)


class AssistantEngineTests(unittest.IsolatedAsyncioTestCase):
    def request(self, message="Спасибо"):
        return TurnRequest(
            message=message,
            history=[],
            user_context={
                "telegram_id": "42",
                "first_name": "Анна",
                "username": "anna",
                "dialog_link": "",
            },
        )

    async def test_standard_case_is_completed_by_single_model(self):
        router = FakeRouter(
            RouteDecision(
                model=MODEL_NAME,
                tool_name="reply_gratitude",
                usage=ModelUsage(total_tokens=12),
            )
        )
        executor = RecordingDryRunExecutor()
        engine = AssistantEngine(
            router=router,
            executor=executor,
        )

        result = await engine.process_turn(self.request())

        self.assertEqual(result.reply_text, replies.GRATITUDE)
        self.assertEqual(result.tool_name, "reply_gratitude")
        self.assertEqual([call["model_name"] for call in router.calls], [MODEL_NAME])
        self.assertEqual(len(executor.executions), 1)
        self.assertEqual(result.trace.total_tokens, 12)

    async def test_complex_handover_is_executed_without_a_second_model(self):
        router = FakeRouter(
            RouteDecision(
                model=MODEL_NAME,
                tool_name="handover_to_admin",
                arguments={"reason": "несколько действий"},
            )
        )
        executor = RecordingDryRunExecutor()
        engine = AssistantEngine(
            router=router,
            executor=executor,
        )

        result = await engine.process_turn(self.request("Нестандартный запрос"))

        self.assertEqual(result.tool_name, "handover_to_admin")
        self.assertEqual(result.handover_reason, "несколько действий")
        self.assertEqual([call["model_name"] for call in router.calls], [MODEL_NAME])
        self.assertEqual(len(executor.executions), 1)

    async def test_invalid_arguments_do_not_execute_a_side_effect(self):
        router = FakeRouter(
            RouteDecision(
                model=MODEL_NAME,
                tool_name="book_slot",
                arguments={"date": "5 сентября"},
            )
        )
        executor = RecordingDryRunExecutor()
        engine = AssistantEngine(
            router=router,
            executor=executor,
        )

        result = await engine.process_turn(self.request("Забронируйте"))

        self.assertIsNone(result.tool_name)
        self.assertIn("invalid_tool_call:", result.handover_reason)
        self.assertEqual(executor.executions, [])

    async def test_model_failure_does_not_execute_a_tool(self):
        router = FakeRouter(
            RouteDecision(model=MODEL_NAME, error="multiple_tool_calls"),
        )
        executor = RecordingDryRunExecutor()
        engine = AssistantEngine(
            router=router,
            executor=executor,
        )

        result = await engine.process_turn(self.request("???"))

        self.assertIn("multiple_tool_calls", result.handover_reason)
        self.assertEqual(executor.executions, [])

    async def test_dry_run_composite_calendar_preserves_month(self):
        executor = DryRunToolExecutor()

        result = await executor.execute_prepared(
            "answer_information",
            {"topics": ["paid_post", "free_slots"], "month": "сентябрь"},
        )

        self.assertIn("Свободные даты за месяц «сентябрь»", result.reply_text)
        self.assertNotIn("Ближайшие свободные даты", result.reply_text)

    async def test_dry_run_composite_calendar_preserves_dates(self):
        executor = DryRunToolExecutor()

        result = await executor.execute_prepared(
            "answer_information",
            {"topics": ["paid_post"], "dates": ["15 сентября"]},
        )

        self.assertIn("Свободны: 15 сентября", result.reply_text)


if __name__ == "__main__":
    unittest.main()
