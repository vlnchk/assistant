import os
import time
from typing import Optional
import gspread
from dotenv import load_dotenv
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

    def _connect(self, retries: int = 3, delay: float = 2.0):
        for attempt in range(1, retries + 1):
            try:
                self.client = gspread.service_account(filename=CREDENTIALS_FILE)
                self.spreadsheet = self.client.open_by_url(SPREADSHEET_URL)
                return
            except Exception as e:
                logger.warning(f"Google Sheets: попытка {attempt}/{retries} не удалась: {e}")
                if attempt < retries:
                    time.sleep(delay)
        self.client = None
        self.spreadsheet = None
        logger.error("Google Sheets: все попытки подключения исчерпаны")

    def get_free_slots(self, month: str = None) -> str:
        """
        Reads the 'Календарь (Слоты)' sheet and returns free slots as text.
        """
        if not self.spreadsheet:
            self._connect()
        if not self.spreadsheet:
            return "Не удалось подключиться к Google Таблицам."

        try:
            sheet = self.spreadsheet.worksheet("Календарь (Слоты)")
            records = sheet.get_all_records()

            target_month = _parse_month_number(month) if month else None
            if month and target_month is None:
                return f"Не удалось распознать месяц: «{month}». Укажите месяц по-русски (например, «апрель») или числом (1–12)."

            free_slots = []
            for row in records:
                status = str(_get_row_value(row, "Статус слота")).strip().lower()
                if status != "свободен":
                    continue

                date_val = str(_get_row_value(row, "Дата")).strip()
                amount = _get_row_value(row, "Сумма")
                duration = _get_row_value(row, "Срок")

                # Filter by month if requested
                if target_month is not None:
                    row_month = _parse_date_month(date_val)
                    if row_month != target_month:
                        continue

                slot_info = date_val
                if amount:
                    slot_info += f" — {amount} руб."
                if duration:
                    slot_info += f" ({duration})"
                free_slots.append(slot_info)

            if not free_slots:
                return "Нет свободных слотов."

            return "Свободные даты:\n" + "\n".join(free_slots)
        except Exception as e:
            return f"Ошибка при чтении календаря: {e}"

    def book_slot(self, date: str, client: str, comments: str, link: str = "", telegram_id: str = "", ad_topic: str = "", publish_time: str = "") -> str:
        """
        Books a free slot for a specific date.
        """
        if not self.spreadsheet:
            self._connect()
        if not self.spreadsheet:
            return "Не удалось подключиться к Google Таблицам (задача не сохранена)."

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
                            updates.append({'range': gspread.utils.rowcol_to_a1(row_index, col_idx), 'values': [[value]]})

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
                        return f"Ошибка: не удалось найти нужные колонки в таблице. Бронирование не выполнено."
                    sheet.batch_update(updates)
                    return f"Слот на {date} успешно забронирован."

            return f"Свободный слот на дату {date} не найден. Возможно, он уже занят или дата указана неверно."
        except Exception as e:
            return f"Ошибка при бронировании слота: {e}"

    def get_client_bookings(self, telegram_id: str = "") -> str:
        """
        Returns all booked slots for a given client.
        Searches strictly by telegram_id only.
        """
        if not self.spreadsheet:
            self._connect()
        if not self.spreadsheet:
            return "Не удалось подключиться к Google Таблицам."

        if not telegram_id:
            return "Ошибка: не передан Telegram ID клиента."

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
                matched = row_tid == tid_str

                if matched:
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

            search_label = telegram_id
            if not bookings:
                return f"Бронирования для «{search_label}» не найдены."

            return f"Бронирования:\n" + "\n".join(bookings)
        except Exception as e:
            return f"Ошибка при поиске бронирований: {e}"

    def create_document_task(self, company: str, inn: str, doc_type: str, contact: str, link: str, telegram_id: str = "") -> str:
        """
        Writes a new task to the 'Задачи (Документы)' sheet.
        """
        if not self.spreadsheet:
            self._connect()
        if not self.spreadsheet:
            return "Не удалось подключиться к Google Таблицам (задача не сохранена)."

        try:
            from datetime import datetime
            now_str = datetime.now().strftime("%d.%m %H:%M")
            sheet = self.spreadsheet.worksheet("Задачи (Документы)")

            # Request row format: | Дата и Время | Статус | Тип документа | Название Клиента | ИНН | Telegram ID | Контактное лицо | Ссылка на диалог |
            new_row = [now_str, "В очереди", doc_type, company, inn, str(telegram_id) if telegram_id else "", contact, link]
            sheet.append_row(new_row, table_range="A1")
            return "Задача успешно добавлена."
        except Exception as e:
            return f"Ошибка при записи задачи: {e}"


sheets_service = GoogleSheetsService()
