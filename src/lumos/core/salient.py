"""LLM-powered salient phrase suggestion — the skeleton of a page, marked.

Given a page's text, return verbatim phrases worth highlighting. Every returned
phrase is verified to be an exact substring of the input so the extension can
find and mark it in the DOM.

The prompt stays deliberately short. Measured against this model, the few
rules below match a seven-rule version on density, length and spread; the rest
were either dead weight or already enforced in code by verify_phrases. Dropping
them entirely, though, is not an option — left to its own devices the model
returns a glossary of terms (~30 chars each) instead of readable sentences.

"Spread them across the whole article" as prose does not survive contact with a
long page: the model reads far enough to fill its quota and stops, so every
suggestion lands in the opening paragraphs. Spread is therefore structural, not
requested — the article is cut into as many parts as we want phrases and the
model is asked for one from each, and verify_phrases enforces the same one-per-
part budget on the way back regardless of what the model actually returned.

Long pages are read in several parallel calls rather than sampled down: sending
the first max_chars of the page was the reason suggestions stopped a third of
the way down a long essay. Only past max_calls chunks does each part fall back
to an equal slice of the call's budget, so coverage stays end to end either way.

Count is a ceiling, never a quota. The number of passages follows the length of
the page (config `ratio`), and the model is told to skip any stretch with
nothing worth underlining — so a page of links comes back nearly empty while an
essay of the same length fills up, the way a book's most-highlighted passages
cluster where the writing earns it.
"""

from __future__ import annotations

import json
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

from lumos.core.config import LlmConfig

_SYSTEM_PROMPT = """\
Mark the passages a reader would underline — the lines they would quote later,
the ones that carry the argument. Not a summary, not the topic sentences.
- Return ONLY a JSON array of strings copied VERBATIM from the article.
- Complete sentences, not fragments or headings.
- The article is split into numbered parts. At most one passage per part, in
  order — never two from the same part.
- Skip any part with nothing worth underlining: boilerplate, navigation, lists
  of links, filler. Returning fewer passages is better than padding.
- Never copy a === PART n/N === marker into a passage.
"""

_USER_TEMPLATE = """\
Article:
\"\"\"
{text}
\"\"\"

Return at most {max_phrases} passages as a JSON array, in order — one per part
at most, and none from a part that has nothing worth underlining."""

_PART_MARKER = "=== PART {n}/{total} ==="
_MARKER_RE = re.compile(r"=+\s*PART\s*\d+\s*/\s*\d+\s*=+")

# Boundaries to cut a part at, best first: paragraph, sentence, any whitespace.
_CUT_PATTERNS = (r"\n\s*\n", r"(?<=[.!?。？！])\s+", r"\s+")


def _normalize(text: str) -> str:
    """Collapse all whitespace runs to a single space."""
    return re.sub(r"\s+", " ", text).strip()


def _split_into_parts(text: str, parts: int) -> list[str]:
    """Cut text into `parts` slices of roughly equal length.

    Cuts land on the cleanest boundary available near each ideal split point —
    a paragraph break if the text has enough of them, then sentence ends, then
    any whitespace — so no part starts mid-sentence.
    """
    if parts <= 1 or not text.strip():
        return [text]

    cuts: list[int] = []
    for pattern in _CUT_PATTERNS:
        cuts = sorted({m.end() for m in re.finditer(pattern, text)} | set(cuts))
        if len(cuts) >= parts - 1:
            break
    if not cuts:
        return [text]

    chosen: list[int] = []
    for i in range(1, parts):
        ideal = len(text) * i / parts
        after = [c for c in cuts if c > (chosen[-1] if chosen else 0)]
        if not after:
            break
        chosen.append(min(after, key=lambda c: abs(c - ideal)))

    edges = [0, *chosen, len(text)]
    return [text[a:b] for a, b in zip(edges, edges[1:]) if text[a:b].strip()]


def _excerpt(text: str, limit: int) -> str:
    """Trim text to at most limit chars, cutting back to a clean boundary."""
    if len(text) <= limit:
        return text
    head = text[:limit]
    for pattern in _CUT_PATTERNS:
        cuts = [m.end() for m in re.finditer(pattern, head)]
        # Only honour a boundary that keeps most of the slice — otherwise the
        # part shrinks to a sentence and the sample stops being representative
        if cuts and cuts[-1] > limit // 2:
            return head[: cuts[-1]]
    return head


