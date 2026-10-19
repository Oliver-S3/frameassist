"""Tests for the keyboard mapping (pure, no hardware needed)."""

from frameassist.keyboard import SHORTCUTS, all_codes, to_keystrokes
from frameassist.linux_input import KEY


def test_letters_and_shift():
    ks = to_keystrokes("Hello")
    assert ks == [(KEY["H"], True), (KEY["E"], False), (KEY["L"], False),
                  (KEY["L"], False), (KEY["O"], False)]


def test_numbers_and_symbols():
    ks = to_keystrokes("1!2@")
    assert ks[0] == (KEY["1"], False)
    assert ks[1] == (KEY["1"], True)
    assert ks[2] == (KEY["2"], False)
    assert ks[3] == (KEY["2"], True)


def test_whisper_smart_quotes_are_straightened():
    ks = to_keystrokes("it’s “great” — really…")
    text_chars = [code for code, _ in ks]
    assert KEY["APOSTROPHE"] in text_chars   # ’ -> '
    assert KEY["LEFTBRACE"] not in text_chars  # “ -> "
    assert KEY["MINUS"] in text_chars          # — -> -
    assert KEY["DOT"] in text_chars            # … -> ...


def test_untypeable_dropped():
    ks = to_keystrokes("emoji 🎉 ok")
    # the emoji is dropped; the two spaces survive
    codes = [c for c, _ in ks]
    assert codes.count(KEY["SPACE"]) == 2
    assert KEY["O"] in codes
    assert KEY["K"] in codes


def test_newline_is_enter():
    ks = to_keystrokes("a\nb")
    assert (KEY["ENTER"], False) in ks


def test_all_codes_covers_map_and_shortcuts():
    codes = all_codes()
    assert KEY["LEFTSHIFT"] in codes
    for name, combo in SHORTCUTS.items():
        for key in combo:
            assert KEY[key] in codes


def test_shortcut_names():
    assert SHORTCUTS["copy"] == ["LEFTCTRL", "C"]
    assert SHORTCUTS["paste"] == ["LEFTCTRL", "V"]
    assert SHORTCUTS["enter"] == ["ENTER"]
    assert SHORTCUTS["escape"] == ["ESC"]
