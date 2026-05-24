"""Tests for style profile extraction fixes and HTML MIME conversion."""
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from tools.google_services import _build_style_features, _extract_closing, _plain_to_html


class TestGreetingExtraction:
    def test_strips_name_from_greeting(self):
        samples = ["Hi Richard,\nHope you're doing well.\n\nBest,\nShivoham"]
        result = _build_style_features(samples)
        assert result["greeting_patterns"] == ["Hi"], (
            f"Expected ['Hi'], got {result['greeting_patterns']!r} — name leaked into greeting"
        )

    def test_hello_keyword_only(self):
        samples = ["Hello Akash,\nJust following up.\n\nThanks,\nShivoham"]
        result = _build_style_features(samples)
        assert result["greeting_patterns"] == ["Hello"]

    def test_most_common_wins(self):
        samples = [
            "Hi Alice,\nBody.\n\nBest,\nShivoham",
            "Hi Bob,\nBody.\n\nBest,\nShivoham",
            "Hello Carol,\nBody.\n\nBest,\nShivoham",
        ]
        result = _build_style_features(samples)
        assert result["greeting_patterns"][0] == "Hi"


class TestClosingExtraction:
    def test_keyword_only_no_name(self):
        assert _extract_closing("Some body.\n\nThanks, Richard") == "Thanks"

    def test_thank_you_before_thanks(self):
        assert _extract_closing("Some body.\n\nThank you so much") == "Thank you"

    def test_best_keyword(self):
        assert _extract_closing("Body.\n\nBest regards,\nShivoham") == "Best"

    def test_no_closing_returns_empty(self):
        assert _extract_closing("Just a message with no closing.") == ""


class TestPlainToHtml:
    def test_single_paragraph(self):
        out = _plain_to_html("Hello world")
        assert "<p>" in out and "Hello world" in out

    def test_line_breaks_become_br(self):
        out = _plain_to_html("line1\nline2")
        assert "<br>" in out

    def test_double_newline_splits_paragraphs(self):
        out = _plain_to_html("para1\n\npara2")
        assert out.count("<p>") == 2

    def test_html_special_chars_escaped(self):
        out = _plain_to_html("a & b <c>")
        assert "&amp;" in out
        assert "&lt;" in out
        assert "<c>" not in out
