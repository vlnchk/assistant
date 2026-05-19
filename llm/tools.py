"""
Tools exposed to the Gemini dispatcher.

All client-facing text returned by these tools is fetched verbatim from
``bot.replies``. The LLM is a router: it picks the right tool, never writes
text. Docstrings are in English on purpose — they are routing metadata for
Gemini and never reach the client.

Convention: every tool returns ``str``. The handler treats the return value
as the final reply to the client (with two exceptions: ``handover_to_admin``
returns a ``__HANDOVER__:<reason>`` sentinel; ``get_free_slots`` /
``get_client_bookings`` return already-formatted text).
"""

from bot import replies
from services.google_sheets import sheets_service


# ---------------------------------------------------------------------------
# Greeting and small talk
# ---------------------------------------------------------------------------

def send_greeting() -> str:
    """
    Send the standard greeting.

    Use on the very first message from a client, or when the client greets
    the bot ("привет", "здравствуйте", "hi", "hello"). Do not use as a
    fallback for unknown intents — use handover_to_admin for that.
    """
    return replies.GREETING


def reply_bot_nature() -> str:
    """
    Answer questions about whether the bot is a human, an AI, has feelings,
    consciousness, emotions, name, age, gender, etc. — anything probing the
    bot's nature or identity.
    """
    return replies.BOT_NATURE


def reply_offtopic() -> str:
    """
    Polite redirect for any small talk or off-topic question that is not
    covered by a more specific tool: weather, jokes, news, philosophy,
    personal opinions, requests to play games, etc. Use only when no FAQ
    or action tool fits.
    """
    return replies.OFFTOPIC


# ---------------------------------------------------------------------------
# FAQ tools — fixed canonical answers
# ---------------------------------------------------------------------------

def faq_free_posting() -> str:
    """
    Explain that posting a regular job vacancy is free and point the client
    to the submission form. Use when the client wants to publish a vacancy
    via the standard form, or asks "how do I post a vacancy?" / "сколько
    стоит разместить вакансию".
    """
    return replies.FAQ_FREE_POSTING


def faq_paid_post() -> str:
    """
    Explain pricing for paid promotional posts (15 000 ₽ per post, 5th post
    free when 4 are paid at once, 24h top placement). Use when the client
    asks about ad pricing, custom-format vacancy posting, or wants to buy
    promotion in the channel.
    """
    return replies.FAQ_PAID_POST


def faq_stats() -> str:
    """
    Provide channel statistics: average reach, ERR, geography of audience.
    Use when the client asks about views, reach, audience size,
    engagement, or geography of subscribers.
    """
    return replies.FAQ_STATS


def faq_ord() -> str:
    """
    Explain advertising marking (ОРД / маркировка рекламы): client can do
    it themselves for free, or we do it turnkey for 2 000 ₽. Use when the
    client asks about ad labelling, ОРД, ЕРИР, or marking requirements.
    """
    return replies.FAQ_ORD


def faq_docs() -> str:
    """
    Explain the document workflow: remote-only (scans or EDI), we prepare
    invoice / contract / act, can work with the client's own contract
    template. Use when the client asks about paperwork, contract format,
    EDI, or how documents are exchanged.
    """
    return replies.FAQ_DOCS


def ask_ad_topic() -> str:
    """
    Politely ask the client what they plan to advertise. Use BEFORE calling
    book_slot when the ad_topic is not yet known — book_slot requires
    ad_topic as a mandatory argument.
    """
    return replies.BOOK_NEED_AD_TOPIC


# ---------------------------------------------------------------------------
# Action tools — booking, documents, escalation
# ---------------------------------------------------------------------------

def check_dates_availability(dates: list[str]) -> str:
    """
    Check whether the SPECIFIC dates named by the client are free.

    Use when the client asks about one or more concrete dates, e.g.:
      - "А 26 мая свободно?"
      - "Свободны ли 5 и 7 июня?"
      - "Можно на 1, 2, 3 числа июля?"

    Do NOT use for general "какие свободные даты" — that is get_free_slots.

    Args:
        dates: List of date strings in any format the client used
            ("5 мая", "26.05", "5/26", "5 и 7 июня" should be split into
            ["5 июня", "7 июня"]). Always include the month — never a
            bare day number.
    """
    return sheets_service.check_dates_availability(dates)


