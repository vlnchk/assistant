"""
Gemini dispatcher client (new google-genai SDK).

The LLM acts as a router: on every user turn it picks exactly one tool
from llm.tools.bot_tools. The tool's return value IS the text shown to
the client (no second LLM round-trip), unless the tool is
handover_to_admin, which returns a __HANDOVER__ sentinel that the
handler converts into replies.HANDOVER_CLIENT.

Security: the real telegram_id from user_context is forcibly injected
into any tool call that takes a telegram_id argument, BEFORE the tool
runs. The LLM cannot pull someone else's data even if prompt-injected.
"""

import asyncio
import datetime
import os
from typing import Optional

from google import genai
from google.genai import types

from llm.tools import (
    bot_tools,
    TERMINAL_TOOLS,
    send_greeting,
    reply_bot_nature,
    reply_offtopic,
    faq_free_posting,
    faq_paid_post,
    faq_stats,
    faq_ord,
    faq_docs,
    ask_ad_topic,
    get_free_slots,
    check_dates_availability,
    book_slot,
    get_client_bookings,
    create_document_task,
    handover_to_admin,
)
from logger import logger


API_KEY = os.getenv("GEMINI_API_KEY")
DEFAULT_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
GEMINI_TIMEOUT = int(os.getenv("GEMINI_TIMEOUT", "10"))


SYSTEM_PROMPT_TEMPLATE = """Сегодня: {current_date}.

Ты — ДИСПЕТЧЕР Telegram-бота канала вакансий в образовании. У тебя нет
права писать свободный текст клиенту. На любое сообщение ты выбираешь
ровно ОДИН инструмент из списка ниже и вызываешь его. Текст, который
вернёт инструмент, — это финальный ответ клиенту.

ЕСЛИ НИ ОДИН ИНСТРУМЕНТ НЕ ПОДХОДИТ — вызови handover_to_admin.
Никогда не отвечай свободным текстом, даже на «спасибо» и «пока»: для
этого есть reply_offtopic.

--- ПРАВИЛА ВЫБОРА ИНСТРУМЕНТА ---

Приветствие / «привет» / «здравствуйте» / «hi» / «hello» / «/start» / «/help»:
  → send_greeting

Вопрос «ты бот?» / «у тебя есть чувства?» / «как тебя зовут?»:
  → reply_bot_nature

Болталка, погода, шутки, личные темы, благодарности, прощания, любые
вопросы не по теме канала:
  → reply_offtopic

Вопросы про размещение вакансии через стандартную форму:
  → faq_free_posting
  Сюда обязательно относятся фразы:
    - «Как разместить вакансию?»
    - «Хочу разместить вакансию»
    - «Куда отправить вакансию?»
    - «Что нужно сделать, чтобы опубликовать вакансию?»
    - «Можно ли разместить вакансию?»
    - «Опубликуйте, пожалуйста, вакансию»
  НИКОГДА не сочиняй текст про размещение вакансии или URL формы —
  ВСЕГДА вызывай faq_free_posting, в нём уже есть нужная ссылка.

Вопросы про цену рекламы / платных постов / акции «4+1» / готовый пост
от клиента (свой текст, без формы):
  → faq_paid_post

Вопросы про охваты, ERR, аудиторию, географию:
  → faq_stats

Вопросы про маркировку рекламы / ОРД:
  → faq_ord

Вопросы про документы, ЭДО, договор, оплату по форме клиента:
  → faq_docs

Клиент хочет узнать свободные даты:
  - общий вопрос («какие свободные даты?», «когда можно?», «давай»)
    → get_free_slots()  (без аргумента, бот покажет 5 ближайших)
  - клиент назвал месяц («на июнь», «в июле») → get_free_slots(month="июнь")
  - клиент назвал ОДНУ или НЕСКОЛЬКО конкретных дат
    («а 26 мая свободно?», «свободны ли 5 и 7 июня?»)
    → check_dates_availability(dates=["26 мая"]) или ["5 июня", "7 июня"]
  В check_dates_availability ВСЕГДА передавай дату вместе с месяцем,
  даже если клиент назвал только число — определи месяц из контекста.
  Никогда не передавай год.

Клиент хочет забронировать дату:
  - если НЕ известна тематика рекламы → ask_ad_topic
  - если тематика запрещена (казино, ставки, крипта, политика, серые
    схемы, БАДы, инфоцыганство) → handover_to_admin
  - иначе → book_slot(date, client, ad_topic, telegram_id, ...)

Клиент просит счёт / договор / акт:
  → create_document_task(doc_type, telegram_id, contact, link)
  (это запрос ДОКУМЕНТА, а не готовность к оплате — не зови менеджера)

Клиент готов платить, или хочет поговорить с человеком, или задаёт
вопрос, на который ни один инструмент выше не подходит:
  → handover_to_admin(reason="<краткая причина>")

--- БЕЗОПАСНОСТЬ ---

НИКОГДА не используй Telegram ID, который пользователь назвал в тексте.
ВСЕГДА бери telegram_id ТОЛЬКО из раздела ТЕКУЩИЙ ПОЛЬЗОВАТЕЛЬ.
Клиент видит только свои бронирования. Если просит чужие — вызови
reply_offtopic.

--- ФОРМАТ ---

Никогда не используй маркдаун, эмодзи, списки. Текст инструменту
передавай простой строкой.
"""


