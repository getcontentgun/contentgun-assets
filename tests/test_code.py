import pytest

from app.code import (
    MAX_CODE_COLUMNS,
    MAX_CODE_LINES,
    CodeError,
    check_code,
    columns,
    normalize,
)


def refusal(*args, **kwargs) -> str:
    with pytest.raises(CodeError) as excinfo:
        check_code(*args, **kwargs)
    return str(excinfo.value)


def test_line_breaks_tabs_and_trailing_whitespace_are_normalized():
    assert normalize("a\r\n\tb  \rc\t") == ["a", "    b", "c"]


def test_blank_lines_at_either_end_and_the_shared_indentation_go():
    source = "\n\n    if ready:\n        go()\n\n    stop()\n   \n"
    assert normalize(source) == ["if ready:", "    go()", "", "stop()"]


def test_a_wide_character_takes_two_columns():
    assert columns("ab") == 2
    assert columns("漢字") == 4


def test_code_at_both_limits_is_accepted():
    code = check_code("\n".join(["x" * MAX_CODE_COLUMNS] * MAX_CODE_LINES))
    assert code.lines == MAX_CODE_LINES


def test_blank_code_is_refused():
    assert refusal(" \n\t\n") == "The code is blank."


def test_one_line_too_many_is_refused():
    assert refusal("\n".join(["x"] * (MAX_CODE_LINES + 1))) == (
        f"The code has {MAX_CODE_LINES + 1} lines; at most {MAX_CODE_LINES} are accepted."
    )


def test_a_line_too_wide_is_refused_with_its_number():
    assert refusal("ok\n" + "x" * (MAX_CODE_COLUMNS + 1)) == (
        f"Line 2 of the code is {MAX_CODE_COLUMNS + 1} columns wide; at most {MAX_CODE_COLUMNS} "
        "are accepted."
    )


def test_wide_characters_count_twice_against_the_width():
    check_code("漢" * (MAX_CODE_COLUMNS // 2))
    assert refusal("漢" * (MAX_CODE_COLUMNS // 2 + 1)).startswith("Line 1 of the code is")


def test_the_width_is_counted_after_the_shared_indentation_is_gone():
    check_code("\n".join(["        " + "x" * MAX_CODE_COLUMNS] * 2))


def test_lines_are_numbered_as_the_card_shows_them():
    assert refusal("\n\nok\n" + "x" * (MAX_CODE_COLUMNS + 1)).startswith("Line 2 of the code")


@pytest.mark.parametrize("character", ["\x07", "\x1b", "‮", "⁦", "​", " "])
def test_control_and_invisible_characters_are_refused(character):
    assert refusal(f"a = 1\nb = {character}2") == (
        f"Line 2 of the code contains the character U+{ord(character):04X}, which a card "
        "cannot show."
    )


def test_an_unknown_language_is_refused():
    assert refusal("x", "klingon").startswith("Unknown language 'klingon'.")


def test_without_a_language_the_code_is_plain_text():
    code = check_code("def f(): pass")
    assert code.language == "text"
    assert "tok-" not in code.html


def test_python_is_highlighted_with_the_token_classes():
    code = check_code('@cache\ndef greet(name):\n    return f"Hallo {name}"  # Gruß', " Python ")
    assert code.language == "python"
    for expected in (
        '<span class="tok-decorator">@cache</span>',
        '<span class="tok-keyword">def</span>',
        '<span class="tok-function">greet</span>',
        '<span class="tok-string">Hallo </span>',
        '<span class="tok-comment"># Gruß</span>',
    ):
        assert expected in code.html


def test_the_last_line_is_highlighted_by_a_line_based_lexer():
    html = check_code("-old\n+new", "diff").html
    assert html == (
        '<span class="line"><span class="tok-deleted">-old</span></span>'
        '<span class="line"><span class="tok-inserted">+new</span></span>'
    )


def test_markup_in_the_code_is_escaped():
    code = check_code('<script>alert("x")</script>', "html")
    assert "<script>" not in code.html
    assert "&lt;" in code.html


def test_every_line_is_a_span_and_an_empty_one_keeps_a_space():
    assert check_code("a\n\nb").html == (
        '<span class="line">a</span><span class="line"> </span><span class="line">b</span>'
    )


def test_a_token_over_several_lines_is_closed_on_every_line():
    html = check_code('text = """eins\nzwei"""', "python").html
    lines = html.split('<span class="line')[1:]
    assert len(lines) == 2
    assert "zwei" in lines[1]
    for line in lines:
        assert line.count("<span") + 1 == line.count("</span>")


def test_line_numbers_and_highlighted_lines():
    code = check_code("a\nb\nc", line_numbers=True, highlight_lines=[2, 2])
    assert code.line_numbers is True
    assert code.html == (
        '<span class="line"><span class="ln">1</span>a</span>'
        '<span class="line hl"><span class="ln">2</span>b</span>'
        '<span class="line"><span class="ln">3</span>c</span>'
    )


@pytest.mark.parametrize("number", [0, 4])
def test_a_highlighted_line_outside_the_code_is_refused(number):
    assert refusal("a\nb\nc", highlight_lines=[number]) == (
        f"highlight_lines names line {number}, but the code has 3 lines."
    )


def test_a_single_line_is_counted_in_the_singular():
    assert refusal("a", highlight_lines=[2]).endswith("but the code has 1 line.")
