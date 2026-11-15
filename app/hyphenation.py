import re
from functools import lru_cache

import pyphen

LANGUAGE = "de_DE"
SOFT_HYPHEN = "­"

# Three letters on each side of a break is what German typography expects, and it is what
# keeps short words from being split at all.
EDGE = 3
MIN_WORD_LENGTH = 10

# Letters only: the dictionary has nothing to say about a handle, a URL or a number.
WORD = re.compile(rf"[^\W\d_]{{{MIN_WORD_LENGTH},}}")

# Longest run the renderer may be handed without a single break opportunity in it. The
# CSS cannot be the safety net here: `overflow-wrap: break-word` lets the browser break
# after any letter, and it then prefers the fuller line over the offered break point, so
# every hyphen below would be ignored.
MAX_UNBROKEN = 12

# A run with nothing to break at: the characters excluded here are the ones a browser
# already treats as break opportunities.
UNBREAKABLE = re.compile(rf"[^\s\-/{SOFT_HYPHEN}\u2013\u2014]{{{MAX_UNBROKEN + 1},}}")


@lru_cache(maxsize=1)
def _dictionary() -> pyphen.Pyphen:
    return pyphen.Pyphen(lang=LANGUAGE, left=EDGE, right=EDGE)


def _chunk(match: re.Match[str]) -> str:
    run = match.group()
    return SOFT_HYPHEN.join(run[at : at + MAX_UNBROKEN] for at in range(0, len(run), MAX_UNBROKEN))


def hyphenate(text: str) -> str:
    """Offer break points as soft hyphens. They are invisible until a line actually breaks
    there, and they are what makes the result identical on every host: Chromium's own
    `hyphens: auto` needs dictionaries that the Linux headless shell does not ship.

    The dictionary goes first; whatever is still one long unbreakable run afterwards, a
    nonsense word or a URL, is chopped so that nothing can run off the card."""
    if not text:
        return text
    dictionary = _dictionary()
    hyphenated = WORD.sub(
        lambda match: dictionary.inserted(match.group(), hyphen=SOFT_HYPHEN), text
    )
    return UNBREAKABLE.sub(_chunk, hyphenated)
