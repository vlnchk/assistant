import datetime
import unittest

from bot import replies
from services.google_sheets import GoogleSheetsService


class FakeWorksheet:
    def __init__(self, headers, records=None):
        self.headers = list(headers)
        self.records = list(records or [])
        self.updated_cells = []
        self.batch_updates = []
        self.appended_rows = []

    def row_values(self, row):
        return list(self.headers)

    def get_all_records(self):
        return list(self.records)

    def update_cell(self, row, col, value):
        self.updated_cells.append((row, col, value))
        while len(self.headers) < col:
            self.headers.append("")
        self.headers[col - 1] = value

    def batch_update(self, updates):
        self.batch_updates.extend(updates)

    def append_row(self, row, table_range=None):
        self.appended_rows.append((list(row), table_range))


class FakeSpreadsheet:
    def __init__(self, worksheets):
        self.worksheets = worksheets

    def worksheet(self, name):
        return self.worksheets[name]


class GoogleSheetsServiceTests(unittest.TestCase):
    def make_service(self, worksheets):
        service = GoogleSheetsService()
        service.spreadsheet = FakeSpreadsheet(worksheets)
        return service

    def test_nearest_slots_ask_client_to_choose_or_name_another_date(self):
        target = datetime.date.today() + datetime.timedelta(days=1)
        sheet = FakeWorksheet(
            ["Дата", "Статус слота"],
            [{"Дата": target.strftime("%d.%m.%Y"), "Статус слота": "Свободен"}],
        )
        service = self.make_service({"Календарь (Слоты)": sheet})

        result = service.get_free_slots()

        self.assertIn("Подходит ли какая-нибудь из них?", result)
        self.assertIn("Если нет, назовите желаемую дату", result)

    def test_booking_records_time_as_unconfirmed_preference(self):
        target = datetime.date.today() + datetime.timedelta(days=1)
        if target.month == ((datetime.date.today().month + 3 - 1) % 12) + 1:
            self.skipTest("target unexpectedly crossed booking cap")
        sheet = FakeWorksheet(
            ["Дата", "Статус слота", "Клиент", "Ссылка на диалог", "Комментарии"],
            [{"Дата": target.strftime("%d.%m.%Y"), "Статус слота": "Свободен"}],
        )
        service = self.make_service({"Календарь (Слоты)": sheet})

        result = service.book_slot(
            target.strftime("%d.%m.%Y"),
            "ООО Тест",
            "",
            telegram_id="42",
            ad_topic="курс",
            publish_time="12:00–14:00",
        )

        self.assertIn("точное время подтвердит менеджер", result)
        values = [item["values"][0][0] for item in sheet.batch_updates]
        self.assertIn("12:00–14:00", values)

    def test_document_row_keeps_requisites_and_follows_header_order(self):
        sheet = FakeWorksheet(
            [
                "Статус",
                "Дата и Время",
                "ИНН",
                "Название Клиента",
                "Тип документа",
                "Telegram ID",
                "Контактное лицо",
                "Ссылка на диалог",
            ]
        )
        service = self.make_service({"Задачи (Документы)": sheet})

        result = service.create_document_task(
            "=ООО Формула",
            "7701234567",
            "счёт",
            "@anna",
            "https://t.me/c/1/2",
            telegram_id="42",
            requisites="БИК 044525000",
        )

        self.assertEqual(result, replies.DOC_CREATED.format(doc_type="счёт"))
        self.assertIn("Реквизиты / комментарий", sheet.headers)
        row, table_range = sheet.appended_rows[0]
        self.assertEqual(table_range, "A1")
        self.assertEqual(row[sheet.headers.index("ИНН")], "7701234567")
        self.assertEqual(row[sheet.headers.index("Название Клиента")], "'=ООО Формула")
        self.assertEqual(
            row[sheet.headers.index("Реквизиты / комментарий")],
            "БИК 044525000",
        )


if __name__ == "__main__":
    unittest.main()
