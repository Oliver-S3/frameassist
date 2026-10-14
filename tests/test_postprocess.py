"""Tests for post-processing: cleaning, spaces, Enter, AI polish hook."""

from frameassist import postprocess as p


def test_empty_stays_empty():
    assert p.finalize("", {}) == ""
    assert p.finalize("   ", {}) == ""


def test_add_space_default():
    assert p.finalize("hello", {"add_space": True}) == "hello "
    assert p.finalize("hello", {"add_space": False}) == "hello"


def test_cleans_double_spaces_and_punctuation():
    out = p.basic_clean("hello   world ,  yes")
    assert out == "hello world, yes"


def test_no_space_before_period():
    assert p.basic_clean("foo .") == "foo."


def test_press_enter():
    # The engine adds Enter after typing; here we check no Enter char is added
    # by finalize itself.
    assert p.finalize("hi", {"add_space": False}) == "hi"


def test_capitalize_sentences():
    assert p.capitalize("hello. it is I!") == "Hello. It is I!"
    assert p.capitalize("a") == "A"


def test_ai_polish_on_with_key_calls_backend():
    calls = []

    class FakePolisher:
        def apply(self, text):
            calls.append(text)
            return "Tidied."

    out = p.finalize("raw text", {"ai_polish": True, "add_space": False},
                     polish=FakePolisher())
    assert out == "Tidied."
    assert calls == ["raw text"]


def test_ai_polish_off_does_not_call():
    calls = []

    class FakePolisher:
        def apply(self, text):
            calls.append(text)
            return "Tidied."

    out = p.finalize("raw text", {"ai_polish": False, "add_space": False},
                     polish=FakePolisher())
    assert out == "raw text"
    assert calls == []


def test_words_spelling():
    words = ["FrameAssist"]
    assert p.spell_with_words("hold both grips and frameassist", words).endswith("FrameAssist")


def test_format_lists():
    out = p.format_lists("three things: one, two, three")
    assert out == "1. one\n2. two\n3. three"


def test_format_lists_ellipsis_separator():
    out = p.format_lists("two things: alpha ... beta")
    assert out == "1. alpha\n2. beta"


def test_format_lists_ignores_mismatch():
    # "three things" but only two items: leave it alone
    out = p.format_lists("three things: one, two")
    assert "1." not in out


def test_finalize_applies_lists():
    out = p.finalize("three things: one, two, three", {"add_space": False})
    assert out == "1. one\n2. two\n3. three"
