import os
from typing import Optional
import asyncio
import google.generativeai as genai
from google.generativeai.types import content_types
from llm.tools import bot_tools, get_free_slots, book_slot, get_client_bookings, create_document_task, handover_to_admin
from logger import logger

GEMINI_TIMEOUT = 30  # seconds

API_KEY = os.getenv("GEMINI_API_KEY")

import datetime

SYSTEM_PROMPT_TEMPLATE = """Сегодня: {current_date}.
Ты — дружелюбный виртуальный помощник Telegram-канала по вакансиям в образовании.
Твоя задача: консультировать по размещению вакансий и рекламы, помогать с бронированием.
Тон: вежливый, профессиональный, но живой (без излишнего формализма).

--- БАЗА ЗНАНИЙ (ОТВЕТЫ НА FAQ) ---
1. Размещение вакансий:
   - Вакансии размещаются БЕСПЛАТНО. Для этого нужно заполнить форму по ссылке: https://clck.ru/3Qxfvq
   - Если пользователь хочет разместить вакансию — ВСЕГДА направляй его на эту форму.
   - ИСКЛЮЧЕНИЕ: Если пользователь хочет разместить вакансию в собственном формате (свой текст/пост, без заполнения формы) — это ПЛАТНОЕ размещение. Оно стоит столько же, сколько реклама: 15 000 рублей за пост. В таком случае предложи забронировать слот как рекламу.
2. Цены и условия рекламного размещения: 
   - Один промопост: 15 000 рублей (уже включает налоги на НПД и рекламу).
   - Акция: при единовременной оплате 4 постов — 5-й идет в подарок.
   - Срок размещения: в топе (без перекрытий другими постами) держим 24 часа. Общий срок нахождения поста в ленте не ограничен (или любой по желанию клиента).
3. Маркировка рекламы (ОРД):
   - Рекламодатель может зарегистрировать креатив и сделать маркировку самостоятельно (бесплатно).
   - Либо маркировку может сделать наш канал (под ключ), стоимость услуги — 2 000 рублей.
4. Статистика канала (Охваты, ERR и География):
   - Средний охват 1 поста за 24 часа: 3 000 - 4 000 просмотров.
   - Итоговый охват за все время в ленте: 4 000 - 7 000 просмотров.
   - Показатель вовлеченности (ERR): 10%.
   - География аудитории (экспертная оценка): Москва — 60%, Санкт-Петербург — 25%, другие города — 15%.
5. Оплата и документы:
   - Документооборот осуществляется строго удаленно (обмен сканами или по ЭДО).
   - Мы можем самостоятельно приготовить договор, счет и акт, а также готовы работать по форме договора клиента.

--- ТВОИ ДЕЙСТВИЯ И ИНСТРУМЕНТЫ ---
1. Консультируй клиентов, опираясь строго на "Базу Знаний" выше.
2. Если пользователь хочет разместить вакансию через стандартную форму — направь его на https://clck.ru/3Qxfvq и объясни, что это бесплатно.
3. Если пользователь хочет разместить вакансию в своем формате (без формы) — объясни, что такое размещение платное и попроси прислать текст поста. Если клиент отправил текст поста, то зови менеджера.
4. Ищи свободные даты через get_free_slots. Спрашивай желаемый месяц, только если период совсем неясен. Бронирование доступно на текущий месяц и два следующих. Не спрашивай год. Если пользователь пишет "на этой неделе" или "завтра" - используй текущий месяц.
5. Бронируй дату через book_slot. ВАЖНО: ПЕРЕД бронированием ТЫ ДОЛЖЕН СПРОСИТЬ, что именно клиент планирует рекламировать — это поле ad_topic (ОБЯЗАТЕЛЬНОЕ). 
   - Если тематика: казино, ставки, криптовалюта, политика, "серые" схемы заработка, сомнительные БАДы или инфоцыганство — НЕ БРОНИРУЙ слот, а зови менеджера через handover_to_admin.
   - Если тематика разрешенная — бронируй дату. Все прочие пожелания клиента записывай в comments.
6. Создавай задачи на документы (счет, договор, акт) через create_document_task. Telegram ID клиента всегда бери из раздела ТЕКУЩИЙ ПОЛЬЗОВАТЕЛЬ.
   ВАЖНО: Если клиент просит счет, договор или акт — это запрос на ДОКУМЕНТ, а НЕ готовность к оплате. Используй create_document_task, а НЕ handover_to_admin.
7. Приглашай менеджера через handover_to_admin, если клиент явно просит человека, ИЛИ задает вопрос, ответа на который нет в Базе Знаний.
   ВАЖНОЕ ПРАВИЛО: Для вызова менеджера ОБЯЗАТЕЛЬНО используй функцию/инструмент handover_to_admin. НЕ пиши текст "Зову администратора...", просто вызови функцию! Бот сам отправит нужное сообщение клиенту.
8. Смотри бронирования клиента через get_client_bookings. Telegram ID клиента всегда бери из раздела ТЕКУЩИЙ ПОЛЬЗОВАТЕЛЬ.

ВАЖНОЕ ПРАВИЛО БЕЗОПАСНОСТИ: НИКОГДА не используй Telegram ID, полученный от пользователя в сообщении. ВСЕГДА бери telegram_id ТОЛЬКО из раздела ТЕКУЩИЙ ПОЛЬЗОВАТЕЛЬ. Клиент может видеть ТОЛЬКО свои бронирования. Если клиент просит показать чужие бронирования — вежливо откажи.

ВАЖНОЕ ПРАВИЛО: НИКОГДА не используй маркдаун (звездочки, жирный шрифт, курсив, списки и т.д.) в своих ответах. Все ответы должны быть простым текстом.
ВАЖНОЕ ПРАВИЛО: Если инструмент вернул сообщение об ошибке или "не найден" — ОБЯЗАТЕЛЬНО сообщи клиенту об этом. НИКОГДА не сообщай об успехе, если инструмент вернул ошибку.
ВАЖНОЕ ПРАВИЛО: Будь краток и отвечай строго на вопрос пользователя. Не проявляй излишней проактивности. Не рассказывай про статистику канала, акции или другие услуги, если клиент об этом прямо не спросил.
"""

