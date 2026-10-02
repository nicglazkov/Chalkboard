import json

from pipeline.partial_json import extract_string_field


def test_field_not_started_is_none():
    assert extract_string_field("", "script") is None
    assert extract_string_field('{"title": "Hi", "scr', "script") is None
    assert extract_string_field('{"title": "Hi", "script"', "script") is None
    assert extract_string_field('{"title": "Hi", "script": ', "script") is None


def test_growing_value():
    assert extract_string_field('{"script": "', "script") == ""
    assert extract_string_field('{"script": "Hello wor', "script") == "Hello wor"
    assert extract_string_field('{"script": "Hello world", "x": 1}', "script") == "Hello world"


def test_escapes_decoded_and_cut_escape_withheld():
    assert extract_string_field(r'{"script": "a\nb\t\"c\" \\ \/', "script") == 'a\nb\t"c" \\ /'
    assert extract_string_field('{"script": "line\\', "script") == "line"
    assert extract_string_field('{"script": "x\\u00', "script") == "x"
    assert extract_string_field('{"script": "x\\u00e9y', "script") == "xéy"


def test_surrogate_pair():
    full = json.dumps({"script": "math \U0001d4b3 ok"})  # ensure_ascii -> 𝒳
    assert "\\ud835" in full
    assert extract_string_field(full, "script") == "math \U0001d4b3 ok"
    cut = full[: full.index("\\udcb3") + 3]  # low half incomplete
    assert extract_string_field(cut, "script") == "math "


def test_key_inside_other_value_does_not_match():
    doc = '{"title": "the \\"script\\": trick", "segments": [{"text": "\\"script\\": no"}], "script": "real'
    assert extract_string_field(doc, "script") == "real"


def test_skips_nested_values_before_field():
    doc = '{"segments": [{"text": "a", "n": 1}, {"text": "b}"}], "flag": true, "script": "go'
    assert extract_string_field(doc, "script") == "go"


def test_incomplete_nested_value_before_field():
    assert extract_string_field('{"segments": [{"text": "a"', "script") is None


def test_non_string_field_is_none():
    assert extract_string_field('{"script": 12', "script") is None


def test_matches_json_loads_on_every_prefix():
    value = 'Say "hi"\n\tthen \\ and é and \U0001F600 end'
    doc = json.dumps({"verdict": "approved", "feedback": value})
    prev = ""
    for i in range(len(doc) + 1):
        got = extract_string_field(doc[:i], "feedback")
        if got is None:
            assert prev == ""
            continue
        assert value.startswith(got)
        assert len(got) >= len(prev)
        prev = got
    assert prev == value
