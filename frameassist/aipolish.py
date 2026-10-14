"""Optional AI polish for dictations via OpenRouter (Groq, or OpenAI fallback).

Off by default. When on, the *text* of your dictation goes through a language
model on your own OpenRouter account to tidy it: "at 7, actually 8" becomes
"at 8", punctuation and lists get cleaned up. Nothing else is sent.

Kept deliberately small and dependency-free (urllib only) so it installs
cleanly on the headset.
"""

import json
import urllib.request

# Groq, then OpenAI as a fallback when Groq is busy. Both are served through
# the same OpenRouter API, so one endpoint is enough.
DEFAULT_MODEL = "groq/llama-3.3-70b-versatile"
ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"

SYSTEM = (
    "You clean up voice-dictated text. Fix punctuation, capitalization, and "
    "spacing. Resolve spoken corrections (\"at 7, actually 8\" becomes \"at 8\"). "
    "Return only the cleaned text, nothing else."
)


def polish(text, api_key, model=DEFAULT_MODEL, timeout=20):
    """Return the cleaned text, or the original if anything fails (a network
    failure should never eat a dictation)."""
    if not api_key:
        return text
    if not (text and text.strip()):
        return text
    payload = {
        "model": model or DEFAULT_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": text},
        ],
        "temperature": 0.2,
    }
    req = urllib.request.Request(
        ENDPOINT,
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.load(resp)
        return (body["choices"][0]["message"]["content"] or "").strip() or text
    except Exception:
        return text
