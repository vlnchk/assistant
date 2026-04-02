import os
import html
from aiogram import Router, F, Bot
from aiogram.types import Message
from sqlalchemy.future import select

from db.database import async_session_maker
from db.models import Representative, AdminTopic, MessageHistory, BotSetting
from llm.gemini_client import gemini_client
from logger import logger

router = Router()
SUPERGROUP_CHAT_ID_STR = os.getenv("SUPERGROUP_CHAT_ID")
ADMIN_TELEGRAM_ID = os.getenv("ADMIN_TELEGRAM_ID")
ESCALATION_TOPIC_ID_STR = os.getenv("ESCALATION_TOPIC_ID")

try:
    SUPERGROUP_CHAT_ID = int(SUPERGROUP_CHAT_ID_STR) if SUPERGROUP_CHAT_ID_STR else None
except ValueError:
    SUPERGROUP_CHAT_ID = SUPERGROUP_CHAT_ID_STR

try:
    ESCALATION_TOPIC_ID = int(ESCALATION_TOPIC_ID_STR) if ESCALATION_TOPIC_ID_STR else None
except ValueError:
    ESCALATION_TOPIC_ID = None

import asyncio
import time
from collections import defaultdict

user_locks = defaultdict(asyncio.Lock)
user_llm_locks = defaultdict(asyncio.Lock)

# Rate limiting: max 10 messages per minute per user
RATE_LIMIT = 10
RATE_WINDOW = 60  # seconds
user_message_times: dict[int, list[float]] = defaultdict(list)

def is_rate_limited(telegram_id: int) -> bool:
    now = time.time()
    timestamps = user_message_times[telegram_id]
    # Remove timestamps older than the window
    user_message_times[telegram_id] = [t for t in timestamps if now - t < RATE_WINDOW]
    if len(user_message_times[telegram_id]) >= RATE_LIMIT:
        return True
    user_message_times[telegram_id].append(now)
    return False

async def get_or_create_representative(session, telegram_id: int, username: str, first_name: str, bot: Bot):
    async with user_locks[telegram_id]:
        rep = await session.scalar(
            select(Representative)
            .where(Representative.telegram_id == telegram_id)
        )
        if not rep:
            rep = Representative(telegram_id=telegram_id, username=username, first_name=first_name)
            session.add(rep)
            await session.flush()
        
        # Check if Topic exists
        admin_topic = await session.scalar(select(AdminTopic).where(AdminTopic.telegram_id == telegram_id))
        
        # Create Topic if missing and supergroup is configured
        if not admin_topic and SUPERGROUP_CHAT_ID:
            try:
                logger.info(f"Создание темы для пользователя {telegram_id} в чате {SUPERGROUP_CHAT_ID}...")
                topic = await bot.create_forum_topic(chat_id=SUPERGROUP_CHAT_ID, name=f"КЛИЕНТ: {first_name} {username or ''}")
                admin_topic = AdminTopic(topic_id=topic.message_thread_id, telegram_id=telegram_id)
                session.add(admin_topic)
                await session.commit()
                logger.info(f"Тема {topic.message_thread_id} создана для пользователя {telegram_id}")
            except Exception as e:
                logger.error(f"Не удалось создать тему для пользователя {telegram_id}: {e}")
                await session.rollback() # Important to rollback if db constraint fails
                    
        return rep

async def get_or_create_escalation_topic(bot: Bot) -> int:
    """Returns the message_thread_id of the escalation topic, creating it if necessary."""
    global ESCALATION_TOPIC_ID
    if ESCALATION_TOPIC_ID:
        return ESCALATION_TOPIC_ID

    # Попробовать загрузить из БД
    async with async_session_maker() as session:
        setting = await session.scalar(
            select(BotSetting).where(BotSetting.key == "ESCALATION_TOPIC_ID")
        )
        if setting:
            ESCALATION_TOPIC_ID = int(setting.value)
            return ESCALATION_TOPIC_ID

    # Создать новую тему и сохранить в БД
    try:
        topic = await bot.create_forum_topic(chat_id=SUPERGROUP_CHAT_ID, name="🔔 Эскалации")
        ESCALATION_TOPIC_ID = topic.message_thread_id

        async with async_session_maker() as session:
            session.add(BotSetting(key="ESCALATION_TOPIC_ID", value=str(ESCALATION_TOPIC_ID)))
            await session.commit()
            logger.info(f"ESCALATION_TOPIC_ID={ESCALATION_TOPIC_ID} сохранён в БД")

        return ESCALATION_TOPIC_ID
    except Exception as e:
        logger.error(f"Не удалось создать тему эскалаций: {e}")
        return None

