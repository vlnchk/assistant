import datetime
import os
from typing import Optional

import gspread
from dotenv import load_dotenv

from bot import replies
from logger import logger

load_dotenv()

# We expect credentials in "credentials.json" in the root directory.
CREDENTIALS_FILE = os.getenv("GOOGLE_SHEETS_CREDENTIALS_FILE", "credentials.json")
SPREADSHEET_URL = os.getenv("GOOGLE_SHEETS_DOCUMENT_URL")

# Mapping of Russian month names (and common variations) to month numbers
MONTH_NAME_TO_NUMBER = {
    "январ": 1, "феврал": 2, "март": 3, "апрел": 4,
    "май": 5, "мая": 5, "июн": 6, "июл": 7, "август": 8,
    "сентябр": 9, "октябр": 10, "ноябр": 11, "декабр": 12,
}

# Genitive forms for human-readable Russian dates ("5 мая", "12 июня").
MONTH_GENITIVE_RU = {
    1: "января", 2: "февраля", 3: "марта", 4: "апреля",
    5: "мая", 6: "июня", 7: "июля", 8: "августа",
    9: "сентября", 10: "октября", 11: "ноября", 12: "декабря",
}

# Prepositional case ("в мае", "в июне") — для подстановки в SLOTS_FOR_MONTH.
MONTH_PREPOSITIONAL_RU = {
    1: "январе", 2: "феврале", 3: "марте", 4: "апреле",
    5: "мае", 6: "июне", 7: "июле", 8: "августе",
    9: "сентябре", 10: "октябре", 11: "ноябре", 12: "декабре",
}


def _format_dates_ru(dates: list[datetime.date]) -> str:
    """Render a list of dates as "5 мая, 12 июня" (month always present)."""
    return ", ".join(f"{d.day} {MONTH_GENITIVE_RU[d.month]}" for d in dates)


def _cap_date(today: datetime.date) -> datetime.date:
    """Return the first day of the month that is 3 months after `today`.
    Booking is only allowed for the current month and the next two."""
    y, m = today.year, today.month + 3
    while m > 12:
        m -= 12
        y += 1
    return datetime.date(y, m, 1)


def _parse_month_number(month_str: str) -> Optional[int]:
    """Convert a Russian month name (any grammatical form) to a month number."""
    if not month_str:
        return None
    month_lower = month_str.strip().lower()
    for prefix, num in MONTH_NAME_TO_NUMBER.items():
        if month_lower.startswith(prefix):
            return num
    # If the user passed a plain digit
    try:
        n = int(month_lower)
        if 1 <= n <= 12:
            return n
    except ValueError:
        pass
    return None


def _parse_date_month(date_str: str) -> Optional[int]:
    """Extract the month number from a date string like '4/1/2026' or '04.01.2026'."""
    date_str = str(date_str).strip()
    for sep in ("/", ".", "-"):
        parts = date_str.split(sep)
        if len(parts) >= 2:
            try:
                return int(parts[0])
            except ValueError:
                continue
    return None


def _normalize_date_to_md(date_str: str) -> Optional[tuple]:
    """
    Parse various date formats into a (month, day) tuple for comparison.
    Supports: '4/1/2026', '4/1', '01.04', '01.04.2026', '1 апреля', etc.
    """
    date_str = str(date_str).strip()

    # Try M/D/YYYY or M/D format (Google Sheets format)
    if "/" in date_str:
        parts = date_str.split("/")
        if len(parts) >= 2:
            try:
                return (int(parts[0]), int(parts[1]))
            except ValueError:
                pass

    # Try D.M or D.M.YYYY format
    if "." in date_str:
        parts = date_str.split(".")
        if len(parts) >= 2:
            try:
                return (int(parts[1]), int(parts[0]))
            except ValueError:
                pass

    # Try D-M or D-M-YYYY format
    if "-" in date_str:
        parts = date_str.split("-")
        if len(parts) >= 2:
            try:
                return (int(parts[1]), int(parts[0]))
            except ValueError:
                pass

    # Try "D месяц" format (e.g. "1 апреля", "15 мая")
    parts = date_str.split()
    if len(parts) >= 2:
        try:
            day = int(parts[0])
            month = _parse_month_number(parts[1])
            if month:
                return (month, day)
        except ValueError:
            pass

    return None


