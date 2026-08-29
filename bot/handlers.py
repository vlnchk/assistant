import asyncio
import html
import os
import re
import time
from collections import defaultdict

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import CommandStart
from aiogram.types import Message
from sqlalchemy.future import select

from bot import replies
from db.database import async_session_maker
from db.models import AdminTopic, BotSetting, MessageHistory, Representative
from llm.gemini_client import gemini_client
from logger import logger

router = Router()
SUPERGROUP_CHAT_ID_STR = os.getenv("SUPERGROUP_CHAT_ID")
ADMIN_TELEGRAM_ID = os.getenv("ADMIN_TELEGRAM_ID")
ESCALATION_TOPIC_ID_STR = os.getenv("ESCALATION_TOPIC_ID")

try:
    SUPERGROUP_CHAT_ID = int(SUPERGROUP_CHAT_ID_STR) if SUPERGROUP_CHAT_ID_STR else None
except ValueError:
    logger.error(f"SUPERGROUP_CHAT_ID должен быть числом, получено: {SUPERGROUP_CHAT_ID_STR!r}")
    SUPERGROUP_CHAT_ID = None

try:
    CONFIGURED_ESCALATION_TOPIC_ID = (
        int(ESCALATION_TOPIC_ID_STR) if ESCALATION_TOPIC_ID_STR else None
    )
except ValueError:
    CONFIGURED_ESCALATION_TOPIC_ID = None

ESCALATION_TOPIC_ID: int | None = None


user_locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
user_llm_locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
escalation_topic_lock = asyncio.Lock()

# Rate limiting: max 10 messages per minute per user
RATE_LIMIT = 10
RATE_WINDOW = 60  # seconds
user_message_times: dict[int, list[float]] = defaultdict(list)


def is_rate_limited(telegram_id: int) -> bool:
    now = time.time()
    timestamps = user_message_times[telegram_id]
    user_message_times[telegram_id] = [t for t in timestamps if now - t < RATE_WINDOW]
    if len(user_message_times[telegram_id]) >= RATE_LIMIT:
        return True
    user_message_times[telegram_id].append(now)
    return False


def _client_mention(telegram_id: int, username: str | None, first_name: str | None) -> str:
    """Render a clickable client mention for the admin topic card."""
    if username:
        safe_username = html.escape(username)
        return f'<a href="https://t.me/{safe_username}">@{safe_username}</a>'
    label = html.escape(first_name or "клиент")
    return f'<a href="tg://user?id={telegram_id}">{label}</a>'


async def _post_client_card(
    bot: Bot,
    topic_id: int,
    telegram_id: int,
    username: str | None,
    first_name: str | None,
) -> None:
    """Post and pin a card with the clickable client mention at the top
    of a newly created forum topic. Failures are non-fatal — the card is
    nice-to-have, not critical."""
    if not SUPERGROUP_CHAT_ID:
        return
    try:
        text = replies.CLIENT_CARD.format(
            mention=_client_mention(telegram_id, username, first_name),
            telegram_id=telegram_id,
            first_name=html.escape(first_name or "—"),
        )
        card_msg = await bot.send_message(
            chat_id=SUPERGROUP_CHAT_ID,
            text=text,
            message_thread_id=topic_id,
            parse_mode="HTML",
            disable_notification=True,
        )
        try:
            await bot.pin_chat_message(
                chat_id=SUPERGROUP_CHAT_ID,
                message_id=card_msg.message_id,
                disable_notification=True,
            )
        except Exception as e:
            logger.warning(
                f"Не удалось закрепить карточку клиента в теме {topic_id}: {e}"
            )
    except Exception as e:
        logger.warning(f"Не удалось отправить карточку клиента в тему {topic_id}: {e}")


async def _mirror_to_topic(bot: Bot, admin_topic: AdminTopic | None, text: str) -> None:
    """Duplicate a bot-side reply into the client's admin topic (best effort —
    managers just lose visibility on failure, the client flow is unaffected)."""
    if not (admin_topic and SUPERGROUP_CHAT_ID):
        return
    try:
        await bot.send_message(
            chat_id=SUPERGROUP_CHAT_ID,
            text=text,
            message_thread_id=admin_topic.topic_id,
            parse_mode="HTML",
        )
    except Exception as e:
        logger.warning(
            f"Не удалось продублировать сообщение в тему {admin_topic.topic_id}: {e}"
        )