@router.message(F.chat.type == "private")
async def user_private_message(message: Message, bot: Bot):
    if is_rate_limited(message.from_user.id):
        await message.answer("Вы отправляете сообщения слишком часто. Пожалуйста, подождите минуту.")
        return

    msg_text = message.text or message.caption or ""
    if len(msg_text) > 5000:
        await message.answer("Сообщение слишком длинное. Пожалуйста, напишите короче (до 5000 символов).")
        return

    async with async_session_maker() as session:
        rep = await get_or_create_representative(session, message.from_user.id, message.from_user.username, message.from_user.first_name, bot)
        
        # Effective text: use caption if no text (e.g. document with caption)
        user_text = message.text or message.caption
        
        # Save user message
        user_msg = MessageHistory(telegram_id=rep.telegram_id, role="user", content=user_text or "(Медиа/Файл)")
        session.add(user_msg)
        await session.commit()
        
        # Forward all messages to topic immediately so admins see client's input
        admin_topic = await session.scalar(select(AdminTopic).where(AdminTopic.telegram_id == rep.telegram_id))
        if admin_topic:
            try:
                if message.text:
                    await bot.send_message(
                        chat_id=SUPERGROUP_CHAT_ID,
                        text=f"👤 <b>Клиент:</b>\n{html.escape(message.text)}",
                        message_thread_id=admin_topic.topic_id,
                        parse_mode="HTML"
                    )
                else:
                    await bot.copy_message(
                        chat_id=SUPERGROUP_CHAT_ID, 
                        from_chat_id=message.chat.id, 
                        message_id=message.message_id, 
                        message_thread_id=admin_topic.topic_id
                    )
            except Exception as e:
                logger.warning(f"Не удалось переслать сообщение в тему админа: {e}")
                
            if not user_text:
                # Pure media with no text — acknowledge receipt and trigger escalation
                ack_text = "Получил ваш файл, передаю менеджеру."
                await message.answer(ack_text)
                bot_msg = MessageHistory(telegram_id=rep.telegram_id, role="assistant", content=ack_text)
                session.add(bot_msg)
                
                # Update status to waiting_human if topic exists
                if admin_topic:
                    admin_topic.status = "waiting_human"
                await session.commit()
                
                if admin_topic:
                    await bot.send_message(chat_id=SUPERGROUP_CHAT_ID, text=f"🤖 <b>Бот:</b>\n{html.escape(ack_text)}", message_thread_id=admin_topic.topic_id, parse_mode="HTML")
                
                # Trigger escalation
                reason = "Был отправлен файл (медиа) без текста"
                logger.info(f"[Эскалация] Пользователь {rep.telegram_id}, причина: {reason}")
                esc_topic_id = await get_or_create_escalation_topic(bot)
                if esc_topic_id and SUPERGROUP_CHAT_ID:
                    admin_mention = f'<a href="tg://user?id={ADMIN_TELEGRAM_ID}">администратора</a>' if ADMIN_TELEGRAM_ID else "администратора"
                    dialog_link = f'📂 <a href="https://t.me/c/{str(SUPERGROUP_CHAT_ID)[4:]}/{admin_topic.topic_id}">Перейти к диалогу</a>\n\n' if admin_topic else ""
                    notif_text = (
                        f"🔔 <b>ВНИМАНИЕ: ЭСКАЛАЦИЯ</b>\n\n"
                        f"👤 Клиент: {html.escape(rep.first_name or '')} (@{html.escape(rep.username or 'нет')})\n"
                        f"❓ Причина: {html.escape(reason)}\n"
                        f"{dialog_link}"
                        f"Просьба позвать {admin_mention}!"
                    )
                    try:
                        await bot.send_message(chat_id=SUPERGROUP_CHAT_ID, text=notif_text, message_thread_id=esc_topic_id, parse_mode="HTML")
                    except Exception as e:
                        logger.error(f"[Эскалация] Не удалось отправить уведомление: {e}")
                
                return

        if not user_text:
            return

        # Check if we are waiting for human
        admin_topic = await session.scalar(select(AdminTopic).where(AdminTopic.telegram_id == rep.telegram_id))
        if admin_topic and admin_topic.status == "waiting_human":
            await message.answer("Менеджер уже в пути! Это может занять от пары минут до нескольких часов. Пожалуйста, подождите.")
            # Map bot's message to topic too
            await bot.send_message(chat_id=SUPERGROUP_CHAT_ID, text=f"⏳ <b>Бот (авто-ответ):</b>\nМенеджер уже в пути...", message_thread_id=admin_topic.topic_id, parse_mode="HTML")
            return
            
        if admin_topic and admin_topic.status == "human_mode":
            # The bot stays completely silent, while the message was already forwarded to the admin group above
            return

        async with user_llm_locks[rep.telegram_id]:
            # Refetch the last 40 messages to form complete history inside the lock
            history_query = await session.execute(
                select(MessageHistory)
                .where(MessageHistory.telegram_id == rep.telegram_id)
                .order_by(MessageHistory.created_at.desc())
                .limit(40)
            )
            history_records = list(reversed(history_query.scalars().all()))

            # Format for Gemini: Group consecutive messages of the same role
            gemini_history = []
            for r in history_records:
                if r.role == "assistant":
                    role = "model"
                    content = r.content
                elif r.role == "admin":
                    role = "user" # Admin replies act as user inputs to the model
                    content = f"[Менеджер]: {r.content}"
                else: # user
                    role = "user"
                    content = r.content
                    
                if gemini_history and gemini_history[-1]["role"] == role:
                    gemini_history[-1]["parts"][0] += f"\n{content}"
                else:
                    gemini_history.append({"role": role, "parts": [content]})
            
            if not gemini_history:
                return # Should not happen because we just saved a message
                
            # The last element should be our combined user message
            last_turn = gemini_history.pop()
            if last_turn["role"] == "model":
                gemini_history.append(last_turn)
                combined_user_text = user_text # fallback
            else:
                combined_user_text = last_turn["parts"][0]

            user_context = {
                "telegram_id": rep.telegram_id,
                "first_name": rep.first_name,
                "username": rep.username,
            }

            try:
                reply_text, handover_reason = await gemini_client.generate_response(gemini_history, combined_user_text, user_context=user_context)
                # Handle Handover
                if handover_reason is not None:
                    # Clean up the text for client
                    clean_reply = "Спрашиваю менеджера, минутку... Специалист подключится к диалогу в течение нескольких часов."
                    
                    # Update status in DB (if topic exists)
                    if admin_topic:
                        admin_topic.status = "waiting_human"
                        await session.commit()
                    
                    # Notify Admin in Escalation Topic — always, regardless of admin_topic
                    logger.info(f"[Эскалация] Пользователь {rep.telegram_id}, причина: {handover_reason}")
                    esc_topic_id = await get_or_create_escalation_topic(bot)
                    logger.info(f"[Эскалация] Тема эскалаций: {esc_topic_id}")
                    if esc_topic_id and SUPERGROUP_CHAT_ID:
                        admin_mention = f'<a href="tg://user?id={ADMIN_TELEGRAM_ID}">администратора</a>' if ADMIN_TELEGRAM_ID else "администратора"
                        dialog_link = (
                            f'📂 <a href="https://t.me/c/{str(SUPERGROUP_CHAT_ID)[4:]}/{admin_topic.topic_id}">Перейти к диалогу</a>\n\n'
                            if admin_topic else ""
                        )
                        notif_text = (
                            f"🔔 <b>ВНИМАНИЕ: ЭСКАЛАЦИЯ</b>\n\n"
                            f"👤 Клиент: {html.escape(rep.first_name or '')} (@{html.escape(rep.username or 'нет')})\n"
                            f"❓ Причина: {html.escape(handover_reason)}\n"
                            f"{dialog_link}"
                            f"Просьба позвать {admin_mention}!"
                        )
                        try:
                            await bot.send_message(chat_id=SUPERGROUP_CHAT_ID, text=notif_text, message_thread_id=esc_topic_id, parse_mode="HTML")
                            logger.info(f"[Эскалация] Уведомление отправлено в тему {esc_topic_id}")
                        except Exception as e:
                            logger.error(f"[Эскалация] Не удалось отправить уведомление: {e}")
                    else:
                        logger.error(f"[Эскалация] Невозможно отправить: esc_topic_id={esc_topic_id}, SUPERGROUP_CHAT_ID={SUPERGROUP_CHAT_ID}")
                    
                    reply_text = clean_reply
    
                # Save assistant response
                bot_msg = MessageHistory(telegram_id=rep.telegram_id, role="assistant", content=reply_text)
                session.add(bot_msg)
                await session.commit()
                
                await message.answer(reply_text)
                
                if admin_topic:
                     # Send bot's reply to the admin topic too
                     await bot.send_message(chat_id=SUPERGROUP_CHAT_ID, text=f"🤖 <b>Бот:</b>\n{html.escape(reply_text)}", message_thread_id=admin_topic.topic_id, parse_mode="HTML")
            
            except Exception as e:
                logger.error(f"Ошибка LLM для пользователя {rep.telegram_id}: {e}")
                error_text = "Извините, произошла техническая ошибка. Я скоро вернусь!"
                await message.answer(error_text)
                if admin_topic:
                    await bot.send_message(chat_id=SUPERGROUP_CHAT_ID, text=f"⚠️ <b>Ошибка LLM:</b>\n{html.escape(str(e))}", message_thread_id=admin_topic.topic_id, parse_mode="HTML")