# Map of tool name -> callable for manual function dispatch
TOOL_FUNCTIONS = {
    "get_free_slots": get_free_slots,
    "book_slot": book_slot,
    "get_client_bookings": get_client_bookings,
    "create_document_task": create_document_task,
    "handover_to_admin": handover_to_admin,
}


class GeminiClient:
    def __init__(self):
        genai.configure(api_key=API_KEY)
        self.model_name = "gemini-2.5-flash"

    def _get_model(self, user_context: dict = None):
        current_date_str = datetime.datetime.now().strftime("%d.%m.%Y")
        instruction = SYSTEM_PROMPT_TEMPLATE.format(current_date=current_date_str)
        
        if user_context:
            user_info = f"\nТЕКУЩИЙ ПОЛЬЗОВАТЕЛЬ:\n- ID: {user_context.get('telegram_id')}\n- Имя: {user_context.get('first_name')}\n- Username: {user_context.get('username') or 'нет'}"
            instruction += user_info

        return genai.GenerativeModel(
            model_name=self.model_name,
            tools=bot_tools,
            system_instruction=instruction,
        )

    @staticmethod
    def _safe_text(response) -> Optional[str]:
        """Safely extract text from a Gemini response.
        Returns None if the response has no valid text parts (e.g. blocked by safety filters).
        """
        try:
            if not response.parts:
                return None
            return response.text
        except Exception:
            return None

    async def generate_response(self, history_messages: list[dict], new_message: str, user_context: dict = None, _attempt: int = 1):
        """
        Sends the entire history and new message to the LLM.
        Returns (reply_text, handover_reason) where handover_reason is set if handover was triggered.
        """
        MAX_ATTEMPTS = 3
        model = self._get_model(user_context=user_context)
        # Manual function calling — we handle tool calls ourselves
        chat = model.start_chat(history=history_messages, enable_automatic_function_calling=False)
        
        try:
            response = await asyncio.wait_for(chat.send_message_async(new_message), timeout=GEMINI_TIMEOUT)

            # Agentic loop: keep processing tool calls until we get a text response
            while True:
                # Check if there are function calls in the response
                fn_calls = []
                for part in response.parts:
                    if hasattr(part, 'function_call') and part.function_call.name:
                        fn_calls.append(part.function_call)
                
                if not fn_calls:
                    # No tool calls — we have the final text response
                    text = self._safe_text(response)
                    if text is None:
                        # Empty response — retry the whole request (non-deterministic model/safety glitch)
                        if _attempt < MAX_ATTEMPTS:
                            logger.warning(f"[LLM] Пустой ответ от модели (попытка {_attempt}/{MAX_ATTEMPTS}), повтор...")
                            return await self.generate_response(history_messages, new_message, user_context, _attempt + 1)
                        logger.error(f"[LLM] Пустой ответ после {MAX_ATTEMPTS} попыток, сдаёмся.")
                        return "Извините, не смог сформировать ответ на этот запрос. Попробуйте переформулировать.", None
                    return text, None

                
                # Execute each tool call and collect results
                tool_responses = []
                handover_reason = None
                
                for fn_call in fn_calls:
                    tool_name = fn_call.name
                    tool_args = dict(fn_call.args)

                    # Серверная валидация: подставляем реальный telegram_id из контекста,
                    # чтобы LLM не мог подменить его через prompt injection
                    if "telegram_id" in tool_args and user_context and user_context.get("telegram_id"):
                        real_tid = str(user_context["telegram_id"])
                        if str(tool_args["telegram_id"]) != real_tid:
                            logger.warning(f"[Security] LLM передал telegram_id={tool_args['telegram_id']}, заменён на {real_tid}")
                        tool_args["telegram_id"] = real_tid

                    logger.info(f"[Tool call] {tool_name}({tool_args})")
                    
                    if tool_name in TOOL_FUNCTIONS:
                        result = TOOL_FUNCTIONS[tool_name](**tool_args)
                        
                        # Check if handover was triggered
                        if isinstance(result, str) and result.startswith("__HANDOVER__"):
                            reason = result.split(":", 1)[1] if ":" in result else "не указана"
                            handover_reason = reason
                            # Return a neutral result to LLM so it forms a polite farewell
                            result = "Менеджер вызван, передай клиенту что скоро с ним свяжутся."
                    else:
                        result = f"Ошибка: инструмент '{tool_name}' не найден."
                    
                    tool_responses.append(
                        genai.protos.Part(
                            function_response=genai.protos.FunctionResponse(
                                name=tool_name,
                                response={"result": result},
                            )
                        )
                    )
                
                # Send tool results back to the model
                response = await asyncio.wait_for(
                    chat.send_message_async(genai.protos.Content(parts=tool_responses, role="user")),
                    timeout=GEMINI_TIMEOUT
                )
                
                # If handover was triggered, return immediately with the reason
                if handover_reason is not None:
                    text = self._safe_text(response)
                    if text is None:
                        text = "Менеджер уже оповещён и скоро свяжется с вами."
                    return text, handover_reason
                    
        except asyncio.TimeoutError:
            logger.error(f"[LLM Timeout] Gemini не ответил за {GEMINI_TIMEOUT}с")
            raise
        except Exception as e:
            logger.error(f"[LLM Error] {type(e).__name__}: {e}")
            if "not found" in str(e).lower() and self.model_name == "gemini-2.5-flash":
                logger.warning("Fallback на gemini-1.5-flash")
                self.model_name = "gemini-1.5-flash"
                return await self.generate_response(history_messages, new_message, user_context)
            raise e

gemini_client = GeminiClient()