async def get_or_create_representative(
    session, telegram_id: int, username: str, first_name: str, bot: Bot
):
    async with user_locks[telegram_id]:
        rep = await session.scalar(
            select(Representative).where(Representative.telegram_id == telegram_id)
        )
        if not rep:
            rep = Representative(
                telegram_id=telegram_id, username=username, first_name=first_name
            )
            session.add(rep)
            await session.flush()

        admin_topic = await session.scalar(
            select(AdminTopic).where(AdminTopic.telegram_id == telegram_id)
        )

        if not admin_topic and SUPERGROUP_CHAT_ID:
            try:
                logger.info(
                    f"Создание темы для пользователя {telegram_id} в чате {SUPERGROUP_CHAT_ID}..."
                )
                topic = await bot.create_forum_topic(
                    chat_id=SUPERGROUP_CHAT_ID,
                    name=f"КЛИЕНТ: {first_name} {username or ''}",
                )
                admin_topic = AdminTopic(
                    topic_id=topic.message_thread_id, telegram_id=telegram_id
                )
                session.add(admin_topic)
                await session.commit()
                logger.info(
                    f"Тема {topic.message_thread_id} создана для пользователя {telegram_id}"
                )
                # Pinned card with clickable mention so managers can DM the client.
                await _post_client_card(
                    bot,
                    topic.message_thread_id,
                    telegram_id,
                    username,
                    first_name,
                )
            except Exception as e:
                logger.error(f"Не удалось создать тему для пользователя {telegram_id}: {e}")
                await session.rollback()

        return rep


async def get_or_create_escalation_topic(
    bot: Bot,
    stale_topic_id: int | None = None,
) -> int | None:
    """Return a usable service topic, replacing a known stale topic once."""
    global ESCALATION_TOPIC_ID

    if ESCALATION_TOPIC_ID and (
        stale_topic_id is None or ESCALATION_TOPIC_ID != stale_topic_id
    ):
        return ESCALATION_TOPIC_ID

    async with escalation_topic_lock:
        # Another notification may have recreated the topic while we waited.
        if ESCALATION_TOPIC_ID and (
            stale_topic_id is None or ESCALATION_TOPIC_ID != stale_topic_id
        ):
            return ESCALATION_TOPIC_ID

        async with async_session_maker() as session:
            setting = await session.scalar(
                select(BotSetting).where(BotSetting.key == "ESCALATION_TOPIC_ID")
            )
            if setting:
                try:
                    stored_topic_id = int(setting.value)
                except (TypeError, ValueError):
                    stored_topic_id = None
                if stored_topic_id and stored_topic_id != stale_topic_id:
                    ESCALATION_TOPIC_ID = stored_topic_id
                    return ESCALATION_TOPIC_ID

        if (
            CONFIGURED_ESCALATION_TOPIC_ID
            and CONFIGURED_ESCALATION_TOPIC_ID != stale_topic_id
        ):
            ESCALATION_TOPIC_ID = CONFIGURED_ESCALATION_TOPIC_ID
            return ESCALATION_TOPIC_ID

        if not SUPERGROUP_CHAT_ID:
            logger.error("Не удалось создать тему эскалаций: SUPERGROUP_CHAT_ID не задан")
            return None

        try:
            topic = await bot.create_forum_topic(
                chat_id=SUPERGROUP_CHAT_ID, name="🔔 Эскалации"
            )
            ESCALATION_TOPIC_ID = topic.message_thread_id

            async with async_session_maker() as session:
                setting = await session.scalar(
                    select(BotSetting).where(BotSetting.key == "ESCALATION_TOPIC_ID")
                )
                if setting:
                    setting.value = str(ESCALATION_TOPIC_ID)
                else:
                    session.add(
                        BotSetting(
                            key="ESCALATION_TOPIC_ID",
                            value=str(ESCALATION_TOPIC_ID),
                        )
                    )
                await session.commit()
            logger.info(f"ESCALATION_TOPIC_ID={ESCALATION_TOPIC_ID} сохранён в БД")
            return ESCALATION_TOPIC_ID
        except Exception as e:
            logger.error(f"Не удалось создать тему эскалаций: {e}")
            return None