def _parse_date_full(date_str: str) -> Optional[datetime.date]:
    """
    Parse various date formats to ``datetime.date``.

    Supported:
      - ``M/D/YYYY`` and ``M/D`` (Google Sheets default for ru-RU)
      - ``D.M.YYYY`` and ``D.M``
      - ``D-M-YYYY`` and ``D-M``
      - ``D <месяц>`` ("5 мая")

    If the year is missing, falls back to the current year — but if that
    yields a date in the past relative to today, rolls over to next year.
    """
    s = str(date_str).strip()
    if not s:
        return None
    today = datetime.date.today()

    def _make(d: int, m: int, y: Optional[int]) -> Optional[datetime.date]:
        try:
            if y is None:
                # Year missing — assume current; roll over to next year if the
                # resulting date is already in the past (user likely means
                # "5 мая" of NEXT year, not the May 5th that already passed).
                candidate = datetime.date(today.year, m, d)
                if candidate < today:
                    candidate = datetime.date(today.year + 1, m, d)
                return candidate
            return datetime.date(y, m, d)
        except (ValueError, TypeError):
            return None

    # M/D[/YYYY]  (Google Sheets format)
    if "/" in s:
        parts = s.split("/")
        try:
            if len(parts) >= 3:
                return _make(int(parts[1]), int(parts[0]), int(parts[2]))
            if len(parts) == 2:
                return _make(int(parts[1]), int(parts[0]), None)
        except (ValueError, TypeError):
            pass

    # D.M[.YYYY]
    if "." in s:
        parts = s.split(".")
        try:
            if len(parts) >= 3:
                return _make(int(parts[0]), int(parts[1]), int(parts[2]))
            if len(parts) == 2:
                return _make(int(parts[0]), int(parts[1]), None)
        except (ValueError, TypeError):
            pass

    # D-M[-YYYY]
    if "-" in s:
        parts = s.split("-")
        try:
            if len(parts) >= 3:
                return _make(int(parts[0]), int(parts[1]), int(parts[2]))
            if len(parts) == 2:
                return _make(int(parts[0]), int(parts[1]), None)
        except (ValueError, TypeError):
            pass

    # "D месяц[ YYYY]"
    parts = s.split()
    if len(parts) >= 2:
        try:
            day = int(parts[0])
            month = _parse_month_number(parts[1])
            if month:
                year = int(parts[2]) if len(parts) >= 3 else None
                return _make(day, month, year)
        except (ValueError, TypeError):
            pass

    return None


def _find_header(headers: list, prefix: str) -> Optional[str]:
    """Find a header that starts with the given prefix (case-insensitive)."""
    prefix_lower = prefix.lower()
    for h in headers:
        if h.lower().startswith(prefix_lower):
            return h
    return None


def _get_row_value(row: dict, prefix: str):
    """Get a value from a row dict where the key starts with the given prefix."""
    prefix_lower = prefix.lower()
    for key, value in row.items():
        if key.lower().startswith(prefix_lower):
            return value
    return ""


