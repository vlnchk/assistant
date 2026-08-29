"""
Tools exposed to the Gemini dispatcher.

All client-facing text returned by these tools is fetched verbatim from
``bot.replies``. The LLM is a router: it picks the right tool, never writes
text. Docstrings are in English on purpose — they are routing metadata for
Gemini and never reach the client.

Convention: every tool returns ``str``. The handler treats the return value
as the final reply to the client, except for internal structured sentinels
used for handover and non-blocking admin notifications.
"""

import json
import re

from bot import replies
from services.google_sheets import sheets_service


INFORMATION_REPLIES = {
    "free_posting": replies.FAQ_FREE_POSTING,
    "vacancy_options": replies.FAQ_VACANCY_OPTIONS,
    "free_advertising": replies.FAQ_FREE_ADVERTISING,
    "paid_post": replies.FAQ_PAID_POST,
    "ad_formats": replies.FAQ_AD_FORMATS,
    "stats": replies.FAQ_STATS,
    "performance": replies.FAQ_PERFORMANCE,
    "ord": replies.FAQ_ORD,
    "docs": replies.FAQ_DOCS,
    "payment_legal": replies.FAQ_PAYMENT_LEGAL,
    "partnerships": replies.FAQ_PARTNERSHIPS,
    "jobseeker": replies.FAQ_JOBSEEKER,
    "moderation": replies.FAQ_MODERATION,
    "publication_time": replies.FAQ_PUBLICATION_TIME,
}


def _handover_with_reply(reason: str, client_reply: str) -> str:
    """Encode a categorized handover without exposing arbitrary LLM text."""
    payload = {"reason": reason, "client_reply": client_reply}
    return "__HANDOVER_JSON__:" + json.dumps(payload, ensure_ascii=False)


def _admin_notify_with_reply(notification: str, client_reply: str) -> str:
    """Encode a non-blocking admin notification plus the client reply."""
    payload = {"notification": notification, "client_reply": client_reply}
    return "__ADMIN_NOTIFY_JSON__:" + json.dumps(payload, ensure_ascii=False)


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
    via the standard form, or asks only "how do I post a vacancy?". For price,
    own-format or free-vs-paid questions use answer_information with
    vacancy_options instead. Never use for a program, course, event, school
    or educational project merely because participation is free.
    """
    return replies.FAQ_FREE_POSTING


def faq_paid_post() -> str:
    """
    Explain pricing for paid promotional posts (15 000 ₽ per post, 5th post
    free when 4 are paid at once, 24h top placement). Use when the client
    asks about ad pricing or wants to buy promotion in the channel. For a
    vacancy where the client compares free and custom-format placement, use
    answer_information with vacancy_options. Announcements about programs,
    courses, events and educational projects are promotional posts even when
    they are free for students or include an internship.
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


