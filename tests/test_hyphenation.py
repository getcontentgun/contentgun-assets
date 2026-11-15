from app.hyphenation import EDGE, MAX_UNBROKEN, MIN_WORD_LENGTH, SOFT_HYPHEN, hyphenate

COMPOUND = "Unternehmensberatungsgesellschaft"


def test_a_long_word_is_offered_break_points():
    result = hyphenate(COMPOUND)
    assert SOFT_HYPHEN in result
    assert result.replace(SOFT_HYPHEN, "") == COMPOUND


def test_every_fragment_keeps_the_minimum_edge():
    fragments = hyphenate(COMPOUND).split(SOFT_HYPHEN)
    assert len(fragments[0]) >= EDGE
    assert len(fragments[-1]) >= EDGE


def test_short_words_are_left_alone():
    text = "Der erste Schritt zählt."
    assert hyphenate(text) == text
    assert len("Schritt") < MIN_WORD_LENGTH


def test_a_short_token_is_not_touched():
    assert hyphenate("@demo.karten") == "@demo.karten"


def test_punctuation_stays_outside_the_word():
    assert hyphenate("Automatisierung.").endswith("ung.")


def test_a_run_the_dictionary_cannot_help_with_is_chopped():
    # Nothing in it is a break opportunity, so without this it would run off the card.
    fragments = hyphenate("A" * 60).split(SOFT_HYPHEN)
    assert max(len(fragment) for fragment in fragments) == MAX_UNBROKEN


def test_existing_break_opportunities_are_respected():
    assert hyphenate("Kosten-Nutzen-Analyse") == "Kosten-Nutzen-Analyse"
    assert hyphenate("https://example.com/ein/sehr/langer/pfad") == (
        "https://example.com/ein/sehr/langer/pfad"
    )


def test_empty_text_stays_empty():
    assert hyphenate("") == ""