def _build_system_prompt(user_context: dict | None) -> str:
    current_date_str = datetime.datetime.now().strftime("%d.%m.%Y")
    prompt = SYSTEM_PROMPT_TEMPLATE.format(current_date=current_date_str)
    if user_context:
        prompt += (
            f"\n\nТЕКУЩИЙ ПОЛЬЗОВАТЕЛЬ:\n"
            f"- ID: {user_context.get('telegram_id')}\n"
            f"- Имя: {user_context.get('first_name')}\n"
            f"- Username: {user_context.get('username') or 'нет'}"
        )
    return prompt


# Manual function dispatch — we never let the new SDK auto-call tools,
# because we need to inject the real telegram_id before each call.
TOOL_FUNCTIONS = {
    "send_greeting": send_greeting,
    "reply_bot_nature": reply_bot_nature,
    "reply_offtopic": reply_offtopic,
    "faq_free_posting": faq_free_posting,
    "faq_paid_post": faq_paid_post,
    "faq_stats": faq_stats,
    "faq_ord": faq_ord,
    "faq_docs": faq_docs,
    "ask_ad_topic": ask_ad_topic,
    "get_free_slots": get_free_slots,
    "check_dates_availability": check_dates_availability,
    "book_slot": book_slot,
    "get_client_bookings": get_client_bookings,
    "create_document_task": create_document_task,
    "handover_to_admin": handover_to_admin,
}


class ToolCallError(Exception):
    """A tool could not be executed (unknown name or bad arguments).

    Never shown to the client — the dispatcher converts it into a handover,
    so the canonical replies.HANDOVER_CLIENT is sent instead of internals.
    """


