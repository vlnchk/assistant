from typing import Optional
from services.google_sheets import sheets_service

def get_free_slots(month: str = "") -> str:
    """
    Returns available dates for advertisement slots.
    Booking is only available for the current month and the next two months.
    
    Args:
        month: The month to check availability for, e.g., 'апрель'. Do not include the year. Optional, leave blank to check all free slots.
    """
    return sheets_service.get_free_slots(month)

def create_document_task(doc_type: str, telegram_id: str, contact: str = "", link: str = "") -> str:
    """
    Creates a task to prepare accounting documents for the client.

    Args:
        doc_type: Type of document requested (e.g. счет, договор, акт).
        telegram_id: The Telegram ID of the user. Take it from the ТЕКУЩИЙ ПОЛЬЗОВАТЕЛЬ section.
        contact: Representative contact/username. Take it from the ТЕКУЩИЙ ПОЛЬЗОВАТЕЛЬ section.
        link: Direct link to the Telegram topic (if available).
    """
    return sheets_service.create_document_task("", "", doc_type, contact, link, telegram_id=telegram_id)

def handover_to_admin(reason: str) -> str:
    """
    Suspends automatic bot responses and notifies a human administrator to take over the conversation.
    Use this when the user explicitly asks for a human, or is ready to pay, or asks a completely unknown question.
    
    Args:
        reason: The reason for calling the administrator (e.g., 'оплата и реквизиты', 'прямой запрос').
    """
    return f"__HANDOVER__:{reason}"

def book_slot(date: str, client: str, ad_topic: str, telegram_id: str, comments: str = "", link: str = "", publish_time: str = "") -> str:
    """
    Books a free slot for a specific date.
    
    Args:
        date: The date to book. Use the exact date string returned by get_free_slots (e.g. '4/1/2026').
        client: The name of the client or company booking the slot.
        ad_topic: What the client plans to advertise (e.g. 'интернет-магазин одежды', 'кулинарный блог'). REQUIRED — ask the client before booking.
        publish_time: Preferred publication time, if specified by the client (e.g. 'утром', '12:00'). Optional.
        comments: Any other comments or preferences from the client not covered by ad_topic or publish_time.
        telegram_id: The Telegram ID of the user booking the slot. Take it from the ТЕКУЩИЙ ПОЛЬЗОВАТЕЛЬ section.
        link: Direct link to the Telegram topic.
    """
    return sheets_service.book_slot(date, client, comments, link, telegram_id=telegram_id, ad_topic=ad_topic, publish_time=publish_time)

def get_client_bookings(telegram_id: str) -> str:
    """
    Returns all booked slots for the current client.
    Always use the telegram_id from the ТЕКУЩИЙ ПОЛЬЗОВАТЕЛЬ section.

    Args:
        telegram_id: The Telegram ID of the user. Take it from the ТЕКУЩИЙ ПОЛЬЗОВАТЕЛЬ section.
    """
    return sheets_service.get_client_bookings(telegram_id=telegram_id)

# List of tools to pass to Gemini
bot_tools = [get_free_slots, book_slot, get_client_bookings, create_document_task, handover_to_admin]