async def _send_to_service_topic(bot: Bot, text: str, log_label: str) -> bool:
    """Send a service alert and recreate the topic once if it was deleted."""
    topic_id = await get_or_create_escalation_topic(bot)
    if not (topic_id and SUPERGROUP_CHAT_ID):
        logger.error(
            f"[{log_label}] Невозможно отправить: "
            f"topic_id={topic_id}, SUPERGROUP_CHAT_ID={SUPERGROUP_CHAT_ID}"
        )
        return False

    try:
        await bot.send_message(
            chat_id=SUPERGROUP_CHAT_ID,
            text=text,
            message_thread_id=topic_id,
            parse_mode="HTML",
        )
        logger.info(f"[{log_label}] Уведомление отправлено в тему {topic_id}")
        return True
    except TelegramBadRequest as e:
        if "message thread not found" not in str(e).lower():
            logger.error(f"[{log_label}] Не удалось отправить уведомление: {e}")
            return False
        logger.warning(
            f"[{log_label}] Тема {topic_id} больше не существует; создаю новую"
        )
    except Exception as e:
        logger.error(f"[{log_label}] Не удалось отправить уведомление: {e}")
        return False

    replacement_id = await get_or_create_escalation_topic(
        bot,
        stale_topic_id=topic_id,
    )
    if not replacement_id:
        return False
    try:
        await bot.send_message(
            chat_id=SUPERGROUP_CHAT_ID,
            text=text,
            message_thread_id=replacement_id,
            parse_mode="HTML",
        )
        logger.info(
            f"[{log_label}] Уведомление повторно отправлено в тему {replacement_id}"
        )
        return True
    except Exception as e:
        logger.error(f"[{log_label}] Повторная отправка не удалась: {e}")
        return False


async def _notify_escalation(
    bot: Bot,
    rep: Representative,
    reason: str,
    admin_topic: AdminTopic | None,
) -> None:
    """Post a standardized escalation notification in the escalation topic."""
    logger.info(f"[Эскалация] Пользователь {rep.telegram_id}, причина: {reason}")
    admin_mention = (
        f'<a href="tg://user?id={ADMIN_TELEGRAM_ID}">администратора</a>'
        if ADMIN_TELEGRAM_ID
        else "администратора"
    )
    dialog_link = (
        f'📂 <a href="https://t.me/c/{str(SUPERGROUP_CHAT_ID)[4:]}/{admin_topic.topic_id}">'
        f'Перейти к диалогу</a>\n\n'
        if admin_topic and SUPERGROUP_CHAT_ID
        else ""
    )
    notif_text = replies.ESCALATION_NOTIF.format(
        first_name=html.escape(rep.first_name or ""),
        username=html.escape(rep.username or "нет"),
        reason=html.escape(reason),
        dialog_link=dialog_link,
        admin_mention=admin_mention,
    )
    await _send_to_service_topic(bot, notif_text, "Эскалация")


async def _notify_document_task(
    bot: Bot,
    rep: Representative,
    details: str,
    admin_topic: AdminTopic | None,
) -> None:
    """Notify managers about a Sheets task without pausing the client bot."""
    logger.info(f"[Документы] Пользователь {rep.telegram_id}, задача: {details}")
    admin_mention = (
        f'<a href="tg://user?id={ADMIN_TELEGRAM_ID}">администратор</a>'
        if ADMIN_TELEGRAM_ID
        else "администратор"
    )
    dialog_link = (
        f'📂 <a href="https://t.me/c/{str(SUPERGROUP_CHAT_ID)[4:]}/{admin_topic.topic_id}">'
        f'Перейти к диалогу</a>\n\n'
        if admin_topic and SUPERGROUP_CHAT_ID
        else ""
    )
    notif_text = replies.DOCUMENT_TASK_NOTIF.format(
        first_name=html.escape(rep.first_name or ""),
        username=html.escape(rep.username or "нет"),
        details=html.escape(details),
        dialog_link=dialog_link,
        admin_mention=admin_mention,
    )
    await _send_to_service_topic(bot, notif_text, "Документы")