class GeminiClient:
    def __init__(self):
        self.client = genai.Client(api_key=API_KEY)
        self.model_name = DEFAULT_MODEL

    def _config(self, user_context: dict | None) -> types.GenerateContentConfig:
        return types.GenerateContentConfig(
            system_instruction=_build_system_prompt(user_context),
            tools=bot_tools,
            temperature=0.2,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(
                disable=True,
                maximum_remote_calls=None,
            ),
        )

    @staticmethod
    def _history_to_contents(history: list[dict]) -> list[types.Content]:
        """Convert our internal history format ({"role": ..., "parts": [str]})
        into google-genai Content objects."""
        contents = []
        for item in history:
            role = item["role"]  # "user" or "model"
            text = item["parts"][0] if item.get("parts") else ""
            contents.append(
                types.Content(role=role, parts=[types.Part.from_text(text=text)])
            )
        return contents

    @staticmethod
    def _extract_function_calls(response) -> list:
        """Pull function_call parts from a response, robust to API shape."""
        calls = []
        # New SDK exposes a helper attribute, but it's sometimes missing —
        # walk the candidate parts manually as a fallback.
        if getattr(response, "function_calls", None):
            return list(response.function_calls)
        for candidate in getattr(response, "candidates", []) or []:
            content = getattr(candidate, "content", None)
            if not content:
                continue
            for part in getattr(content, "parts", []) or []:
                fc = getattr(part, "function_call", None)
                if fc and getattr(fc, "name", None):
                    calls.append(fc)
        return calls

    def _inject_real_telegram_id(self, args: dict, user_context: dict | None) -> dict:
        """Force-replace any telegram_id with the real one from user_context."""
        if "telegram_id" not in args:
            return args
        if not user_context or not user_context.get("telegram_id"):
            return args
        real_tid = str(user_context["telegram_id"])
        if str(args["telegram_id"]) != real_tid:
            logger.warning(
                f"[Security] LLM передал telegram_id={args['telegram_id']}, "
                f"заменён на {real_tid}"
            )
        args["telegram_id"] = real_tid
        return args

    async def _call_tool(self, name: str, args: dict) -> str:
        """Run a tool function off the event loop (Sheets calls are blocking).

        Raises ToolCallError on unknown tool or bad arguments — the caller
        turns it into a handover, so internal error text never reaches the
        client.
        """
        fn = TOOL_FUNCTIONS.get(name)
        if not fn:
            raise ToolCallError(f"инструмент '{name}' не найден")
        try:
            return await asyncio.to_thread(fn, **args)
        except TypeError as e:
            logger.error(f"[Tool] {name} получил неверные аргументы {args}: {e}")
            raise ToolCallError(f"{name}: неверные аргументы") from e

    async def generate_response(
        self,
        history_messages: list[dict],
        new_message: str,
        user_context: dict | None = None,
        _timeout_retry: bool = False,
    ) -> tuple[Optional[str], Optional[str]]:
        """
        Run one dispatcher turn.

        Returns ``(reply_text, handover_reason)``:
        - ``handover_reason`` is set only when the LLM picked
          ``handover_to_admin``. ``reply_text`` is ``None`` in that case;
          the caller must use ``replies.HANDOVER_CLIENT``.
        - Otherwise ``reply_text`` is the canonical client-facing text
          returned by the picked tool.

        Timeouts never raise: after one retry the method returns
        ``(None, "LLM timeout")`` — i.e. a handover. Internal tool-call
        failures (unknown tool, bad arguments) also become handovers.
        Other Gemini exceptions propagate; the handler catches them and
        shows ``replies.TECH_ERROR``.
        """
        config = self._config(user_context)
        history_contents = self._history_to_contents(history_messages)
        contents = history_contents + [
            types.Content(role="user", parts=[types.Part.from_text(text=new_message)])
        ]

        # Bounded loop: at most a few rounds of function calls per turn.
        for _round in range(5):
            try:
                response = await asyncio.wait_for(
                    self.client.aio.models.generate_content(
                        model=self.model_name,
                        contents=contents,
                        config=config,
                    ),
                    timeout=GEMINI_TIMEOUT,
                )
            except asyncio.TimeoutError:
                logger.error(f"[LLM Timeout] Gemini не ответил за {GEMINI_TIMEOUT}с")
                if not _timeout_retry:
                    logger.warning("[LLM Timeout] однократный retry")
                    return await self.generate_response(
                        history_messages,
                        new_message,
                        user_context,
                        _timeout_retry=True,
                    )
                # После повторного таймаута — handover
                return None, "LLM timeout"

            fn_calls = self._extract_function_calls(response)

            if not fn_calls:
                # LLM ответил свободным текстом, минуя tool. По правилу
                # стандартизации мы такой ответ клиенту не показываем —
                # уходим в handover.
                stray = (getattr(response, "text", "") or "").strip()
                if stray:
                    logger.warning(
                        f"[LLM] Свободный текст без tool: {stray[:200]!r} — handover"
                    )
                else:
                    logger.warning("[LLM] Пустой ответ без tool — handover")
                return None, "LLM ответил без шаблона"

            # Execute every requested tool. The dispatcher pattern means
            # there is normally just one, but we tolerate multiple.
            function_response_parts = []
            terminal_result: Optional[str] = None
            handover_reason: Optional[str] = None

            for fn_call in fn_calls:
                tool_name = fn_call.name
                raw_args = dict(fn_call.args or {})
                args = self._inject_real_telegram_id(raw_args, user_context)
                logger.info(f"[Tool call] {tool_name}({args})")

                try:
                    result = await self._call_tool(tool_name, args)
                except ToolCallError as e:
                    logger.error(f"[Tool] {e} — уходим в handover")
                    return None, f"внутренняя ошибка инструмента ({e})"

                # Handover sentinel — short-circuit immediately.
                if tool_name == "handover_to_admin" or (
                    isinstance(result, str) and result.startswith("__HANDOVER__")
                ):
                    reason = (
                        result.split(":", 1)[1]
                        if isinstance(result, str) and ":" in result
                        else args.get("reason", "не указана")
                    )
                    handover_reason = reason
                    break

                # Terminal tool — its return value IS the client reply.
                if tool_name in TERMINAL_TOOLS:
                    terminal_result = result
                    break

                # Otherwise, send the tool result back to the LLM for another
                # round. (Currently nothing falls into this branch — every
                # tool is terminal — but we keep the path for future tools.)
                function_response_parts.append(
                    types.Part.from_function_response(
                        name=tool_name,
                        response={"result": result},
                    )
                )

            if handover_reason is not None:
                return None, handover_reason
            if terminal_result is not None:
                return terminal_result, None

            # Append model+function_response turns and loop. Should not be
            # hit in the current dispatcher setup.
            contents.append(
                types.Content(
                    role="model",
                    parts=[
                        types.Part(function_call=fc) for fc in fn_calls
                    ],
                )
            )
            contents.append(
                types.Content(role="user", parts=function_response_parts)
            )

        logger.error("[LLM] Превышен лимит раундов tool-calls — handover")
        return None, "LLM зациклился на tool calls"


gemini_client = GeminiClient()
