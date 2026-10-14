"""Clean a dictation before it's typed.

This is where Whisper's raw text becomes the text that lands in your text box:
smart punctuation, "add a space after each dictation" / "press Enter after
each dictation", and the optional AI polish passthrough.

The AI polish itself (OpenRouter -> Groq, or OpenAI if Groq is busy) lives in
``aipolish.py``; this module decides whether and how to call it.
"""

import re

# Straightening that doesn't change meaning and always helps.
_SMART = [
    (r"\s+", " "),                       # collapse double spaces
    (r"\s+([,.;:!?])", r"\1"),           # no space before punctuation
    (r"^\s+|\s+$", ""),                  # trim
]
_CAP = re.compile(r"(?<=[.!?]\s)[a-z]")

# number words -> int
_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
          "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}

# "three things: one, ... two, ..." -> a numbered list. m.group(2) is e.g.
# "one, ... two, ..." ; items are split on ", " or " ... ".
_LIST_RE = re.compile(
    r"\b([1-9]|one|two|three|four|five|six|seven|eight|nine|ten)\s+things?\s*:\s*(.*)",
    re.IGNORECASE)


def format_lists(text):
    """Turn "three things: one, two, three" into a numbered list.

    Whisper tends to transcribe spoken lists as a number word + an item then
    "one, two, three". This matches the phrasing FrameAssist documents ("say
    'three things: one, ... two, ...'"). Items are separated by ", " or
    " ... ". Each item goes on its own line.
    """
    def _m(match):
        word = match.group(1).lower()
        n = int(word) if word.isdigit() else _WORDS.get(word)
        if n is None:
            return match.group(0)
        body = match.group(2).strip()
        items = [i.strip() for i in re.split(r",\s*|\s*\.\.\.\s*", body) if i.strip()]
        # only treat it as a real list if the item count ≈ the declared number
        if len(items) != n or n < 2:
            return match.group(0)
        return "\n".join(f"{i}. {item}" for i, item in enumerate(items, 1))

    return _LIST_RE.sub(_m, text)


def basic_clean(text):
    """Cheap, deterministic cleanup that never needs a network call."""
    for pattern, repl in _SMART:
        text = re.sub(pattern, repl, text)
    text = format_lists(text)
    return text.strip()


def capitalize(text):
    """Capitalize the start of each sentence (after . ! ?)."""
    if not text:
        return text
    text = text[0].upper() + text[1:]
    return _CAP.sub(lambda m: m.group(0).upper(), text)


def spell_with_words(text, words):
    """Apply the user's 'My words' list naively: ensure exact-case words that
    Whisper lowers appear as the user spelled them. Word list wins over the
    raw transcript for exact matches, which is what makes a name stick."""
    for word in words:
        if not word:
            continue
        lowered = word.lower()
        text = re.sub(rf"\b{re.escape(lowered)}\b", word, text)
    return text


def finalize(text, cfg, polish=None, lists=True):
    """Turn raw Whisper text into what gets typed.

    ``polish`` is an object with ``apply(text) -> str`` (the AI polish
    backend). When the config has AI polish on and a backend is supplied, the
    text goes through it; otherwise plain cleaning is used. With ``lists``,
    "three things: one, ..." becomes a numbered list.
    """
    if not text:
        return ""
    if cfg.get("ai_polish") and polish is not None:
        polished = polish.apply(text)
        if polished:
            text = polished
    text = basic_clean(text)
    if lists:
        text = format_lists(text)
    if not text:
        return ""
    if cfg.get("add_space", True):
        text = text + " "
    return text


class NoPolish:
    """AI polish that's off: pass text through unchanged."""

    def __init__(self, cfg=None):
        self.cfg = cfg or {}

    @property
    def enabled(self):
        return bool(self.cfg.get("ai_polish")) and bool(self.cfg.get("ai_key"))

    def apply(self, text):
        if self.enabled:
            from .aipolish import polish
            return polish(text, self.cfg.get("ai_key", ""), self.cfg.get("ai_model"))
        return text
