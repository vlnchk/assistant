"""Validated execution of model-selected tools.

Routing and execution are deliberately separate so a side-effecting tool is
executed at most once.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import re
from typing import Any, Callable

from assistant.models import ToolOutcome
from bot import replies
from llm.tools import (
    INFORMATION_REPLIES,
    answer_information,
    ask_ad_topic,
    book_slot,
    check_dates_availability,
    create_document_task,
    faq_docs,
    faq_free_posting,
    faq_ord,
    faq_paid_post,
    faq_stats,
    get_client_bookings,
    get_free_slots,
    handover_to_admin,
    reply_bot_nature,
    reply_gratitude,
    reply_offtopic,
    request_mutual_pr_support,
    request_publication_support,
    send_greeting,
)
from logger import logger


ToolFunction = Callable[..., str]

TOOL_FUNCTIONS: dict[str, ToolFunction] = {
    "send_greeting": send_greeting,
    "reply_gratitude": reply_gratitude,
    "reply_bot_nature": reply_bot_nature,
    "reply_offtopic": reply_offtopic,
    "faq_free_posting": faq_free_posting,
    "faq_paid_post": faq_paid_post,
    "faq_stats": faq_stats,
    "faq_ord": faq_ord,
    "faq_docs": faq_docs,
    "answer_information": answer_information,
    "ask_ad_topic": ask_ad_topic,
    "get_free_slots": get_free_slots,
    "check_dates_availability": check_dates_availability,
    "book_slot": book_slot,
    "get_client_bookings": get_client_bookings,
    "create_document_task": create_document_task,
    "request_publication_support": request_publication_support,
    "request_mutual_pr_support": request_mutual_pr_support,
    "handover_to_admin": handover_to_admin,
}


class ToolCallError(Exception):
    """A tool name or its arguments are invalid."""


def inject_server_context(
    tool_name: str,
    args: dict[str, Any],
    user_context: dict[str, Any] | None,
) -> dict[str, Any]:
    """Overwrite security-sensitive arguments with trusted server values."""
    prepared = dict(args)
    if not user_context:
        return prepared

    identity_tools = {"book_slot", "get_client_bookings", "create_document_task"}
    real_tid = str(user_context.get("telegram_id") or "")
    if tool_name in identity_tools and real_tid:
        supplied_tid = prepared.get("telegram_id")
        if supplied_tid is not None and str(supplied_tid) != real_tid:
            logger.warning(
                f"[Security] LLM передал telegram_id={supplied_tid}, заменён на {real_tid}"
            )
        prepared["telegram_id"] = real_tid

    if tool_name in {"book_slot", "create_document_task"}:
        prepared["link"] = str(user_context.get("dialog_link") or "")

    if tool_name == "create_document_task":
        username = user_context.get("username")
        first_name = user_context.get("first_name")
        prepared["contact"] = f"@{username}" if username else str(first_name or "")

    return prepared


def parse_tool_result(result: str, *, dry_run: bool = False) -> ToolOutcome:
    """Convert legacy tool sentinels into a typed core result."""
    if result.startswith("__ADMIN_NOTIFY_JSON__:"):
        try:
            payload = json.loads(result.split(":", 1)[1])
            return ToolOutcome(
                reply_text=str(payload["client_reply"]),
                admin_notification=str(payload["notification"]),
                dry_run=dry_run,
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ToolCallError("некорректное служебное уведомление") from exc

    if result.startswith("__HANDOVER_JSON__:"):
        try:
            payload = json.loads(result.split(":", 1)[1])
            return ToolOutcome(
                reply_text=str(payload["client_reply"]),
                handover_reason=str(payload["reason"]),
                dry_run=dry_run,
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ToolCallError("некорректный запрос эскалации") from exc

    if result.startswith("__HANDOVER__"):
        reason = result.split(":", 1)[1] if ":" in result else "не указана"
        return ToolOutcome(handover_reason=reason, dry_run=dry_run)

    return ToolOutcome(reply_text=result, dry_run=dry_run)


class ProductionToolExecutor:
    """Execute the final validated tool against production integrations."""

    def prepare(
        self,
        tool_name: str,
        args: dict[str, Any],
        user_context: dict[str, Any] | None,
    ) -> dict[str, Any]:
        fn = TOOL_FUNCTIONS.get(tool_name)
        if not fn:
            raise ToolCallError(f"инструмент '{tool_name}' не найден")

        prepared = inject_server_context(tool_name, args, user_context)
        try:
            inspect.signature(fn).bind(**prepared)
        except TypeError as exc:
            raise ToolCallError(f"{tool_name}: неверные аргументы") from exc
        return prepared

    async def execute_prepared(
        self,
        tool_name: str,
        prepared_args: dict[str, Any],
    ) -> ToolOutcome:
        fn = TOOL_FUNCTIONS[tool_name]
        logger.info(f"[Tool call] {tool_name}({prepared_args})")
        try:
            result = await asyncio.to_thread(fn, **prepared_args)
        except TypeError as exc:
            logger.error(
                f"[Tool] {tool_name} получил неверные аргументы {prepared_args}: {exc}"
            )
            raise ToolCallError(f"{tool_name}: неверные аргументы") from exc
        return parse_tool_result(result)


class DryRunToolExecutor(ProductionToolExecutor):
    """Safe executor for local evaluation; never touches external services."""

    EXTERNAL_TOOLS = {
        "get_free_slots",
        "check_dates_availability",
        "book_slot",
        "get_client_bookings",
        "create_document_task",
    }

    async def execute_prepared(
        self,
        tool_name: str,
        prepared_args: dict[str, Any],
    ) -> ToolOutcome:
        logger.info(f"[Dry-run tool] {tool_name}({prepared_args})")

        if tool_name == "get_free_slots":
            month = str(prepared_args.get("month") or "").strip()
            reply = (
                f"[DRY-RUN] Свободные даты в {month}: 5, 12 и 19 числа."
                if month
                else "[DRY-RUN] Ближайшие свободные даты: 5, 12 и 19 сентября."
            )
            return ToolOutcome(reply_text=reply, dry_run=True)

        if tool_name == "check_dates_availability":
            dates = prepared_args.get("dates") or []
            return ToolOutcome(
                reply_text=f"[DRY-RUN] Свободны: {', '.join(map(str, dates))}.",
                dry_run=True,
            )

        if tool_name == "get_client_bookings":
            return ToolOutcome(reply_text=replies.BOOKINGS_EMPTY, dry_run=True)

        if tool_name == "book_slot":
            date = str(prepared_args.get("date") or "выбранную дату")
            return ToolOutcome(
                reply_text=replies.BOOK_SUCCESS.format(date=date),
                dry_run=True,
            )

        if tool_name == "create_document_task":
            company = str(prepared_args.get("company") or "").strip()
            inn = re.sub(r"\D", "", str(prepared_args.get("inn") or ""))
            if not company and not inn:
                return ToolOutcome(reply_text=replies.DOC_NEED_DETAILS, dry_run=True)
            if not company:
                return ToolOutcome(reply_text=replies.DOC_NEED_COMPANY, dry_run=True)
            if not inn:
                return ToolOutcome(reply_text=replies.DOC_NEED_INN, dry_run=True)
            if len(inn) not in {10, 12}:
                return ToolOutcome(reply_text=replies.DOC_BAD_INN, dry_run=True)
            doc_type = str(prepared_args.get("doc_type") or "документ")
            return ToolOutcome(
                reply_text=replies.DOC_CREATED.format(doc_type=doc_type),
                admin_notification=(
                    f"Подготовить документ: {doc_type}; компания: {company}; ИНН: {inn}"
                ),
                dry_run=True,
            )

        if tool_name == "answer_information":
            topics = [str(topic).strip().lower() for topic in prepared_args.get("topics", [])]
            wants_slots = (
                "free_slots" in topics
                or bool(prepared_args.get("month"))
                or bool(prepared_args.get("dates"))
            )
            if wants_slots:
                unknown = [
                    topic
                    for topic in topics
                    if topic != "free_slots" and topic not in INFORMATION_REPLIES
                ]
                if unknown:
                    return ToolOutcome(
                        handover_reason="неизвестная информационная тема",
                        dry_run=True,
                    )
                blocks = [
                    INFORMATION_REPLIES[topic]
                    for topic in dict.fromkeys(topics)
                    if topic in INFORMATION_REPLIES
                ]
                dates = prepared_args.get("dates") or []
                month = str(prepared_args.get("month") or "").strip()
                if dates:
                    blocks.append(
                        f"[DRY-RUN] Свободны: {', '.join(map(str, dates))}."
                    )
                elif month:
                    blocks.append(
                        f"[DRY-RUN] Свободные даты за месяц «{month}»: "
                        "5, 12 и 19 числа."
                    )
                else:
                    blocks.append(
                        "[DRY-RUN] Ближайшие свободные даты: "
                        "5, 12 и 19 сентября."
                    )
                return ToolOutcome(
                    reply_text="\n\n".join(dict.fromkeys(blocks)),
                    dry_run=True,
                )

        # Remaining tools are pure: they only compose canonical strings or
        # structured handover markers and are safe to execute locally.
        fn = TOOL_FUNCTIONS[tool_name]
        result = await asyncio.to_thread(fn, **prepared_args)
        return parse_tool_result(result, dry_run=True)
