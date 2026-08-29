import unittest

from bot.handlers import is_non_vacancy_promotion


class PromotionRoutingTests(unittest.TestCase):
    def test_program_announcements_are_paid_promotion(self):
        promotional_requests = [
            (
                "Могли ли бы вы разместить информацию о наборе на программу "
                "в вашем паблике? Это бесплатно для студентов, участники "
                "получат стажировку."
            ),
            "Хотим опубликовать анонс бесплатного курса для студентов",
            "Можно разместить в канале информацию о благотворительном проекте?",
            "Сколько стоит рекламный пост про наш вебинар?",
        ]

        for text in promotional_requests:
            with self.subTest(text=text):
                self.assertTrue(is_non_vacancy_promotion(text))

    def test_real_vacancies_are_not_intercepted_as_promotion(self):
        vacancy_requests = [
            "Хочу разместить вакансию преподавателя курса",
            "Опубликуйте вакансию менеджера образовательного проекта",
            "Ищем преподавателя на курс, зарплата 100 000 рублей",
            "Как разместить вакансию?",
        ]

        for text in vacancy_requests:
            with self.subTest(text=text):
                self.assertFalse(is_non_vacancy_promotion(text))


if __name__ == "__main__":
    unittest.main()