def get_free_slots(month: str = "") -> str:
    """
    Return available advertisement dates.

    Behaviour:
      - Empty ``month`` → the 5 nearest free dates, with a hint to specify
        a month for more. Use this whenever the client asks about free
        dates in general ("какие свободные даты?", "когда можно", etc.).
      - Non-empty ``month`` → ALL free dates in that month. Use this only
        when the client has explicitly named a month.

    Booking is hard-capped to the current month and the next two months;
    anything further is filtered out server-side and won't appear.

    Args:
        month: Russian month name (e.g. "апрель", "июнь"), or empty
            string for the default 5-nearest behaviour. Do NOT pass year.
    """
    return sheets_service.get_free_slots(month)


def book_slot(
    date: str,
    client: str,
    ad_topic: str,
    telegram_id: str,
    comments: str = "",
    link: str = "",
    publish_time: str = "",
) -> str:
    """
    Book a free slot for a specific date.

    IMPORTANT: ad_topic is mandatory — call ask_ad_topic first if the
    client has not specified what they want to advertise. If ad_topic
    falls into a forbidden category (casino, betting, crypto, politics,
    grey schemes, dubious supplements, info-gypsies) — DO NOT call this
    tool; call handover_to_admin instead.

    Args:
        date: Exact date string as returned by get_free_slots (e.g. "5/5/2026").
        client: Client name or company.
        ad_topic: What the client plans to advertise. REQUIRED.
        telegram_id: Always take from the ТЕКУЩИЙ ПОЛЬЗОВАТЕЛЬ section.
        comments: Other client preferences.
        link: Direct link to the Telegram topic.
        publish_time: Preferred publication time (e.g. "утром", "12:00").
    """
    return sheets_service.book_slot(
        date,
        client,
        comments,
        link,
        telegram_id=telegram_id,
        ad_topic=ad_topic,
        publish_time=publish_time,
    )


def get_client_bookings(telegram_id: str) -> str:
    """
    Return all bookings for the current client.

    Args:
        telegram_id: Always take from the ТЕКУЩИЙ ПОЛЬЗОВАТЕЛЬ section.
            The server will overwrite this with the real ID anyway, so
            the LLM cannot be tricked into pulling someone else's data.
    """
    return sheets_service.get_client_bookings(telegram_id=telegram_id)


def create_document_task(
    doc_type: str,
    telegram_id: str,
    contact: str = "",
    link: str = "",
) -> str:
    """
    Create a task to prepare accounting documents (счёт, договор, акт).

    IMPORTANT: a request for documents is NOT a request for payment —
    use this tool, not handover_to_admin, when the client asks for an
    invoice / contract / act.

    Args:
        doc_type: Type of document (счёт, договор, акт).
        telegram_id: Always take from the ТЕКУЩИЙ ПОЛЬЗОВАТЕЛЬ section.
        contact: Client's contact / username from ТЕКУЩИЙ ПОЛЬЗОВАТЕЛЬ.
        link: Direct link to the Telegram topic.
    """
    return sheets_service.create_document_task(
        "", "", doc_type, contact, link, telegram_id=telegram_id
    )


def handover_to_admin(reason: str) -> str:
    """
    Stop the bot and notify a human manager to take over the conversation.

    Use when:
      - the client explicitly asks for a human;
      - the client wants to actually pay (NOT just request documents);
      - the client asks an off-topic question that does NOT fit
        reply_bot_nature or reply_offtopic and clearly needs a human;
      - the ad topic is forbidden (casino, betting, crypto, politics,
        grey schemes, dubious supplements, info-gypsies);
      - any other situation where no other tool fits.

    Args:
        reason: Short reason in Russian (e.g. "оплата и реквизиты",
            "запрещённая тематика рекламы", "прямой запрос менеджера").
    """
    return f"__HANDOVER__:{reason}"


# ---------------------------------------------------------------------------
# Registry — order matters for the system prompt readability
# ---------------------------------------------------------------------------

# Tools that produce a final client-facing reply. The dispatcher loop in
# gemini_client exits as soon as one of these returns — no extra LLM
# round-trip is needed because the text is already canonical.
TERMINAL_TOOLS = {
    "send_greeting",
    "reply_bot_nature",
    "reply_offtopic",
    "faq_free_posting",
    "faq_paid_post",
    "faq_stats",
    "faq_ord",
    "faq_docs",
    "ask_ad_topic",
    "book_slot",
    "get_free_slots",
    "check_dates_availability",
    "get_client_bookings",
    "create_document_task",
}

bot_tools = [
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
]