GRATITUDE_PATTERN = re.compile(
    r"^\s*"
    r"(?:(?:супер|отлично|понял|поняла|вс[её]\s+понятно|хорошо|класс|реально|от\s+души)"
    r"[,!.:\s]+)?"
    r"(?:"
    r"спасибо(?:(?:\s+вам)?\s+(?:большое|огромное)|\s+вам)?"
    r"(?:\s+за\s+(?:ответ|информацию|помощь))?"
    r"(?:\s+от\s+души)?"
    r"|(?:большое|огромное)(?:\s+вам)?\s+спасибо"
    r"|благодарю(?:\s+вас)?"
    r"|спс"
    r")"
    r"\s*[!.,):;\"'“”«»😊🙏🙂👍❤️❤]*\s*$",
    re.IGNORECASE,
)


def is_gratitude_only(text: str | None) -> bool:
    """Return True only for a standalone thank-you, never for a real question."""
    return bool(text and GRATITUDE_PATTERN.fullmatch(text))


PLACEMENT_REQUEST_PATTERN = re.compile(
    r"\b(?:размест\w*|опубликов\w*|публикац\w*|реклам\w*|пост\w*)\b",
    re.IGNORECASE,
)
PROMOTIONAL_CONTENT_PATTERN = re.compile(
    r"\b(?:программ\w*|курс\w*|мероприят\w*|вебинар\w*|конференц\w*|"
    r"интенсив\w*|марафон\w*|школ\w*|проект\w*|набор\w*)\b",
    re.IGNORECASE,
)
VACANCY_SIGNAL_PATTERN = re.compile(
    r"\b(?:ваканси\w*|должност\w*|зарплат\w*|трудоустрой\w*|"
    r"(?:услови|график)\w*\s+работ\w*|"
    r"ищем\s+(?:кандидат\w*|сотрудник\w*|специалист\w*|"
    r"преподавател\w*|менеджер\w*))\b",
    re.IGNORECASE,
)


def is_non_vacancy_promotion(text: str | None) -> bool:
    """Identify a request to promote a program/event, not a job vacancy."""
    if not text:
        return False
    return bool(
        PLACEMENT_REQUEST_PATTERN.search(text)
        and PROMOTIONAL_CONTENT_PATTERN.search(text)
        and not VACANCY_SIGNAL_PATTERN.search(text)
    )

@router.message(F.text.regexp(GRATITUDE_PATTERN), F.chat.type == "private")
async def handle_gratitude(message: Message, bot: Bot):
    if is_rate_limited(message.from_user.id):
        await message.answer(replies.RATE_LIMITED)
        return

    async with async_session_maker() as session:
        rep = await get_or_create_representative(
            session,
            message.from_user.id,
            message.from_user.username,
            message.from_user.first_name,
            bot,
        )

        user_text = message.text

        # Save user message
        session.add(
            MessageHistory(
                telegram_id=rep.telegram_id,
                role="user",
                content=user_text,
            )
        )
        await session.commit()

        # Mirror the client message first. In human/waiting mode the normal
        # handler would keep the bot silent, so this fast path must do the same.
        admin_topic = await session.scalar(
            select(AdminTopic).where(AdminTopic.telegram_id == rep.telegram_id)
        )
        await _mirror_to_topic(
            bot,
            admin_topic,
            replies.ADMIN_CLIENT_TEXT.format(text=html.escape(user_text)),
        )
        if admin_topic and admin_topic.status in {"waiting_human", "human_mode"}:
            return

        await message.answer(replies.GRATITUDE)
        session.add(
            MessageHistory(
                telegram_id=rep.telegram_id,
                role="assistant",
                content=replies.GRATITUDE,
            )
        )
        await session.commit()
        await _mirror_to_topic(
            bot,
            admin_topic,
            replies.ADMIN_BOT_REPLY.format(text=html.escape(replies.GRATITUDE)),
        )


