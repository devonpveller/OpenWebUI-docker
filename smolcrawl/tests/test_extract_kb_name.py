import sys, types, unittest
from pathlib import Path

try:  # smolcrawl_pipeline does `from pydantic import BaseModel` at import
    import pydantic  # noqa: F401  (real pydantic wins where installed)
except ImportError:  # executor image has no pydantic — stub the one symbol
    _m = types.ModuleType("pydantic")
    _m.BaseModel = type("BaseModel", (), {})
    sys.modules["pydantic"] = _m
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from smolcrawl_pipeline import Pipeline  # noqa: E402
from smolcrawl_pipeline import _normalize_kb_name  # noqa: E402


class TestExtractKbName(unittest.TestCase):
    """_extract_kb_name must never infer a name from tag/keyword fragments."""

    def _name(self, message):
        url = Pipeline._extract_url(message)
        self.assertIsNotNone(url, f"no URL found in {message!r}")
        return Pipeline._extract_kb_name(message, url)

    def test_reporter_repro_mandatory_tag(self):
        # Issue #17 repro: `to` inside `mandatory` must not capture `ry>`.
        message = "<query>crawl https://docks.gaggimate.eu/</query> <mandatory>"
        self.assertEqual(self._name(message), "SmolCrawl - docks.gaggimate.eu")

    def test_keyword_inside_word_mandatory(self):
        self.assertEqual(
            self._name("crawl https://example.com/ <mandatory>"),
            "SmolCrawl - example.com",
        )

    def test_keyword_inside_word_automatically(self):
        self.assertEqual(
            self._name("crawl https://example.com/ automatically"),
            "SmolCrawl - example.com",
        )

    def test_keyword_inside_word_database(self):
        self.assertEqual(
            self._name("crawl https://example.com/ database"),
            "SmolCrawl - example.com",
        )

    def test_explicit_name_into(self):
        self.assertEqual(
            self._name("crawl https://example.com/ into My KB Name"),
            "My KB Name",
        )

    def test_explicit_name_kb_colon(self):
        self.assertEqual(
            self._name("crawl https://example.com/ kb: Gaggia Docs"),
            "Gaggia Docs",
        )

    def test_explicit_name_quoted(self):
        self.assertEqual(
            self._name('crawl https://example.com/ into "Gaggia Docs"'),
            "Gaggia Docs",
        )

    def test_explicit_name_apostrophe(self):
        self.assertEqual(
            self._name("crawl https://example.com/ into Bill's Docs"),
            "Bill's Docs",
        )

    def test_suffix_stripping_with(self):
        self.assertEqual(
            self._name('crawl https://example.com/ into "Gaggia Docs" with max depth 2'),
            "Gaggia Docs",
        )

    def test_bare_url_domain_fallback(self):
        self.assertEqual(
            self._name("crawl https://docks.gaggimate.eu/"),
            "SmolCrawl - docks.gaggimate.eu",
        )

    def test_name_over_80_chars_fallback(self):
        message = "crawl https://example.com/ into " + "x" * 81
        self.assertEqual(self._name(message), "SmolCrawl - example.com")

    # -- word-INITIAL hits: keyword at the start of a longer word must NOT
    #    match (a shared leading \b alone leaves these open) --

    def test_word_start_today_fallback(self):
        # `to` at the start of `today` must not capture `day`.
        self.assertEqual(
            self._name("crawl https://example.com/ today"),
            "SmolCrawl - example.com",
        )

    def test_word_start_asap_fallback(self):
        # `as` at the start of `asap` must not capture `ap`.
        self.assertEqual(
            self._name("crawl https://example.com/ asap"),
            "SmolCrawl - example.com",
        )

    def test_word_start_tomorrow_fallback(self):
        # `to` at the start of `tomorrow` must not capture `morrow`.
        self.assertEqual(
            self._name("crawl https://example.com/ tomorrow"),
            "SmolCrawl - example.com",
        )

    def test_prompt_wrapper_assistant(self):
        # Prompt-wrapper tail: `as` at the start of `Assistant` must not
        # capture `sistant: proceed`.
        self.assertEqual(
            self._name("crawl https://example.com/\nAssistant: proceed"),
            "SmolCrawl - example.com",
        )

    # -- whole-word hits: `to` and `as` as standalone keywords must still
    #    match (the boundary is per-alternative, not shared) --

    def test_explicit_name_to(self):
        self.assertEqual(
            self._name("crawl https://example.com/ to My KB"),
            "My KB",
        )

    def test_explicit_name_as(self):
        self.assertEqual(
            self._name("crawl https://example.com/ as My KB"),
            "My KB",
        )

    # -- system/instruction text: tag tails must fail the match, not be
    #    captured --

    def test_system_instruction_tail(self):
        self.assertEqual(
            self._name(
                "crawl https://example.com/ <instructions>be concise</instructions>"
            ),
            "SmolCrawl - example.com",
        )


class TestNormalizeKbName(unittest.TestCase):
    """_normalize_kb_name must not let dangling tag delimiters survive."""

    def test_dangling_close_bracket_stripped(self):
        self.assertEqual(_normalize_kb_name("ry>"), "ry")

    def test_dangling_open_bracket_stripped(self):
        self.assertEqual(_normalize_kb_name("<ry"), "ry")

    def test_complete_tags_stripped(self):
        self.assertEqual(_normalize_kb_name("<b>Gaggia</b> Docs"), "Gaggia Docs")


if __name__ == "__main__":
    unittest.main()
