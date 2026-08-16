"""LLM-powered salient phrase suggestion — the skeleton of a page, marked.

Given a page's text, return verbatim phrases worth highlighting. Every returned
phrase is verified to be an exact substring of the input so the extension can
find and mark it in the DOM.

The prompt stays deliberately short. Measured against this model, the three
rules below match a seven-rule version on density, length and spread; the rest
were either dead weight or already enforced in code by verify_phrases. Dropping
them entirely, though, is not an option — left to its own devices the model
returns a glossary of terms (~30 chars each) instead of readable sentences.
"""

from __future__ import annotations

import json
import os
import re

from lumos.core.config import LlmConfig

_SYSTEM_PROMPT = """\
Pick the phrases worth highlighting in this article.
- Return ONLY a JSON array of strings copied VERBATIM from the article.
- Complete sentences, not fragments or headings.
- Spread them across the whole article.
"""

_USER_TEMPLATE = """\
Article:
\"\"\"
{text}
\"\"\"

Return {max_phrases} phrases as a JSON array."""


def _normalize(text: str) -> str:
    """Collapse all whitespace runs to a single space."""
    return re.sub(r"\s+", " ", text).strip()


def _extract_json_array(raw: str) -> list[str]:
    """Pull a JSON array of strings out of a model response."""
    start = raw.find("[")
    end = raw.rfind("]")
    if start == -1 or end == -1 or end < start:
        return []
    try:
        parsed = json.loads(raw[start : end + 1])
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [p for p in parsed if isinstance(p, str)]


def verify_phrases(
    phrases: list[str],
    text: str,
    max_phrases: int = 8,
) -> list[str]:
    """Keep only phrases that appear verbatim in text, non-overlapping.

    Matching is whitespace-insensitive; the returned phrase is the slice as it
    appears in the (whitespace-normalized) source text, so the caller can search
    for it the same way.
    """
    haystack = _normalize(text)
    lowered = haystack.lower()
    taken: list[tuple[int, int]] = []
    result: list[str] = []

    for phrase in phrases:
        needle = _normalize(phrase)
        if len(needle) < 12:  # too short to be a meaningful highlight
            continue
        idx = lowered.find(needle.lower())
        if idx == -1:
            continue
        end = idx + len(needle)
        if any(idx < t_end and t_start < end for t_start, t_end in taken):
            continue
        taken.append((idx, end))
        result.append(haystack[idx:end])
        if len(result) >= max_phrases:
            break

    return result


def suggest_phrases(
    text: str,
    config: LlmConfig,
    max_phrases: int = 8,
) -> tuple[list[str], str | None]:
    """Suggest salient phrases from text. Returns (phrases, error_message)."""
    if not text or not text.strip():
        return [], None

    api_key = os.environ.get(config.api_key_env)
    if not api_key:
        return [], f"LLM API key not set ({config.api_key_env}). Skipping suggestions."

    try:
        from openai import OpenAI

        client = OpenAI(api_key=api_key, base_url=config.base_url, timeout=30.0)
        resp = client.chat.completions.create(
            model=config.model,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": _USER_TEMPLATE.format(
                        text=text, max_phrases=max_phrases
                    ),
                },
            ],
            temperature=0.2,
            max_tokens=1200,
        )
        raw = resp.choices[0].message.content or ""
    except Exception as e:
        return [], f"Suggestion failed: {e}"

    return verify_phrases(_extract_json_array(raw), text, max_phrases), None
