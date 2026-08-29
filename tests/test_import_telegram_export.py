import unittest

from scripts.import_telegram_export import anonymize_text, extract_dialogs, flatten_text


class TelegramExportImportTests(unittest.TestCase):
    def test_flatten_text_supports_rich_entities(self):
        value = ["Привет, ", {"type": "link", "text": "ссылка"}, "!"]
        self.assertEqual(flatten_text(value), "Привет, ссылка!")

    def test_anonymize_text_masks_common_identifiers(self):
        source = (
            "Меня зовут Анна, ООО «Ромашка», ИНН 7701234567, "
            "почта anna@example.com, профиль @anna, https://example.com"
        )
        result = anonymize_text(source)
        self.assertNotIn("Анна", result)
        self.assertNotIn("Ромашка", result)
        self.assertNotIn("7701234567", result)
        self.assertNotIn("anna@example.com", result)
        self.assertNotIn("@anna", result)
        self.assertNotIn("example.com", result)
        self.assertIn("[NAME]", result)
        self.assertIn("[INN]", result)

    def test_extract_dialogs_groups_topics_and_skips_profile_cards(self):
        export = {
            "messages": [
                {
                    "id": 10,
                    "type": "service",
                    "action": "topic_created",
                    "text": "",
                },
                {
                    "id": 11,
                    "type": "message",
                    "reply_to_message_id": 10,
                    "text": [
                        {"type": "bold", "text": "👤 Клиент:"},
                        "\n@private_user Привет",
                    ],
                },
                {
                    "id": 12,
                    "type": "message",
                    "reply_to_message_id": 11,
                    "text": "🤖 Бот:\nЗдравствуйте!",
                },
                {
                    "id": 13,
                    "type": "message",
                    "reply_to_message_id": 10,
                    "text": "👤 Клиент:\n🆔 123456789 📛 Имя: Анна",
                },
                {
                    "id": 14,
                    "type": "message",
                    "reply_to_message_id": 10,
                    "text": "👤 Клиент:\n/start",
                },
            ]
        }

        dialogs = extract_dialogs(export)

        self.assertEqual(len(dialogs), 1)
        self.assertEqual(dialogs[0]["turns"], [{"user": "Привет"}])


if __name__ == "__main__":
    unittest.main()