@router.message(F.chat.type.in_({"group", "supergroup"}))
async def admin_topic_message(message: Message, bot: Bot):
    if not message.message_thread_id or str(message.chat.id) != str(SUPERGROUP_CHAT_ID):
        return
        
    async with async_session_maker() as session:
        # Find which representative this topic belongs to
        topic = await session.scalar(select(AdminTopic).where(AdminTopic.topic_id == message.message_thread_id))
        if not topic:
            return
            
        if message.text and message.text.strip().lower().startswith("/close"):
            if topic.status != "active":
                topic.status = "active"
                await session.commit()
                await message.reply("✅ Диалог закрыт. Бот снова общается с клиентом.")
                try:
                    await bot.send_message(chat_id=topic.telegram_id, text="Администратор завершил диалог. Я снова готов вам помочь!")
                except Exception as e:
                    logger.warning(f"Не удалось уведомить клиента о сбросе диалога: {e}")
            return
            
        # Send reply back to the user
        try:
            await bot.copy_message(chat_id=topic.telegram_id, from_chat_id=message.chat.id, message_id=message.message_id)
            
            admin_msg = MessageHistory(telegram_id=topic.telegram_id, role="admin", content=message.text or "(Ответ менеджера)")
            session.add(admin_msg)
            
            # Change status to human_mode when admin replies (so the bot shuts up)
            if topic.status != "human_mode":
                topic.status = "human_mode"
                await message.reply("✅ Вы перехватили диалог! Теперь бот временно отключен для этого клиента и будет молчать. Чтобы вернуть клиента боту, введите сюда команду /close.")
                
            await session.commit()
        except Exception as e:
            await message.reply(f"Не удалось отправить сообщение клиенту: {e}")