@router.message(CommandStart(), F.chat.type == "private")
async def handle_start_command(message: Message, bot: Bot):
    if is_rate_limited(message.from_user.id):
        await message.answer(replies.RATE_LIMITED)
        return

    async with async_session_maker() as session:
        rep = await get_or_create_representative(
            session,
            message.from_user.id,
            message.from_user.username,
            message.from_user.first_name,
            bot,
        )

        # Save user message
        session.add(
            MessageHistory(
                telegram_id=rep.telegram_id,
                role="user",
                content="/start",
            )
        )
        await session.commit()

        # Send greeting
        await message.answer(replies.GREETING)

        # Save bot response
        session.add(
            MessageHistory(
                telegram_id=rep.telegram_id,
                role="assistant",
                content=replies.GREETING,
            )
        )
        await session.commit()

        # Forward to admin topic
        admin_topic = await session.scalar(
            select(AdminTopic).where(AdminTopic.telegram_id == rep.telegram_id)
        )
        if admin_topic:
            try:
                await bot.send_message(
                    chat_id=SUPERGROUP_CHAT_ID,
                    text=replies.ADMIN_CLIENT_TEXT.format(text="/start"),
                    message_thread_id=admin_topic.topic_id,
                    parse_mode="HTML",
                )
                await bot.send_message(
                    chat_id=SUPERGROUP_CHAT_ID,
                    text=replies.ADMIN_BOT_REPLY.format(text=html.escape(replies.GREETING)),
                    message_thread_id=admin_topic.topic_id,
                    parse_mode="HTML",
                )
            except Exception as e:
                logger.error(f"Failed to forward /start to admin topic: {e}")


