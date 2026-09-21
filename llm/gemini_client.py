"""Single-model Gemini routing through Flash-Lite.

This module only obtains a tool decision. Validation, trusted-context
injection and execution are separate so a side-effecting tool runs at most once.
"""

import asyncio
import datetime
import os
import time
from typing import Any, Optional

from google import genai
from google.genai import types

from assistant.models import ModelUsage, RouteDecision
from llm.tools import bot_tools
from llm.tool_executor import (
    ProductionToolExecutor,
    TOOL_FUNCTIONS,
    ToolCallError,
    inject_server_context,
)
from logger import logger


API_KEY = os.getenv("GEMINI_API_KEY")
MODEL_NAME = os.getenv(
    "GEMINI_MODEL",
    os.getenv("GEMINI_LITE_MODEL", "gemini-3.5-flash-lite"),
)
GEMINI_TIMEOUT = float(os.getenv("GEMINI_TIMEOUT", "10"))


SYSTEM_PROMPT_TEMPLATE = """Сегодня: {current_date}.

ВНИМАНИЕ: ТЫ — СТРОГИЙ ДИСПЕТЧЕР-РОУТЕР (API). Твоя ЕДИНСТВЕННАЯ задача — классифицировать запрос пользователя и вызвать соответствующую функцию (tool).
ТЕБЕ СТРОГО ЗАПРЕЩЕНО ОТВЕЧАТЬ СВОБОДНЫМ ТЕКСТОМ! Любое твое текстовое сообщение приведет к критической ошибке системы бота.

На любое сообщение пользователя ты ОБЯЗАН выбрать ровно ОДИН инструмент из списка и вызвать его.
Не пытайся генерировать тексты для клиентов самостоятельно (даже цены или описания) — бот сам отправит эталонный текст после вызова нужной функции.

Если в ОДНОМ сообщении несколько информационных вопросов, вызови ровно один
answer_information и перечисли ВСЕ нужные темы. Например:
  «Сколько стоит, какие форматы и есть ли даты в июне?»
  → answer_information(topics=["paid_post", "ad_formats", "free_slots"], month="июнь")
Такой составной tool остаётся одним вызовом, но вернёт несколько канонических
блоков. Не выбирай только один FAQ и не теряй остальные вопросы клиента.

За один ход разрешено не более ОДНОГО изменяющего действия: бронирование,
создание задачи по документам или передача конкретной операции менеджеру.
Если клиент явно просит выполнить действие, выбери это действие; не пытайся
одновременно вызвать дополнительные tools.

ЕСЛИ НИ ОДИН ИНСТРУМЕНТ НЕ ПОДХОДИТ или ситуация нестандартная — вызови инструмент handover_to_admin.
Если сообщение является только благодарностью — вызови reply_gratitude;
но благодарность перед реальным вопросом не должна скрывать вопрос.

--- ПРАВИЛА ВЫБОРА ИНСТРУМЕНТА ---

Приветствие / «привет» / «здравствуйте» / «hi» / «hello» / «/start» / «/help»:
  → send_greeting

Вопрос «ты бот?» / «у тебя есть чувства?» / «как тебя зовут?»:
  → reply_bot_nature

Болталка, погода, шутки, личные темы, прощания, любые
вопросы не по теме канала:
  → reply_offtopic

Чистая благодарность («спасибо», «благодарю», «от души спасибо»):
  → reply_gratitude
Если благодарность является только вводной частью вопроса («Спасибо, а какие
даты свободны?»), игнорируй вводную благодарность и обработай сам вопрос.

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
  ЯВНЫЙ СИГНАЛ ВАКАНСИИ ИМЕЕТ ПРИОРИТЕТ над словами про продукт или отрасль.
  Если клиент прямо пишет «вакансия», называет должность, зарплату или ищет
  сотрудника/специалиста/преподавателя, это вакансия. Слова «курс», «школа»,
  «программа» или «проект» могут описывать место работы и сами по себе не
  превращают вакансию в рекламу. Например:
    - «Хочу разместить вакансию преподавателя курса» → faq_free_posting
    - «Ищем менеджера образовательного проекта» → faq_free_posting
  ИСКЛЮЧЕНИЕ: если клиент спрашивает цену размещения вакансии, разницу
  бесплатного и платного вариантов, свой формат, готовый текст, отсутствие
  прямых контактов или изображение — вызывай
  answer_information(topics=["vacancy_options"]), а не faq_free_posting.

Вопросы именно про рекламу / промопост (не вакансию), цену рекламного поста
или акцию «4+1»:
  → faq_paid_post
  Объявления о программах, курсах, школах, мероприятиях, вебинарах,
  интенсивах и наборах на обучение — это рекламные посты, а НЕ вакансии.
  Но если прямо указана вакансия, должность или поиск сотрудника, применяй
  правило вакансии выше, даже когда человек будет работать над курсом.
  Фразы «бесплатно для студентов/участников», «благотворительный проект»
  или обещание стажировки описывают продукт и НЕ делают размещение бесплатным.
  Если просят разместить информацию о таком проекте в канале или паблике,
  вызывай faq_paid_post. Бесплатная форма допустима только для настоящей
  вакансии: конкретной должности или поиска сотрудника.

Общий вопрос об условиях или цене, где продукт НЕ назван:
  → ask_placement_type
  Условия вакансии и рекламного поста отличаются в разы, поэтому на такой
  вопрос сначала уточняем продукт, а не выдаём оба прайса сразу. Например:
    - «Какие условия размещения?»
    - «Расскажите про условия»
    - «Сколько у вас стоит?»
    - «Что по прайсу?»
  Это ЕДИНСТВЕННЫЙ случай для ask_placement_type. Условие срабатывания
  жёсткое: в сообщении нет ни слова «вакансия», ни слов «реклама»,
  «рекламный пост», «промопост», «пост», «анонс», нет должности, нет описания
  программы, курса или мероприятия — и продукт не назван раньше в истории
  диалога. Если хоть один такой признак есть, продукт считается известным.
  Отсылка к уже названной цене или предложению («за такую стоимость»,
  «за эти деньги», «по этой цене») тоже означает, что продукт уже обсуждается:
  уточнять не нужно.
  ЗАПРЕЩЕНО вызывать ask_placement_type, когда продукт уже понятен:
    - «Как разместить вакансию?» → faq_free_posting
    - «Сколько стоит рекламный пост?» → faq_paid_post
    - «Сколько стоит пост у вас?» → faq_paid_post
    - «Какие условия размещения за такую стоимость?» → faq_paid_post
    - «Чем платная вакансия отличается от бесплатной?»
      → answer_information(topics=["vacancy_options"])
    - «Сколько стоит вакансия и сколько реклама?»
      → answer_information(topics=["vacancy_options", "paid_post"])
    - вопрос про охваты, ОРД, документы, форматы, свободные даты или
      модерацию → соответствующий инструмент
  Уточняем не больше одного раза за диалог: как только клиент назвал продукт
  (в ответ на уточнение или раньше), сразу вызывай нужный инструмент.

Вопросы про охваты, ERR, аудиторию, географию:
  → faq_stats

Вопросы про маркировку рекламы / ОРД:
  → faq_ord

Вопросы про документы, ЭДО, договор, оплату по форме клиента:
  → faq_docs

НОВЫЕ И СОСТАВНЫЕ FAQ через answer_information:
  - платная вакансия или сравнение бесплатной и платной вакансии
    → vacancy_options
  - вопрос о бесплатной рекламе → free_advertising
  - форматы, фото/видео, нативный пост, закреп, редактура → ad_formats
  - переходы, CTR, конверсия, CPA → performance
  - ИП/СЗ, НДС, реквизиты, юридическая форма оплаты → payment_legal
  - CPA, агентская модель, оплата за установки → partnerships
  - взаимопиар, взаимный пиар или «ВП» → request_mutual_pr_support
    Это обязательная эскалация менеджеру, не используй partnerships.
  - поиск работы, просмотр вакансий, размещение резюме → jobseeker
  - сроки и общие правила модерации бесплатной вакансии → moderation
  - вопрос о точном времени публикации → publication_time
Для одного такого вопроса тоже используй answer_information с одной темой.

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
  Календарь — ответ на вопрос О ДАТАХ. Если клиент спрашивает про условия,
  цену или возможность размещения, а дату («на сегодня», «на этой неделе»)
  упоминает лишь как срок, сначала ответь на вопрос об условиях; при
  необходимости добавь даты темой free_slots в answer_information. Например:
    - «Хотели бы на сегодня купить пост-вакансию, подскажите, возможно ли
      это и на каких условиях?»
      → answer_information(topics=["vacancy_options"]) — вопрос об условиях
      платной вакансии, а не о свободных датах.

Клиент выбрал ПЛАТНОЕ размещение ВАКАНСИИ («давайте платно», «нужна
платная вакансия», «беру платный вариант», «хочу вакансию платно»):
  → request_paid_vacancy
  Платная вакансия НЕ бронирует слот: календарь и свободные даты — только
  для рекламы. НИКОГДА не вызывай get_free_slots, check_dates_availability,
  book_slot или ask_ad_topic, когда речь о вакансии, даже если клиент сам
  назвал дату.
  Если клиент уже прислал текст вакансии, передай его в vacancy_text
  дословно. Если текста ещё нет, вызови tool без аргумента — он сам
  попросит текст.
  Итоговую цену не называй: «от 3 000 ₽» — единственная цифра бота.

Клиент хочет забронировать дату:
  - если НЕ известна тематика рекламы → ask_ad_topic
  - если тематика запрещена (казино, ставки, крипта, политика, серые
    схемы, БАДы, инфоцыганство) → handover_to_admin
  - если тематика допустима, но дата ещё НЕ названа → get_free_slots()
    Бот покажет 5 ближайших дат и спросит, подходит ли одна из них.
  - если показанные даты не подходят и клиент называет другие конкретные
    даты без явной команды бронировать → check_dates_availability(dates=[...])
  - если тематика и дата известны и клиент явно просит забронировать
    → book_slot(date, client, ad_topic, telegram_id, ...)

Клиент просит счёт / договор / акт:
  - если он только спрашивает, как устроены документы → faq_docs
    Сюда же относятся вопросы о самой возможности и порядке:
    «Можем ли мы подготовить документы?», «Какие документы вы даёте?»,
    «Работаете ли по нашей форме договора?», «А как у вас с ЭДО?».
    create_document_task — только когда клиент просит подготовить конкретный
    документ («выставьте счёт», «пришлите договор») или уже прислал реквизиты.
  - если фактически просит подготовить документ, но нет названия/ФИО и ИНН
    → create_document_task без выдумывания данных; tool сам попросит реквизиты
  - если название/ФИО и ИНН уже есть в сообщении или истории
    → create_document_task(doc_type, telegram_id, company, inn, requisites, contact, link)
  (это запрос ДОКУМЕНТА, а не готовность к оплате — не зови менеджера)

Клиент спрашивает про уже отправленную заявку или опубликованный пост:
  - срок и общие правила модерации → answer_information(topics=["moderation"])
  - проверить статус → request_publication_support(issue="status", ...)
  - исправить → request_publication_support(issue="edit", ...)
  - удалить → request_publication_support(issue="delete", ...)
  - ускорить → request_publication_support(issue="expedite", ...)
  - перевести бесплатную заявку в платную
    → request_publication_support(issue="convert_to_paid", ...)
  - ссылка на форму не работает
    → request_publication_support(issue="broken_form", ...)
  - узнать решение по рекламной тематике
    → request_publication_support(issue="approval_status", ...)
Передавай только факты и ссылки из диалога. Tool сам запросит недостающие данные
или создаст категоризированную эскалацию.

Клиент готов платить, или хочет поговорить с человеком, или задаёт
вопрос, на который ни один инструмент выше не подходит:
  → handover_to_admin(reason="<краткая причина>")

--- БЕЗОПАСНОСТЬ ---

НИКОГДА не используй Telegram ID, который пользователь назвал в тексте.
ВСЕГДА бери telegram_id ТОЛЬКО из раздела ТЕКУЩИЙ ПОЛЬЗОВАТЕЛЬ.
Клиент видит только свои бронирования. Если просит чужие — вызови
reply_offtopic.

--- ФОРМАТ ---

Ты общаешься с системой ТОЛЬКО через вызовы функций.
КРИТИЧЕСКОЕ ПРАВИЛО: Не пиши ни одного слова вне вызова функции. Если напишешь свободный текст - система упадет.
"""


