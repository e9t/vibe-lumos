"""LLM-powered query expansion for smart search."""

from __future__ import annotations

import os
import shlex

from lumos.core.config import LlmConfig


_SYSTEM_PROMPT = """\
You are a search query expander. Given a query, return ONLY direct \
synonyms, translations (Korean↔English), and abbreviation expansions.

Rules:
- Return ONLY a comma-separated list of terms, nothing else.
- Include the original query first.
- Generate 2-4 additional terms MAX.
- ONLY the SAME concept in different languages or forms. Nothing else.
- Do NOT add descriptions, definitions, or explanations of the term.
- Do NOT add related fields, subcategories, or broader topics.

Examples:
- "머신러닝" → "머신러닝, ML, machine learning, 기계학습"
- "ML" → "ML, machine learning, 머신러닝, 기계학습"
- "자연어처리" → "자연어처리, NLP, natural language processing"
- "database" → "database, DB, 데이터베이스"
"""


def expand_query(query: str, config: LlmConfig) -> tuple[list[str], str | None]:
    """Expand query terms using LLM. Returns (terms, error_message)."""
    original_terms = _parse_terms(query)
    if not original_terms:
        return [], None

    api_key = os.environ.get(config.api_key_env)
    if not api_key:
        return original_terms, f"LLM API key not set ({config.api_key_env}). Falling back to exact match."

    try:
        from openai import OpenAI

        client = OpenAI(api_key=api_key, base_url=config.base_url)
        resp = client.chat.completions.create(
            model=config.model,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": query},
            ],
            temperature=0.3,
            max_tokens=200,
        )
        raw = resp.choices[0].message.content or ""
        expanded = [t.strip().strip('"\'') for t in raw.split(",") if t.strip()]
        # Filter out terms that are too long (likely descriptions, not synonyms)
        expanded = [t for t in expanded if len(t.split()) <= 3]
        # Deduplicate while preserving order, ensure originals are first
        seen: set[str] = set()
        result: list[str] = []
        for t in original_terms + expanded:
            low = t.lower()
            if low not in seen and t:
                seen.add(low)
                result.append(t)
        return result, None
    except Exception as e:
        return original_terms, f"LLM query expansion failed: {e}. Falling back to exact match."


def _parse_terms(query: str) -> list[str]:
    try:
        return shlex.split(query.strip())
    except ValueError:
        return query.strip().split()