@router.message(F.chat.type == "private")
async def user_private_message(message: Message, bot: Bot):
    if is_rate_limited(message.from_user.id):
        await message.answer(replies.RATE_LIMITED)
        return

    msg_text = message.text or message.caption or ""
    if len(msg_text) > 5000:
        await message.answer(replies.TOO_LONG)
        return

    async with async_session_maker() as session:
        rep = await get_or_create_representative(
            session,
            message.from_user.id,
            message.from_user.username,
            message.from_user.first_name,
            bot,
        )

        user_text = message.text or message.caption

        # Save user message
        session.add(
            MessageHistory(
                telegram_id=rep.telegram_id,
                role="user",
                content=user_text or "(Медиа/Файл)",
            )
        )
        await session.commit()

        # Forward to admin topic so admins see client's input
        admin_topic = await session.scalar(
            select(AdminTopic).where(AdminTopic.telegram_id == rep.telegram_id)
        )
        if admin_topic:
            try:
                if message.text:
                    await bot.send_message(
                        chat_id=SUPERGROUP_CHAT_ID,
                        text=replies.ADMIN_CLIENT_TEXT.format(
                            text=html.escape(message.text)
                        ),
                        message_thread_id=admin_topic.topic_id,
                        parse_mode="HTML",
                    )
                else:
                    await bot.copy_message(
                        chat_id=SUPERGROUP_CHAT_ID,
                        from_chat_id=message.chat.id,
                        message_id=message.message_id,
                        message_thread_id=admin_topic.topic_id,
                    )
            except Exception as e:
                logger.warning(f"Не удалось переслать сообщение в тему админа: {e}")

        if not user_text:
            # Чистое медиа (файл/фото без текста) — поведение зависит от
            # статуса диалога, как и для текста.
            status = admin_topic.status if admin_topic else "active"

            if status == "human_mode":
                # Менеджер ведёт диалог: файл уже переслан в тему, бот молчит
                # и статус не трогает.
                return

            if status == "waiting_human":
                # Эскалация уже была — напоминаем клиенту, без повторного алерта.
                await message.answer(replies.WAITING_HUMAN)
                await _mirror_to_topic(bot, admin_topic, replies.ADMIN_BOT_WAITING)
                return

            # active (или тема ещё не создана) — подтверждаем и эскалируем.
            ack_text = replies.MEDIA_ACK
            await message.answer(ack_text)
            session.add(
                MessageHistory(
                    telegram_id=rep.telegram_id, role="assistant", content=ack_text
                )
            )
            if admin_topic:
                admin_topic.status = "waiting_human"
            await session.commit()

            await _mirror_to_topic(
                bot,
                admin_topic,
                replies.ADMIN_BOT_REPLY.format(text=html.escape(ack_text)),
            )
            await _notify_escalation(
                bot,
                rep,
                "Был отправлен файл (медиа) без текста",
                admin_topic,
            )
            return

        # Telegram-команды /start и /help — детерминированное приветствие
        # без вызова LLM (иначе диспетчер может уйти в handover на голое /start).
        stripped = user_text.strip().lower()
        if stripped == "/start" or stripped == "/help" or stripped.startswith("/start@") or stripped.startswith("/help@"):
            greeting = replies.GREETING
            session.add(
                MessageHistory(
                    telegram_id=rep.telegram_id,
                    role="assistant",
                    content=greeting,
                )
            )
            await session.commit()
            await message.answer(greeting)
            await _mirror_to_topic(
                bot,
                admin_topic,
                replies.ADMIN_BOT_REPLY.format(text=html.escape(greeting)),
            )
            return

        # Re-read admin_topic (status may have changed via /close path)
        admin_topic = await session.scalar(
            select(AdminTopic).where(AdminTopic.telegram_id == rep.telegram_id)
        )
        if admin_topic and admin_topic.status == "waiting_human":
            await message.answer(replies.WAITING_HUMAN)
            await _mirror_to_topic(bot, admin_topic, replies.ADMIN_BOT_WAITING)
            return

        if admin_topic and admin_topic.status == "human_mode":
            # Bot stays silent in human mode.
            return

        # High-confidence business rule: announcements about programs,
        # courses and events are advertising, even when participation is free.
        # This prevents words such as "бесплатно" and "стажировка" from
        # incorrectly routing a promotional post to the vacancy form.
        if is_non_vacancy_promotion(user_text):
            reply_text = replies.FAQ_PAID_POST
            session.add(
                MessageHistory(
                    telegram_id=rep.telegram_id,
                    role="assistant",
                    content=reply_text,
                )
            )
            await session.commit()
            await message.answer(reply_text)
            await _mirror_to_topic(
                bot,
                admin_topic,
                replies.ADMIN_BOT_REPLY.format(text=html.escape(reply_text)),
            )
            return

        async with user_llm_locks[rep.telegram_id]:
            history_query = await session.execute(
                select(MessageHistory)
                .where(MessageHistory.telegram_id == rep.telegram_id)
                .order_by(MessageHistory.created_at.desc())
                .limit(40)
            )
            history_records = list(reversed(history_query.scalars().all()))

            gemini_history: list[dict] = []
            for r in history_records:
                if r.role == "assistant":
                    role = "model"
                    content = r.content
                elif r.role == "admin":
                    role = "user"
                    content = f"[Менеджер]: {r.content}"
                else:
                    role = "user"
                    content = r.content

                if gemini_history and gemini_history[-1]["role"] == role:
                    gemini_history[-1]["parts"][0] += f"\n{content}"
                else:
                    gemini_history.append({"role": role, "parts": [content]})

            if not gemini_history:
                return

            last_turn = gemini_history.pop()
            if last_turn["role"] == "model":
                gemini_history.append(last_turn)
                combined_user_text = user_text
            else:
                combined_user_text = last_turn["parts"][0]

            user_context = {
                "telegram_id": rep.telegram_id,
                "first_name": rep.first_name,
                "username": rep.username,
                "dialog_link": (
                    f"https://t.me/c/{str(SUPERGROUP_CHAT_ID)[4:]}/{admin_topic.topic_id}"
                    if admin_topic and SUPERGROUP_CHAT_ID
                    else ""
                ),
            }

            try:
                (
                    reply_text,
                    handover_reason,
                    admin_notification,
                ) = await gemini_client.generate_response(
                    gemini_history,
                    combined_user_text,
                    user_context=user_context,
                )
            except Exception as e:
                logger.error(f"Ошибка LLM для пользователя {rep.telegram_id}: {e}")
                await message.answer(replies.TECH_ERROR)
                await _mirror_to_topic(
                    bot,
                    admin_topic,
                    replies.ADMIN_LLM_ERROR.format(error=html.escape(str(e))),
                )
                return

            if handover_reason is not None:
                # Handover — fixed canonical reply, escalate in admin group.
                final_reply = reply_text or replies.HANDOVER_CLIENT
                if admin_topic:
                    admin_topic.status = "waiting_human"

                session.add(
                    MessageHistory(
                        telegram_id=rep.telegram_id,
                        role="assistant",
                        content=final_reply,
                    )
                )
                await session.commit()

                await message.answer(final_reply)
                await _mirror_to_topic(
                    bot,
                    admin_topic,
                    replies.ADMIN_BOT_REPLY.format(text=html.escape(final_reply)),
                )
                await _notify_escalation(bot, rep, handover_reason, admin_topic)
                return

            # Normal terminal tool reply
            if not reply_text:
                # Should never happen, but fail safe.
                reply_text = replies.TECH_ERROR
                logger.error("[LLM] generate_response вернул пустой reply без handover")

            session.add(
                MessageHistory(
                    telegram_id=rep.telegram_id,
                    role="assistant",
                    content=reply_text,
                )
            )
            await session.commit()

            await message.answer(reply_text)
            await _mirror_to_topic(
                bot,
                admin_topic,
                replies.ADMIN_BOT_REPLY.format(text=html.escape(reply_text)),
            )
            if admin_notification:
                await _notify_document_task(
                    bot,
                    rep,
                    admin_notification,
                    admin_topic,
                )


