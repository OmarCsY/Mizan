"""Arabic normalization (SPEC §7.1). Applied to Mushaf text at indexing time and to every Arabic quote.

Follows SPEC §7.1, plus (DECISIONS D-13) the extended Arabic combining marks U+08D3–U+08FF (the King Fahd
Complex text uses open tanween U+08F0–U+08F2, which the §7.1 class misses and NON_AR would turn into a
space, splitting words) and invisible bidi / zero-width marks, which are deleted rather than spaced.
"""

from __future__ import annotations

import re

TASHKEEL = re.compile(r"[ؐ-ًؚ-ٰٟۖ-ۭ࣓-ࣿ]")
INVISIBLE = re.compile(r"[​-‏‪-‮⁦-⁩﻿]")
NON_AR = re.compile(r"[^ء-غف-ي\s]")
_HAMZA_ALEF = re.compile(r"[إأآٱ]")  # إ أ آ ٱ
_SPACES = re.compile(r"\s+")


def normalize_ar(s: str) -> str:
    s = INVISIBLE.sub("", s)
    s = TASHKEEL.sub("", s)
    s = s.replace("ـ", "")  # tatweel
    s = _HAMZA_ALEF.sub("ا", s)  # -> ا
    s = (
        s.replace("ى", "ي")  # ى -> ي
        .replace("ة", "ه")  # ة -> ه
        .replace("ؤ", "و")  # ؤ -> و
        .replace("ئ", "ي")  # ئ -> ي
    )
    s = NON_AR.sub(" ", s)  # punctuation, digits, symbols
    return _SPACES.sub(" ", s).strip()


def tokens(s: str) -> list[str]:
    """Split already-normalized text on spaces."""
    return s.split() if s else []
