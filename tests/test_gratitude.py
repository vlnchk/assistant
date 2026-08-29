import unittest

from bot.handlers import is_gratitude_only


class GratitudeRecognitionTests(unittest.TestCase):
    def test_accepts_standalone_gratitude_variants(self):
        accepted = [
            "Спасибо",
            "спс!",
            "Благодарю вас",
            "Спасибо большое за информацию!",
            "Спасибо вам большое!",
            "Большое вам спасибо",
            "Огромное спасибо 🙏",
            "Супер, спасибо!",
            'Реально спасибо"',
            "От души спасибо",
            "Спасибо от души!",
            "Понял, благодарю 😊",
            "Всё понятно, спасибо",
        ]

        for text in accepted:
            with self.subTest(text=text):
                self.assertTrue(is_gratitude_only(text))

    def test_does_not_swallow_questions_or_business_messages(self):
        rejected = [
            "Спасибо, а какие даты свободны?",
            "Благодарю, нужен ещё договор",
            "Спасибо за ответ, забронируйте 5 сентября",
            "Реально спасибо, а какие даты свободны?",
            "Подскажите стоимость",
            "",
        ]

        for text in rejected:
            with self.subTest(text=text):
                self.assertFalse(is_gratitude_only(text))


if __name__ == "__main__":
    unittest.main()