@router.message(F.chat.type.in_({"group", "supergroup"}))
async def admin_topic_message(message: Message, bot: Bot):
    if not message.message_thread_id or str(message.chat.id) != str(SUPERGROUP_CHAT_ID):
        return

    # Игнорируем сервисные сообщения форума и закрепления — это, в частности,
    # убирает баг «Bad Request: the message can't be copied» в момент создания
    # темы (Telegram автоматически кидает туда service-message, и без фильтра
    # бот пытался его скопировать клиенту, а тему сразу переключал в human_mode).
    if (
        message.forum_topic_created
        or message.forum_topic_edited
        or message.forum_topic_closed
        or message.forum_topic_reopened
        or getattr(message, "general_forum_topic_hidden", None)
        or getattr(message, "general_forum_topic_unhidden", None)
        or message.pinned_message
    ):
        return

    # Не реагируем на собственные сообщения бота (карточка клиента, дубли).
    if message.from_user and message.from_user.is_bot:
        return

    async with async_session_maker() as session:
        topic = await session.scalar(
            select(AdminTopic).where(AdminTopic.topic_id == message.message_thread_id)
        )
        if not topic:
            return

        if message.text and message.text.strip().lower().startswith("/close"):
            if topic.status != "active":
                topic.status = "active"
                await session.commit()
                await message.reply(replies.ADMIN_CLOSED_GROUP)
                try:
                    await bot.send_message(
                        chat_id=topic.telegram_id, text=replies.ADMIN_CLOSED
                    )
                except Exception as e:
                    logger.warning(
                        f"Не удалось уведомить клиента о сбросе диалога: {e}"
                    )
            return

        try:
            await bot.copy_message(
                chat_id=topic.telegram_id,
                from_chat_id=message.chat.id,
                message_id=message.message_id,
            )

            session.add(
                MessageHistory(
                    telegram_id=topic.telegram_id,
                    role="admin",
                    content=message.text or "(Ответ менеджера)",
                )
            )

            if topic.status != "human_mode":
                topic.status = "human_mode"
                await message.reply(replies.ADMIN_INTERCEPTED)

            await session.commit()
        except Exception as e:
            await message.reply(replies.ADMIN_SEND_FAILED.format(error=e))
