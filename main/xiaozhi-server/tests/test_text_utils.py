import unittest

from core.utils.textUtils import get_string_no_punctuation_or_emoji


class TextUtilsTest(unittest.TestCase):
    def test_pure_sentence_delimiters_are_not_sent_to_tts(self) -> None:
        for value in ("；", ";", "？", "?", "。", "！", "~", "～", "……"):
            self.assertEqual(get_string_no_punctuation_or_emoji(value), "")

    def test_sentence_delimiters_are_trimmed_without_damaging_content(self) -> None:
        self.assertEqual(
            get_string_no_punctuation_or_emoji("；客户报价；"),
            "客户报价",
        )


if __name__ == "__main__":
    unittest.main()
