"""Shared time-window parsing for Coyote agent."""
from __future__ import annotations
import re


def days_from_text(text: str, default: int = 90) -> int:
    """Extract a time window (in days) from natural language.

    Supports numeric patterns ("past 3 days"), spelled-out numbers
    ("past three weeks"), and common phrases ("yesterday", "this month").

    Returns *default* (90) when no temporal expression is detected.
    """
    t = (text or "").lower()

    num_words = {
        "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
        "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
        "couple": 2, "few": 3,
    }

    # Try numeric pattern: "past 3 days"
    m = re.search(r"(past|last)\s+(\d+)\s+(day|week|month|year)s?", t)
    if m:
        n = int(m.group(2))
        unit = m.group(3)
        return {"day": 1, "week": 7, "month": 30, "year": 365}[unit] * n

    # Try spelled-out numbers: "past three weeks"
    for word, num in num_words.items():
        m = re.search(rf"(past|last)\s+{word}\s+(day|week|month|year)s?", t)
        if m:
            unit = m.group(2)
            return {"day": 1, "week": 7, "month": 30, "year": 365}[unit] * num

    # Common phrases
    if "today" in t:
        return 1
    if "yesterday" in t:
        return 2
    if "this week" in t or "past week" in t:
        return 7
    if "last week" in t:
        return 14
    if "this month" in t or "past month" in t:
        return 30
    if "last month" in t:
        return 30
    if "this year" in t or "past year" in t:
        return 365

    return default