def _prompt_text(text: str, parts: int, max_chars: int) -> str:
    """Lay out the article for the prompt: labeled parts within a char budget.

    Under budget, the model sees the whole page. Over it, every part is trimmed
    to an equal share instead of the page being cut off at max_chars — the tail
    of a long essay is worth as much as its opening.
    """
    chunks = _split_into_parts(text, parts)
    if len(text) > max_chars:
        share = max(max_chars // len(chunks), 1)
        chunks = [_excerpt(chunk, share) for chunk in chunks]
    return _label_parts(chunks)


def _label_parts(parts: list[str]) -> str:
    """Prefix each part with a marker so the model can aim at one per part."""
    if len(parts) <= 1:
        return parts[0] if parts else ""
    total = len(parts)
    return "\n\n".join(
        f"{_PART_MARKER.format(n=i, total=total)}\n{part.strip()}"
        for i, part in enumerate(parts, 1)
    )


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
    """Keep only phrases that appear verbatim in text, non-overlapping, spread.

    Matching is whitespace-insensitive; the returned phrase is the slice as it
    appears in the (whitespace-normalized) source text, so the caller can search
    for it the same way.

    The text is divided into max_phrases equal bands and at most one phrase is
    kept per band, so a model that dumped all of its picks into the first two
    paragraphs yields highlights down the whole page instead of a clump at the
    top. Leftover picks fill the slots that no band claimed, which keeps the
    count intact when a page genuinely has nothing worth marking in a band.
    Results come back in document order.
    """
    haystack = _normalize(text)
    lowered = haystack.lower()
    taken: list[tuple[int, int]] = []
    matches: list[tuple[int, int]] = []

    for phrase in phrases:
        needle = _normalize(_MARKER_RE.sub(" ", phrase))
        if len(needle) < 12:  # too short to be a meaningful highlight
            continue
        idx = lowered.find(needle.lower())
        if idx == -1:
            continue
        end = idx + len(needle)
        if any(idx < t_end and t_start < end for t_start, t_end in taken):
            continue
        taken.append((idx, end))
        matches.append((idx, end))

    if max_phrases < 1 or not matches:
        return []

    band = max(len(haystack), 1) / max_phrases
    claimed: dict[int, tuple[int, int]] = {}
    spare: list[tuple[int, int]] = []
    for match in matches:  # model order — its first pick wins a contested band
        key = min(int(match[0] // band), max_phrases - 1)
        if key in claimed:
            spare.append(match)
        else:
            claimed[key] = match

    selected = list(claimed.values())
    selected += spare[: max_phrases - len(selected)]
    selected.sort()

    return [haystack[start:end] for start, end in selected]


def _allocate(chunks: list[str], total: int) -> list[int]:
    """Hand out `total` phrase slots to chunks in proportion to their length."""
    length = sum(len(c) for c in chunks) or 1
    counts = [max(1, round(total * len(c) / length)) for c in chunks]
    # Rounding up per chunk can overshoot; trim from the shortest chunks first
    while sum(counts) > total and max(counts) > 1:
        i = max(
            (i for i, n in enumerate(counts) if n > 1),
            key=lambda i: (counts[i], -len(chunks[i])),
        )
        counts[i] -= 1
    return counts


def _client(config: LlmConfig, api_key: str):
    from openai import OpenAI

    return OpenAI(api_key=api_key, base_url=config.base_url, timeout=30.0)


def _ask(client, config: LlmConfig, body: str, count: int) -> list[str]:
    """One call: labeled parts in, a JSON array of verbatim passages out."""
    resp = client.chat.completions.create(
        model=config.model,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {
                "role": "user",
                "content": _USER_TEMPLATE.format(text=body, max_phrases=count),
            },
        ],
        temperature=0.2,
        max_tokens=1200,
    )
    return _extract_json_array(resp.choices[0].message.content or "")


def suggest_phrases(
    text: str,
    config: LlmConfig,
    max_phrases: int = 8,
    max_chars: int = 12000,
    max_calls: int = 6,
) -> tuple[list[str], str | None]:
    """Suggest salient passages from text. Returns (phrases, error_message).

    Pass the *whole* page text. max_phrases is a ceiling, not a quota — the
    model is told to skip stretches with nothing worth underlining, so a listicle
    comes back nearly empty while an essay of the same length fills up.

    A page longer than max_chars is read in several parallel calls rather than
    sampled down, up to max_calls of them; past that the chunks get excerpted.
    """
    if not text or not text.strip():
        return [], None

    api_key = os.environ.get(config.api_key_env)
    if not api_key:
        return [], f"LLM API key not set ({config.api_key_env}). Skipping suggestions."

    try:
        client = _client(config, api_key)
    except Exception as e:
        return [], f"Suggestion failed: {e}"

    calls = min(max(1, -(-len(text) // max_chars)), max_calls)
    chunks = _split_into_parts(text, calls)
    counts = _allocate(chunks, max_phrases)

    def read(chunk: str, count: int) -> list[str]:
        # One part per slot inside the chunk — the model picks within each, so
        # passages cover the page even when it reads closely only at the start.
        body = _prompt_text(chunk, count, max_chars)
        return verify_phrases(_ask(client, config, body, count), chunk, count)

    errors: list[str] = []
    results: list[list[str]] = [[] for _ in chunks]
    with ThreadPoolExecutor(max_workers=len(chunks)) as pool:
        futures = {
            pool.submit(read, chunk, count): i
            for i, (chunk, count) in enumerate(zip(chunks, counts))
        }
        for future in as_completed(futures):
            try:
                results[futures[future]] = future.result()
            except Exception as e:  # one chunk failing shouldn't lose the rest
                errors.append(str(e))

    phrases = [p for chunk_phrases in results for p in chunk_phrases]
    if not phrases and errors:
        return [], f"Suggestion failed: {errors[0]}"
    return phrases, None
