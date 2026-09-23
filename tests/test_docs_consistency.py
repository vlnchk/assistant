"""Индексы в dev/ совпадают с файлами, на которые ссылаются.

Поле «Статус» у ADR и находок существует ради одного: чтобы разрыв между
«решили» и «сделали» было видно. 23.09.2026 выяснилось, что после M2 и M3
статусы ADR-0004, ADR-0005 и обеих находок остались «не реализовано» —
обновить их забыл автор самой системы. Ручной шаг теряется; этот тест
ловит хотя бы расхождение между индексом и файлом.
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROW = re.compile(r"^\| \[[^\]]+\]\(([^)]+\.md)\) \|.*\| ([^|]+) \|$")
STATUS = re.compile(r"^- \*\*Статус:\*\* (.+)$", re.M)


def _index(folder: Path) -> dict[str, str]:
    rows = {}
    for line in (folder / "README.md").read_text(encoding="utf-8").splitlines():
        match = ROW.match(line.strip())
        if match:
            rows[match.group(1)] = match.group(2).strip()
    return rows


def _documents(folder: Path) -> list[Path]:
    return sorted(p for p in folder.glob("*.md") if p.name != "README.md")


class IndexConsistencyTests(unittest.TestCase):
    FOLDERS = (ROOT / "dev" / "decisions", ROOT / "dev" / "findings")

    def test_every_document_is_indexed_and_every_row_exists(self):
        for folder in self.FOLDERS:
            with self.subTest(folder=folder.name):
                index = _index(folder)
                files = {p.name for p in _documents(folder)}
                self.assertEqual(set(index), files)

    def test_index_status_matches_the_document(self):
        """Индекс может быть короче файла, но не может ему противоречить."""
        for folder in self.FOLDERS:
            index = _index(folder)
            for document in _documents(folder):
                with self.subTest(document=document.name):
                    match = STATUS.search(document.read_text(encoding="utf-8"))
                    self.assertIsNotNone(match, "нет строки «Статус»")
                    self.assertTrue(
                        match.group(1).startswith(index[document.name]),
                        f"индекс: {index[document.name]!r}, файл: {match.group(1)!r}",
                    )


class ShippedDecisionsTests(unittest.TestCase):
    """Выкаченный майлстоун не может оставить свой ADR «не реализованным».

    Именно эту ошибку допустил автор системы после M2 и M3: и файл ADR, и
    индекс согласованно говорили «принято, не реализовано», поэтому проверка
    согласованности выше её не видела. Эта — видит: строка в «Выкачено»
    ссылается на ADR, который майлстоун реализовал.
    """

    UNSHIPPED = ("предложено", "принято, не реализовано")

    def test_adrs_of_shipped_milestones_are_marked_implemented(self):
        roadmap = (ROOT / "dev" / "ROADMAP.md").read_text(encoding="utf-8")
        shipped = roadmap.split("## Выкачено", 1)[1]
        numbers = sorted(set(re.findall(r"ADR-(\d{4})", shipped)))
        self.assertTrue(numbers, "в «Выкачено» нет ни одной ссылки на ADR")

        for number in numbers:
            with self.subTest(adr=number):
                (document,) = (ROOT / "dev" / "decisions").glob(f"{number}-*.md")
                status = STATUS.search(document.read_text(encoding="utf-8")).group(1)
                self.assertFalse(
                    status.startswith(self.UNSHIPPED),
                    f"ADR-{number} выкачен, но в статусе: {status!r}",
                )

if __name__ == "__main__":
    unittest.main()
