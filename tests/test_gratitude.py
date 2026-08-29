import unittest

from bot import replies
from llm.tools import bot_tools, reply_gratitude


class GratitudeRoutingTests(unittest.TestCase):
    def test_gratitude_has_a_canonical_terminal_tool(self):
        self.assertEqual(reply_gratitude(), replies.GRATITUDE)

    def test_gratitude_tool_is_available_to_model(self):
        names = {tool.__name__ for tool in bot_tools}
        self.assertIn("reply_gratitude", names)


if __name__ == "__main__":
    unittest.main()
