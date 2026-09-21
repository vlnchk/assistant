"""Transport-independent orchestration for one assistant turn."""

from __future__ import annotations

import json
import os
import time
from typing import Any

from assistant.models import RouteDecision, TurnRequest, TurnResult, TurnTrace
from llm.gemini_client import MODEL_NAME, gemini_client
from llm.tool_executor import (
    ProductionToolExecutor,
    ToolCallError,
    apply_product_guard,
)
from logger import logger


class AssistantEngine:
    """Route a message, validate one tool, then execute that tool once."""

    def __init__(
        self,
        *,
        router: Any = None,
        executor: Any = None,
        metrics_enabled: bool | None = None,
    ) -> None:
        self.router = router or gemini_client
        self.executor = executor or ProductionToolExecutor()
        if metrics_enabled is None:
            metrics_enabled = os.getenv("ASSISTANT_METRICS_ENABLED", "0") == "1"
        self.metrics_enabled = metrics_enabled

    async def _route(
        self,
        request: TurnRequest,
    ) -> RouteDecision:
        try:
            return await self.router.route(
                request.history,
                request.message,
                request.user_context,
                model_name=MODEL_NAME,
            )
        except Exception as exc:
            logger.exception(f"[LLM] Ошибка вызова {MODEL_NAME}: {exc}")
            return RouteDecision(
                model=MODEL_NAME,
                error=f"exception:{type(exc).__name__}",
            )

    def _prepare(
        self,
        decision: RouteDecision,
        user_context: dict[str, Any],
    ) -> tuple[dict[str, Any] | None, str | None]:
        if decision.error:
            return None, decision.error
        try:
            prepared = self.executor.prepare(
                decision.tool_name or "",
                decision.arguments,
                user_context,
            )
        except ToolCallError as exc:
            return None, f"invalid_tool_call:{exc}"
        return prepared, None

    @staticmethod
    def _routing_failure(
        decision: RouteDecision,
        trace: TurnTrace,
    ) -> TurnResult:
        error = decision.error or "invalid_tool_call"
        if error.startswith("exception:"):
            return TurnResult(
                reply_text=None,
                trace=trace,
                error=error,
            )
        reason = "LLM timeout" if error == "timeout" else f"LLM routing error ({error})"
        return TurnResult(
            reply_text=None,
            handover_reason=reason,
            trace=trace,
        )

    async def process_turn(self, request: TurnRequest) -> TurnResult:
        started = time.perf_counter()
        trace = TurnTrace(model_name=MODEL_NAME)
        final_decision = await self._route(request)
        trace.routes.append(final_decision)
        trace.model_path.append(MODEL_NAME)
        self._guard(final_decision, request)
        prepared_args, preparation_error = self._prepare(
            final_decision,
            request.user_context,
        )
        if preparation_error:
            final_decision.error = preparation_error

        if final_decision.error or prepared_args is None:
            trace.total_ms = (time.perf_counter() - started) * 1000
            result = self._routing_failure(final_decision, trace)
            self._log_trace(result)
            return result

        tool_started = time.perf_counter()
        try:
            outcome = await self.executor.execute_prepared(
                final_decision.tool_name or "",
                prepared_args,
            )
        except ToolCallError as exc:
            trace.tool_ms = (time.perf_counter() - tool_started) * 1000
            trace.total_ms = (time.perf_counter() - started) * 1000
            result = TurnResult(
                reply_text=None,
                handover_reason=f"внутренняя ошибка инструмента ({exc})",
                tool_name=final_decision.tool_name,
                tool_arguments=prepared_args,
                trace=trace,
            )
            self._log_trace(result)
            return result
        except Exception as exc:
            trace.tool_ms = (time.perf_counter() - tool_started) * 1000
            trace.total_ms = (time.perf_counter() - started) * 1000
            logger.exception(f"[Tool] Ошибка {final_decision.tool_name}: {exc}")
            result = TurnResult(
                reply_text=None,
                tool_name=final_decision.tool_name,
                tool_arguments=prepared_args,
                trace=trace,
                error=f"tool_exception:{type(exc).__name__}:{exc}",
            )
            self._log_trace(result)
            return result

        trace.tool_ms = (time.perf_counter() - tool_started) * 1000
        trace.total_ms = (time.perf_counter() - started) * 1000
        result = TurnResult(
            reply_text=outcome.reply_text,
            handover_reason=outcome.handover_reason,
            admin_notification=outcome.admin_notification,
            tool_name=final_decision.tool_name,
            tool_arguments=prepared_args,
            trace=trace,
            dry_run=outcome.dry_run,
        )
        self._log_trace(result)
        return result

    @staticmethod
    def _guard(decision: RouteDecision, request: TurnRequest) -> None:
        """Override a route the dialogue's product forbids (ADR-0005).

        Deterministic and server-side on purpose: a rule in the system prompt
        cannot be unit-tested and competes with the neighbouring rules.
        """
        if decision.error or not decision.tool_name:
            return
        guarded_name, guarded_args = apply_product_guard(
            decision.tool_name,
            decision.arguments,
            request.history,
        )
        if guarded_name == decision.tool_name and guarded_args == decision.arguments:
            return
        logger.info(
            f"[Guard] {decision.tool_name}({decision.arguments}) -> "
            f"{guarded_name}({guarded_args})"
        )
        decision.tool_name = guarded_name
        decision.arguments = guarded_args

    def _log_trace(self, result: TurnResult) -> None:
        if not self.metrics_enabled or not result.trace:
            return
        payload = result.trace.as_dict()
        payload.update(
            {
                "tool": result.tool_name,
                "handover": result.handover_reason is not None,
                "error": result.error,
            }
        )
        logger.info(f"[Assistant metrics] {json.dumps(payload, ensure_ascii=False)}")


assistant_engine = AssistantEngine()
