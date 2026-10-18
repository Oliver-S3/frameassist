"""Optional AI polish for dictations via OpenRouter or a local model server.

Off by default. When on, the *text* of your dictation goes through a language
model to tidy it: "at 7, actually 8" becomes "at 8", punctuation and lists
get cleaned up. Nothing else is sent.

Default is OpenRouter (Groq, or OpenAI fallback through the same API) using
your own key. Any OpenAI-compatible local server can be used instead by
setting the AI base URL (Ollama ``http://localhost:11434/v1``, LM Studio
``http://localhost:1234/v1``, llama.cpp server ``http://localhost:8080``) —
then the text never leaves your machine and no API key is needed.

Kept deliberately small and dependency-free (urllib only) so it installs
cleanly on the headset.
"""

import json
import urllib.request

# OpenRouter's OpenAI-compatible endpoint. Local servers (Ollama, LM Studio,
# llama.cpp server, Open WebUI) expose the same ``/v1/chat/completions`` shape,
# so one client plus a configurable base URL covers both.
DEFAULT_MODEL = "groq/llama-3.3-70b-versatile"
DEFAULT_ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"

SYSTEM = (
    "You clean up voice-dictated text. Fix punctuation, capitalization, and "
    "spacing. Resolve spoken corrections (\"at 7, actually 8\" becomes \"at 8\"). "
    "Return only the cleaned text, nothing else."
)


def endpoint_for(base_url):
    """The full chat-completions URL for a base URL (or the OpenRouter default).

    Accepts ``http://host:port/v1`` (Ollama/LM Studio), a bare
    ``http://host:port`` (llama.cpp server), or an already-complete
    ``.../chat/completions`` URL.
    """
    url = (base_url or "").strip().rstrip("/")
    if not url:
        return DEFAULT_ENDPOINT
    if url.endswith("/chat/completions"):
        return url
    if url.endswith(("/v1", "/api/v1")):
        return url + "/chat/completions"
    return url + "/v1/chat/completions"


def polish(text, api_key, model=DEFAULT_MODEL, base_url="", timeout=20):
    """Return the cleaned text, or the original if anything fails (a network
    failure should never eat a dictation).

    Cloud (no ``base_url``) requires ``api_key``; a local ``base_url`` needs
    no key (Ollama/LM Studio ignore auth), so the Authorization header is only
    sent when a key is present.
    """
    if not api_key and not base_url:
        return text
    if not (text and text.strip()):
        return text
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = "Bearer " + api_key
    payload = {
        "model": model or DEFAULT_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": text},
        ],
        "temperature": 0.2,
    }
    req = urllib.request.Request(
        endpoint_for(base_url),
        data=json.dumps(payload).encode(),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.load(resp)
        return (body["choices"][0]["message"]["content"] or "").strip() or text
    except Exception:
        return text