def answer_information(
    topics: list[str],
    month: str = "",
    dates: list[str] | None = None,
) -> str:
    """
    Build one deterministic answer for one or several informational intents.

    Use this tool whenever a single client message asks two or more questions.
    It is also the only tool for the new FAQ topics listed below. Every block
    comes from bot.replies; never invent or paraphrase an answer.

    Allowed topics:
      - free_posting: standard free vacancy submission form;
      - vacancy_options: compare free and paid custom-format vacancy posting
        (from 3 000 ₽, optional direct contacts, image allowed);
      - free_advertising: clarify that ads are paid while vacancies may be free;
      - paid_post: non-vacancy advertising price, 4+1 offer and 24-hour top;
      - ad_formats: text/photo/video/native format, pinning and editing;
      - stats: reach, ERR and audience geography;
      - performance: clicks, CTR, conversion and CPA availability;
      - ord: advertisement labelling;
      - docs: general document workflow;
      - payment_legal: self-employed/IP, VAT, payment and signing details;
      - partnerships: CPA, affiliate and agency cooperation (not mutual PR);
      - jobseeker: finding a job, viewing vacancies or posting a CV;
      - moderation: review timing and why a free vacancy may not appear;
      - publication_time: exact publication-time policy;
      - free_slots: available advertisement dates.

    Calendar arguments:
      - dates with one or more concrete dates checks those exact dates;
      - month returns all free dates in that month;
      - free_slots without dates/month returns the five nearest dates.

    Args:
        topics: One or more allowed topic keys, in the client's question order.
        month: Russian month name only when explicitly named by the client.
        dates: Concrete dates only when explicitly named by the client.
    """
    blocks: list[str] = []
    seen: set[str] = set()
    unknown: list[str] = []

    for topic in topics or []:
        normalized = str(topic).strip().lower()
        if normalized in seen:
            continue
        seen.add(normalized)
        block = INFORMATION_REPLIES.get(normalized)
        if block:
            blocks.append(block)
        elif normalized != "free_slots":
            unknown.append(normalized)

    if unknown:
        return "__HANDOVER__:неизвестная информационная тема"

    wants_slots = "free_slots" in seen or bool(month) or bool(dates)
    if wants_slots:
        if dates:
            blocks.append(sheets_service.check_dates_availability(dates))
        else:
            blocks.append(sheets_service.get_free_slots(month))

    if not blocks:
        return "__HANDOVER__:неизвестная информационная тема"

    # The same canonical block can be selected through aliases or overlapping
    # topics. Remove exact duplicates while preserving the requested order.
    return "\n\n".join(dict.fromkeys(blocks))


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

    The booking window (current month + 2 next) is enforced server-side:
    out-of-window dates get a canonical refusal reply automatically.

    Args:
        date: Date as the client named it or as shown by get_free_slots
            (e.g. "5 мая", "26.08"). Always include the month, never the year.
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
    company: str = "",
    inn: str = "",
    requisites: str = "",
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
        company: Company name or the customer's full legal name.
        inn: Russian taxpayer number, exactly 10 or 12 digits.
        requisites: Other payment details supplied by the client. Never invent.
        contact: Client's contact / username from ТЕКУЩИЙ ПОЛЬЗОВАТЕЛЬ.
        link: Direct link to the Telegram topic.
    """
    company = str(company or "").strip()
    inn = re.sub(r"\D", "", str(inn or ""))
    if not company and not inn:
        return replies.DOC_NEED_DETAILS
    if not company:
        return replies.DOC_NEED_COMPANY
    if not inn:
        return replies.DOC_NEED_INN
    if len(inn) not in {10, 12}:
        return replies.DOC_BAD_INN
    client_reply = sheets_service.create_document_task(
        company,
        inn,
        doc_type,
        contact,
        link,
        telegram_id=telegram_id,
        requisites=requisites,
    )
    expected_reply = replies.DOC_CREATED.format(doc_type=doc_type)
    if client_reply != expected_reply:
        return client_reply

    notification = (
        f"Подготовить документ: {doc_type}; компания: {company}; ИНН: {inn}"
    )
    return _admin_notify_with_reply(notification, client_reply)


def request_publication_support(
    issue: str,
    vacancy_name: str = "",
    publication_link: str = "",
    details: str = "",
) -> str:
    """
    Collect a concrete publication problem and hand it to a manager.

    Use for an already submitted or published vacancy/ad. Supported issue
    values: status, edit, delete, expedite, convert_to_paid, broken_form,
    approval_status. This tool never changes or deletes a publication itself.

    Args:
        issue: One supported issue value.
        vacancy_name: Vacancy or campaign name if the client supplied it.
        publication_link: Telegram post URL if the client supplied it.
        details: Short factual details from the client; never invent details.
    """
    issue = str(issue or "").strip().lower()
    supported = {
        "status": "проверить статус заявки",
        "edit": "исправить публикацию",
        "delete": "удалить публикацию",
        "expedite": "ускорить публикацию",
        "convert_to_paid": "перевести бесплатную заявку в платное размещение",
        "broken_form": "проверить неработающую форму",
        "approval_status": "проверить согласование рекламной тематики",
    }
    if issue not in supported:
        return "__HANDOVER__:неизвестный запрос по публикации"

    vacancy_name = str(vacancy_name or "").strip()
    publication_link = str(publication_link or "").strip()
    details = str(details or "").strip()

    if issue in {"status", "expedite", "convert_to_paid"} and not (
        vacancy_name or details
    ):
        return replies.PUBLICATION_NEED_VACANCY
    if issue in {"edit", "delete"} and not publication_link:
        return replies.PUBLICATION_NEED_LINK
    if issue == "approval_status" and not (vacancy_name or details):
        return replies.BOOK_NEED_AD_TOPIC

    reason_parts = [supported[issue]]
    if vacancy_name:
        reason_parts.append(f"название: {vacancy_name[:300]}")
    if publication_link:
        reason_parts.append(f"ссылка: {publication_link[:500]}")
    if details:
        reason_parts.append(f"детали: {details[:700]}")
    reason = "; ".join(reason_parts)
    client_reply = (
        replies.PUBLICATION_FORM_BROKEN
        if issue == "broken_form"
        else replies.PUBLICATION_SUPPORT_SENT
    )
    return _handover_with_reply(reason, client_reply)


def request_mutual_pr_support() -> str:
    """
    Hand a mutual-promotion proposal to a manager.

    Always use this tool when the client mentions mutual PR, reciprocal
    promotion, "взаимопиар", "взаимный пиар" or the abbreviation "ВП".
    This is a real handover: the manager is notified and the bot pauses.
    Do not use the informational partnerships topic for mutual PR.
    """
    return _handover_with_reply(
        "предложение по взаимопиару (ВП)",
        replies.MUTUAL_PR_HANDOVER,
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
    "answer_information",
    "ask_ad_topic",
    "book_slot",
    "get_free_slots",
    "check_dates_availability",
    "get_client_bookings",
    "create_document_task",
    "request_publication_support",
    "request_mutual_pr_support",
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
    answer_information,
    ask_ad_topic,
    get_free_slots,
    check_dates_availability,
    book_slot,
    get_client_bookings,
    create_document_task,
    request_publication_support,
    request_mutual_pr_support,
    handover_to_admin,
]