class GoogleSheetsService:
    def __init__(self):
        self.client = None
        self.spreadsheet = None
        self._connect()

    def _connect(self):
        """Connect to Google Sheets. gspread does its own retry internally,
        so we don't loop with time.sleep (that would block the event loop
        when called from inside an async context via asyncio.to_thread)."""
        try:
            self.client = gspread.service_account(filename=CREDENTIALS_FILE)
            self.spreadsheet = self.client.open_by_url(SPREADSHEET_URL)
        except Exception as e:
            self.client = None
            self.spreadsheet = None
            logger.error(f"Google Sheets: подключение не удалось: {e}")

    def get_free_slots(self, month: str = "") -> str:
        """
        Return free advertisement dates.

        - Without ``month``: only the 5 nearest dates (from today onward),
          with a hint to specify a month for more.
        - With ``month``: ALL free dates in that month, bounded by
          "current month + 2 next months" (никогда не отдаём дальше).

        Past dates and dates beyond the cap are filtered out in code,
        not just in the LLM prompt.
        """
        if not self.spreadsheet:
            self._connect()
        if not self.spreadsheet:
            return replies.SLOTS_CONNECTION_ERROR

        try:
            sheet = self.spreadsheet.worksheet("Календарь (Слоты)")
            records = sheet.get_all_records()

            target_month = _parse_month_number(month) if month else None
            if month and target_month is None:
                return replies.SLOTS_BAD_MONTH.format(month=month)

            today = datetime.date.today()
            cap = _cap_date(today)

            free_dates: list[datetime.date] = []
            for row in records:
                status = str(_get_row_value(row, "Статус слота")).strip().lower()
                if status != "свободен":
                    continue
                date_val = str(_get_row_value(row, "Дата")).strip()
                d = _parse_date_full(date_val)
                if d is None:
                    continue
                # Жёсткое окно: с сегодня до cap (текущий + 2 месяца).
                if d < today or d >= cap:
                    continue
                free_dates.append(d)

            free_dates.sort()

            if target_month is not None:
                filtered = [d for d in free_dates if d.month == target_month]
                month_label = MONTH_PREPOSITIONAL_RU[target_month]
                if not filtered:
                    return replies.SLOTS_EMPTY_MONTH.format(month=month_label)
                return replies.SLOTS_FOR_MONTH.format(
                    month=month_label,
                    list=_format_dates_ru(filtered),
                )

            nearest = free_dates[:5]
            if not nearest:
                return replies.SLOTS_EMPTY
            return replies.SLOTS_NEAREST.format(list=_format_dates_ru(nearest))
        except Exception as e:
            logger.error(f"[Sheets] get_free_slots failed: {e}")
            return replies.SLOTS_CONNECTION_ERROR

    def check_dates_availability(self, dates: list[str]) -> str:
        """
        Check whether each of the given dates is currently free in the
        booking calendar. Returns a single client-facing message.
        """
        if not self.spreadsheet:
            self._connect()
        if not self.spreadsheet:
            return replies.SLOTS_CONNECTION_ERROR

        if not dates:
            return replies.SLOTS_EMPTY

        try:
            sheet = self.spreadsheet.worksheet("Календарь (Слоты)")
            records = sheet.get_all_records()

            today = datetime.date.today()
            cap = _cap_date(today)

            # Карта дата → статус из таблицы (только в окне «текущий + 2 мес»).
            sheet_dates: dict[datetime.date, str] = {}
            for row in records:
                date_val = str(_get_row_value(row, "Дата")).strip()
                d = _parse_date_full(date_val)
                if d is None or d < today or d >= cap:
                    continue
                status = str(_get_row_value(row, "Статус слота")).strip().lower()
                sheet_dates[d] = status

            free: list[datetime.date] = []
            busy: list[datetime.date] = []
            unknown: list[str] = []  # за окном или не парсится

            for raw in dates:
                d = _parse_date_full(raw)
                if d is None or d < today or d >= cap:
                    unknown.append(str(raw).strip())
                    continue
                status = sheet_dates.get(d)
                if status == "свободен":
                    free.append(d)
                else:
                    # «забронирован», отсутствует в таблице — считаем занятым.
                    busy.append(d)

            free.sort()
            busy.sort()

            if free and not busy and not unknown:
                return replies.SLOTS_CHECK_ALL_FREE.format(free=_format_dates_ru(free))
            if busy and not free and not unknown:
                return replies.SLOTS_CHECK_ALL_BUSY.format(busy=_format_dates_ru(busy))
            if free and busy:
                return replies.SLOTS_CHECK_FREE.format(
                    free=_format_dates_ru(free),
                    busy_line=replies.SLOTS_CHECK_BUSY.format(
                        busy=_format_dates_ru(busy)
                    ),
                )
            if free:
                return replies.SLOTS_CHECK_ALL_FREE.format(free=_format_dates_ru(free))
            if busy:
                return replies.SLOTS_CHECK_ALL_BUSY.format(busy=_format_dates_ru(busy))
            # Только unknown — даты за окном бронирования.
            return replies.SLOTS_EMPTY
        except Exception as e:
            logger.error(f"[Sheets] check_dates_availability failed: {e}")
            return replies.SLOTS_CONNECTION_ERROR

    def book_slot(
        self,
        date: str,
        client: str,
        comments: str,
        link: str = "",
        telegram_id: str = "",
        ad_topic: str = "",
        publish_time: str = "",
    ) -> str:
        """
        Books a free slot for a specific date.
        """
        if not self.spreadsheet:
            self._connect()
        if not self.spreadsheet:
            return replies.SLOTS_CONNECTION_ERROR

        try:
            sheet = self.spreadsheet.worksheet("Календарь (Слоты)")
            records = sheet.get_all_records()
            headers = sheet.row_values(1)

            # Ensure optional columns exist
            def _ensure_column(header_name: str):
                if not _find_header(headers, header_name):
                    next_col = len(headers) + 1
                    sheet.update_cell(1, next_col, header_name)
                    headers.append(header_name)

            if telegram_id:
                _ensure_column("Telegram ID")
            if ad_topic:
                _ensure_column("Тематика рекламы")
            if publish_time:
                _ensure_column("Время публикации")

            requested = _normalize_date_to_md(date)
            for i, row in enumerate(records):
                status = str(_get_row_value(row, "Статус слота")).strip().lower()
                row_date = str(_get_row_value(row, "Дата")).strip()

                row_md = _normalize_date_to_md(row_date)
                if status == "свободен" and requested and row_md == requested:
                    row_index = i + 2  # +2: row 1 is header, enumerate is 0-indexed

                    updates = []

                    def _add_update(prefix: str, value: str):
                        h = _find_header(headers, prefix)
                        if h:
                            col_idx = headers.index(h) + 1
                            updates.append({
                                'range': gspread.utils.rowcol_to_a1(row_index, col_idx),
                                'values': [[value]],
                            })

                    _add_update("Статус слота", "Забронирован")
                    _add_update("Клиент", client)
                    _add_update("Ссылка на диалог", link)
                    _add_update("Комментари", comments)
                    if telegram_id:
                        _add_update("Telegram ID", str(telegram_id))
                    if ad_topic:
                        _add_update("Тематика рекламы", ad_topic)
                    if publish_time:
                        _add_update("Время публикации", publish_time)

                    if not updates:
                        return replies.BOOK_ERROR.format(
                            error="не найдены нужные колонки в таблице"
                        )
                    sheet.batch_update(updates)
                    return replies.BOOK_SUCCESS.format(date=date)

            return replies.BOOK_NOT_FOUND.format(date=date)
        except Exception as e:
            logger.error(f"[Sheets] book_slot failed: {e}")
            return replies.BOOK_ERROR.format(error=str(e))

    def get_client_bookings(self, telegram_id: str = "") -> str:
        """
        Returns all booked slots for a given client.
        Searches strictly by telegram_id only.
        """
        if not self.spreadsheet:
            self._connect()
        if not self.spreadsheet:
            return replies.SLOTS_CONNECTION_ERROR

        if not telegram_id:
            # Должно быть невозможно — серверная подмена telegram_id всегда
            # проставляет реальный ID. Но на всякий случай не утечём ничего.
            return replies.BOOKINGS_EMPTY

        try:
            sheet = self.spreadsheet.worksheet("Календарь (Слоты)")
            records = sheet.get_all_records()

            tid_str = str(telegram_id).strip()
            bookings = []
            for row in records:
                status = str(_get_row_value(row, "Статус слота")).strip().lower()
                if status != "забронирован":
                    continue

                row_tid = str(_get_row_value(row, "Telegram ID")).strip()
                if row_tid != tid_str:
                    continue

                date_val = str(_get_row_value(row, "Дата")).strip()
                amount = _get_row_value(row, "Сумма")
                duration = _get_row_value(row, "Срок")
                ad_topic = _get_row_value(row, "Тематика рекламы")
                publish_time = _get_row_value(row, "Время публикации")
                comments = _get_row_value(row, "Комментари")
                row_client_display = str(_get_row_value(row, "Клиент")).strip()

                info = date_val
                if row_client_display:
                    info += f" — {row_client_display}"
                if amount:
                    info += f" — {amount} руб."
                if duration:
                    info += f" ({duration})"
                if ad_topic:
                    info += f" | Тематика: {ad_topic}"
                if publish_time:
                    info += f" | Время: {publish_time}"
                if comments:
                    info += f" | {comments}"
                bookings.append(info)

            if not bookings:
                return replies.BOOKINGS_EMPTY

            return replies.BOOKINGS_HEADER.format(list="\n".join(bookings))
        except Exception as e:
            logger.error(f"[Sheets] get_client_bookings failed: {e}")
            return replies.SLOTS_CONNECTION_ERROR

    def create_document_task(
        self,
        company: str,
        inn: str,
        doc_type: str,
        contact: str,
        link: str,
        telegram_id: str = "",
    ) -> str:
        """
        Writes a new task to the 'Задачи (Документы)' sheet.
        """
        if not self.spreadsheet:
            self._connect()
        if not self.spreadsheet:
            return replies.SLOTS_CONNECTION_ERROR

        try:
            from datetime import datetime
            now_str = datetime.now().strftime("%d.%m %H:%M")
            sheet = self.spreadsheet.worksheet("Задачи (Документы)")

            # | Дата и Время | Статус | Тип документа | Название Клиента | ИНН | Telegram ID | Контактное лицо | Ссылка на диалог |
            new_row = [
                now_str,
                "В очереди",
                doc_type,
                company,
                inn,
                str(telegram_id) if telegram_id else "",
                contact,
                link,
            ]
            sheet.append_row(new_row, table_range="A1")
            return replies.DOC_CREATED.format(doc_type=doc_type)
        except Exception as e:
            logger.error(f"[Sheets] create_document_task failed: {e}")
            return replies.DOC_ERROR.format(error=str(e))


sheets_service = GoogleSheetsService()