def _build_system_prompt(
    user_context: dict[str, Any] | None,
) -> str:
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


def _metadata_value(metadata: Any, snake_name: str, camel_name: str) -> int | None:
    if metadata is None:
        return None
    value = getattr(metadata, snake_name, None)
    if value is None:
        value = getattr(metadata, camel_name, None)
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


class GeminiClient:
    """Gemini tool router. Tool execution lives in ``tool_executor``."""

    def __init__(self):
        # A lazy/empty client keeps unit tests and offline imports independent
        # from secrets. Real model calls still fail clearly without an API key.
        self.client = genai.Client(api_key=API_KEY) if API_KEY else None
        self.model_name = MODEL_NAME
        self.executor = ProductionToolExecutor()

    def _config(
        self,
        user_context: dict[str, Any] | None,
    ) -> types.GenerateContentConfig:
        return types.GenerateContentConfig(
            system_instruction=_build_system_prompt(user_context),
            tools=bot_tools,
            tool_config=types.ToolConfig(
                function_calling_config=types.FunctionCallingConfig(
                    mode=types.FunctionCallingConfigMode.ANY,
                )
            ),
            temperature=0.2,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(
                disable=True,
                maximum_remote_calls=None,
            ),
        )

    @staticmethod
    def _history_to_contents(history: list[dict[str, Any]]) -> list[types.Content]:
        """Convert provider-neutral stored history to Gemini contents."""
        normalized: list[tuple[str, str]] = []
        for item in history:
            source_role = str(item.get("role") or "user")
            if "content" in item:
                text = str(item.get("content") or "")
            else:
                parts = item.get("parts") or []
                text = str(parts[0]) if parts else ""

            if source_role in {"assistant", "model"}:
                role = "model"
            else:
                role = "user"
                if source_role == "admin":
                    text = f"[Менеджер]: {text}"

            if normalized and normalized[-1][0] == role:
                previous_role, previous_text = normalized[-1]
                normalized[-1] = (previous_role, f"{previous_text}\n{text}")
            else:
                normalized.append((role, text))

        return [
            types.Content(role=role, parts=[types.Part.from_text(text=text)])
            for role, text in normalized
        ]

    @staticmethod
    def _extract_function_calls(response: Any) -> list[Any]:
        """Pull function-call parts from a response, robust to API shape."""
        calls: list[Any] = []
        if getattr(response, "function_calls", None):
            return list(response.function_calls)
        for candidate in getattr(response, "candidates", []) or []:
            content = getattr(candidate, "content", None)
            if not content:
                continue
            for part in getattr(content, "parts", []) or []:
                fn_call = getattr(part, "function_call", None)
                if fn_call and getattr(fn_call, "name", None):
                    calls.append(fn_call)
        return calls

    @staticmethod
    def _usage(response: Any) -> ModelUsage:
        metadata = getattr(response, "usage_metadata", None)
        return ModelUsage(
            prompt_tokens=_metadata_value(
                metadata, "prompt_token_count", "promptTokenCount"
            ),
            candidate_tokens=_metadata_value(
                metadata, "candidates_token_count", "candidatesTokenCount"
            ),
            thoughts_tokens=_metadata_value(
                metadata, "thoughts_token_count", "thoughtsTokenCount"
            ),
            total_tokens=_metadata_value(
                metadata, "total_token_count", "totalTokenCount"
            ),
        )

    def _inject_server_context(
        self,
        tool_name: str,
        args: dict[str, Any],
        user_context: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Backward-compatible facade for tests and external callers."""
        return inject_server_context(tool_name, args, user_context)

    def _inject_real_telegram_id(
        self,
        args: dict[str, Any],
        user_context: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Backward-compatible helper retained for older callers."""
        if "telegram_id" not in args:
            return args
        return inject_server_context("get_client_bookings", args, user_context)

    async def _call_tool(self, name: str, args: dict[str, Any]) -> str:
        """Backward-compatible raw tool execution helper."""
        fn = TOOL_FUNCTIONS.get(name)
        if not fn:
            raise ToolCallError(f"инструмент '{name}' не найден")
        try:
            return await asyncio.to_thread(fn, **args)
        except TypeError as exc:
            raise ToolCallError(f"{name}: неверные аргументы") from exc

    async def route(
        self,
        history_messages: list[dict[str, Any]],
        new_message: str,
        user_context: dict[str, Any] | None = None,
        *,
        model_name: str = MODEL_NAME,
        timeout_s: float | None = None,
    ) -> RouteDecision:
        """Ask one model to select one tool without executing that tool."""
        started = time.perf_counter()
        timeout_s = timeout_s or GEMINI_TIMEOUT
        if self.client is None:
            raise RuntimeError("GEMINI_API_KEY не задан")

        contents = self._history_to_contents(history_messages)
        contents.append(
            types.Content(
                role="user",
                parts=[types.Part.from_text(text=new_message)],
            )
        )

        try:
            response = await asyncio.wait_for(
                self.client.aio.models.generate_content(
                    model=model_name,
                    contents=contents,
                    config=self._config(user_context),
                ),
                timeout=timeout_s,
            )
        except asyncio.TimeoutError:
            latency_ms = (time.perf_counter() - started) * 1000
            logger.error(f"[LLM Timeout] {model_name} не ответил за {timeout_s}с")
            return RouteDecision(
                model=model_name,
                latency_ms=latency_ms,
                error="timeout",
            )

        latency_ms = (time.perf_counter() - started) * 1000
        usage = self._usage(response)
        fn_calls = self._extract_function_calls(response)
        if not fn_calls:
            stray = (getattr(response, "text", "") or "").strip()
            logger.warning(
                f"[LLM] {model_name} ответил без tool: {stray[:200]!r}"
            )
            return RouteDecision(
                model=model_name,
                latency_ms=latency_ms,
                usage=usage,
                error="no_tool_call",
            )
        if len(fn_calls) != 1:
            logger.warning(
                f"[LLM] {model_name} вызвал {len(fn_calls)} tools вместо одного"
            )
            return RouteDecision(
                model=model_name,
                latency_ms=latency_ms,
                usage=usage,
                error="multiple_tool_calls",
            )

        fn_call = fn_calls[0]
        tool_name = str(fn_call.name)
        arguments = dict(fn_call.args or {})
        if tool_name not in TOOL_FUNCTIONS:
            return RouteDecision(
                model=model_name,
                tool_name=tool_name,
                arguments=arguments,
                latency_ms=latency_ms,
                usage=usage,
                error="unknown_tool",
            )

        return RouteDecision(
            model=model_name,
            tool_name=tool_name,
            arguments=arguments,
            latency_ms=latency_ms,
            usage=usage,
        )

    async def generate_response(
        self,
        history_messages: list[dict[str, Any]],
        new_message: str,
        user_context: dict[str, Any] | None = None,
        _timeout_retry: bool = False,
    ) -> tuple[Optional[str], Optional[str], Optional[str]]:
        """Backward-compatible one-model route-and-execute method."""
        del _timeout_retry
        decision = await self.route(
            history_messages,
            new_message,
            user_context,
            model_name=self.model_name,
            timeout_s=GEMINI_TIMEOUT,
        )
        if decision.error:
            reason = (
                "LLM timeout"
                if decision.error == "timeout"
                else f"LLM routing error ({decision.error})"
            )
            return None, reason, None

        try:
            prepared = self.executor.prepare(
                decision.tool_name or "",
                decision.arguments,
                user_context,
            )
            outcome = await self.executor.execute_prepared(
                decision.tool_name or "",
                prepared,
            )
        except ToolCallError as exc:
            logger.error(f"[Tool] {exc} — уходим в handover")
            return None, f"внутренняя ошибка инструмента ({exc})", None

        return (
            outcome.reply_text,
            outcome.handover_reason,
            outcome.admin_notification,
        )


gemini_client = GeminiClient()
