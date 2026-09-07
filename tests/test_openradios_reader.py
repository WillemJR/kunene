import pytest
from pathlib import Path
from kunene.util.openradios_reader import OpenRadiosKeywordReader

SAMPLE_KEYWORD_FILE = """\
#RADIOSS STARTER
#---1----|----2----|----3----|----4----|----5----|----6----|----7----|----8----|
/PARAMETER/GLOBAL/REAL/2
Molar mass of inflating gas
MW                        .025
#---1----|----2----|----3----|----4----|----5----|----6----|----7----|----8----|
/PARAMETER/GLOBAL/REAL/3
Cp heat constant molar
CPM                         13
#---1----|----2----|----3----|----4----|----5----|----6----|----7----|----8----|
/PARAMETER/GLOBAL/REAL_EXPR/4
Cp heat constant mass
CP        CPM/MW
#---1----|----2----|----3----|----4----|----5----|----6----|----7----|----8----|
/PARAMETER/GLOBAL/INTEGER/8
surf part for airbag
s_part             4
#---1----|----2----|----3----|----4----|----5----|----6----|----7----|----8----|
/PARAMETER/GLOBAL/TEXT/1
Update output
Name       12
EXAMPLE_TEXT
#---1----|----2----|----3----|----4----|----5----|----6----|----7----|----8----|
/PARAMETER/GLOBAL/TEXT/3
Rotation axe X
RotX               5
XX
#---1----|----2----|----3----|----4----|----5----|----6----|----7----|----8----|
/END
"""


@pytest.fixture()
def keyword_file(tmp_path):
    p = tmp_path / "test.k"
    p.write_text(SAMPLE_KEYWORD_FILE)
    return p


# ---------------------------------------------------------------------------
# reading
# ---------------------------------------------------------------------------

def test_parameters_count(keyword_file):
    with OpenRadiosKeywordReader(keyword_file) as okr:
        params = okr.parameters()
    assert len(params) == 6


def test_parameters_types(keyword_file):
    with OpenRadiosKeywordReader(keyword_file) as okr:
        params = okr.parameters()

    assert params["MW"][0] == "REAL"
    assert params["CPM"][0] == "REAL"
    assert params["CP"][0] == "REAL_EXPR"
    assert params["s_part"][0] == "INTEGER"
    assert params["Name"][0] == "TEXT"
    assert params["RotX"][0] == "TEXT"


def test_parameters_values(keyword_file):
    with OpenRadiosKeywordReader(keyword_file) as okr:
        params = okr.parameters()

    assert params["MW"][1] == pytest.approx(0.025)
    assert params["CPM"][1] == pytest.approx(13.0)
    assert params["CP"][1] == "CPM/MW"
    assert params["s_part"][1] == 4
    assert params["Name"][1] == "EXAMPLE_TEXT"
    assert params["RotX"][1] == "XX"


def test_real_value_is_float(keyword_file):
    with OpenRadiosKeywordReader(keyword_file) as okr:
        params = okr.parameters()
    assert isinstance(params["MW"][1], float)
    assert isinstance(params["CPM"][1], float)


def test_integer_value_is_int(keyword_file):
    with OpenRadiosKeywordReader(keyword_file) as okr:
        params = okr.parameters()
    assert isinstance(params["s_part"][1], int)


# ---------------------------------------------------------------------------
# set_parameters
# ---------------------------------------------------------------------------

def test_set_real(keyword_file):
    with OpenRadiosKeywordReader(keyword_file) as okr:
        okr.set_parameters({"MW": 0.028})
        assert okr.parameters()["MW"][1] == 0.028


def test_set_integer(keyword_file):
    with OpenRadiosKeywordReader(keyword_file) as okr:
        okr.set_parameters({"s_part": 7})
        assert okr.parameters()["s_part"][1] == 7


def test_set_real_expr(keyword_file):
    with OpenRadiosKeywordReader(keyword_file) as okr:
        okr.set_parameters({"CP": "CPM/MW * 1.1"})
        assert okr.parameters()["CP"][1] == "CPM/MW * 1.1"


def test_set_text(keyword_file):
    with OpenRadiosKeywordReader(keyword_file) as okr:
        okr.set_parameters({"Name": "NEW_TEXT"})
        assert okr.parameters()["Name"][1] == "NEW_TEXT"


def test_set_case_insensitive(keyword_file):
    with OpenRadiosKeywordReader(keyword_file) as okr:
        okr.set_parameters({"mw": 0.032})
        assert okr.parameters()["MW"][1] == 0.032


def test_set_unknown_name_ignored(keyword_file):
    with OpenRadiosKeywordReader(keyword_file) as okr:
        params_before = okr.parameters().copy()
        okr.set_parameters({"NONEXISTENT": 1.0})
        assert okr.parameters() == params_before


# ---------------------------------------------------------------------------
# write
# ---------------------------------------------------------------------------

def test_write_preserves_non_parameter_lines(keyword_file, tmp_path):
    out = tmp_path / "out.k"
    with OpenRadiosKeywordReader(keyword_file) as okr:
        okr.write(str(out))
    assert "#RADIOSS STARTER" in out.read_text()
    assert "/END" in out.read_text()


def test_write_updates_real_value(keyword_file, tmp_path):
    out = tmp_path / "out.k"
    with OpenRadiosKeywordReader(keyword_file) as okr:
        okr.set_parameters({"MW": 0.028})
        okr.write(str(out))
    assert "0.028" in out.read_text()


def test_write_updates_integer_value(keyword_file, tmp_path):
    out = tmp_path / "out.k"
    with OpenRadiosKeywordReader(keyword_file) as okr:
        okr.set_parameters({"s_part": 9})
        okr.write(str(out))
    assert "9" in out.read_text()


def test_write_updates_expr_value(keyword_file, tmp_path):
    out = tmp_path / "out.k"
    with OpenRadiosKeywordReader(keyword_file) as okr:
        okr.set_parameters({"CP": "CPM/MW * 2"})
        okr.write(str(out))
    assert "CPM/MW * 2" in out.read_text()


def test_write_updates_text_value(keyword_file, tmp_path):
    out = tmp_path / "out.k"
    with OpenRadiosKeywordReader(keyword_file) as okr:
        okr.set_parameters({"Name": "UPDATED"})
        okr.write(str(out))
    content = out.read_text()
    assert "UPDATED" in content
    assert "EXAMPLE_TEXT" not in content


def test_write_name_column_preserved(keyword_file, tmp_path):
    out = tmp_path / "out.k"
    with OpenRadiosKeywordReader(keyword_file) as okr:
        okr.set_parameters({"MW": 0.028})
        okr.write(str(out))
    # Name column must still be present on the value line
    lines = out.read_text().splitlines()
    mw_line = next(l for l in lines if l.startswith("MW"))
    assert mw_line.startswith("MW")
    assert "0.028" in mw_line


def test_roundtrip_unmodified(keyword_file, tmp_path):
    out = tmp_path / "out.k"
    with OpenRadiosKeywordReader(keyword_file) as okr:
        okr.write(str(out))
    assert out.read_text() == keyword_file.read_text()


# ---------------------------------------------------------------------------
# Example-style test — mirrors the usage pattern from todo.radioss
# ---------------------------------------------------------------------------

EXAMPLE_KEYWORD_FILE = """\
#RADIOSS STARTER
#---1----|----2----|----3----|----4----|----5----|----6----|----7----|----8----|
/PARAMETER/GLOBAL/REAL/1
Termination time
Term                      1.0
#---1----|----2----|----3----|----4----|----5----|----6----|----7----|----8----|
/PARAMETER/GLOBAL/INTEGER/2
Number of output states
States             50
#---1----|----2----|----3----|----4----|----5----|----6----|----7----|----8----|
/PARAMETER/GLOBAL/TEXT/3
Second parameter
Par2       10
foo
#---1----|----2----|----3----|----4----|----5----|----6----|----7----|----8----|
/PARAMETER/GLOBAL/REAL_EXPR/4
Plot frequency expression
Plot      Term/states
#---1----|----2----|----3----|----4----|----5----|----6----|----7----|----8----|
/END
"""


@pytest.fixture()
def example_keyword_file(tmp_path):
    p = tmp_path / "example.k"
    p.write_text(EXAMPLE_KEYWORD_FILE)
    return p


def test_example_read_set_write(example_keyword_file, tmp_path):
    """Full workflow matching the usage example in todo.radioss."""
    output_file = tmp_path / "example_out.k"

    with OpenRadiosKeywordReader(example_keyword_file) as okr:
        # Read existing parameters
        params = okr.parameters()
        assert set(params.keys()) == {"Term", "States", "Par2", "Plot"}
        assert params["Term"]   == ("REAL",      pytest.approx(1.0))
        assert params["States"] == ("INTEGER",   50)
        assert params["Par2"]   == ("TEXT",      "foo")
        assert params["Plot"]   == ("REAL_EXPR", "Term/states")

        # Perform the updates from the example
        parameters_to_change = {
            "Term":   0.5,
            "States": 100,
            "Par2":   "baz",
            "Plot":   "TerM/(states-50) * 2.0",
        }
        okr.set_parameters(parameters_to_change)

        # Verify updated values in memory
        params_updated = okr.parameters()
        assert params_updated["Term"][1]   == pytest.approx(0.5)
        assert params_updated["States"][1] == 100
        assert params_updated["Par2"][1]   == "baz"
        assert params_updated["Plot"][1]   == "TerM/(states-50) * 2.0"

        # Write and verify the file on disk
        okr.write(str(output_file))

    content = output_file.read_text()
    assert "0.5"                   in content
    assert "100"                   in content
    assert "baz"                   in content
    assert "TerM/(states-50) * 2.0" in content
    # Original values must be gone
    assert "1.0"       not in content
    assert "foo"       not in content
    assert "Term/states" not in content
    # Non-parameter lines must be preserved
    assert "#RADIOSS STARTER" in content
    assert "/END"             in content


# ---------------------------------------------------------------------------
# Fix 1 — INT_EXPR type support
# ---------------------------------------------------------------------------

INT_EXPR_FILE = """\
#RADIOSS STARTER
/PARAMETER/GLOBAL/INTEGER/1
base count
BASE               10
/PARAMETER/GLOBAL/INT_EXPR/2
doubled count
DOUBLE    BASE*2
/END
"""


@pytest.fixture()
def int_expr_file(tmp_path):
    p = tmp_path / "int_expr.k"
    p.write_text(INT_EXPR_FILE)
    return p


def test_int_expr_parsed(int_expr_file):
    with OpenRadiosKeywordReader(int_expr_file) as okr:
        params = okr.parameters()
    assert "DOUBLE" in params
    assert params["DOUBLE"][0] == "INT_EXPR"
    assert params["DOUBLE"][1] == "BASE*2"


def test_int_expr_set_and_write(int_expr_file, tmp_path):
    out = tmp_path / "out.k"
    with OpenRadiosKeywordReader(int_expr_file) as okr:
        okr.set_parameters({"DOUBLE": "BASE*3"})
        okr.write(str(out))
    content = out.read_text()
    assert "BASE*3" in content
    assert "BASE*2" not in content


# ---------------------------------------------------------------------------
# Fix 2 — TEXT: significant leading whitespace preserved
# ---------------------------------------------------------------------------

TEXT_WHITESPACE_FILE = """\
/PARAMETER/GLOBAL/TEXT/3
Rotation axe X
RotX               5
   XX
/END
"""


@pytest.fixture()
def text_whitespace_file(tmp_path):
    p = tmp_path / "text_ws.k"
    p.write_text(TEXT_WHITESPACE_FILE)
    return p


def test_text_leading_whitespace_preserved(text_whitespace_file):
    with OpenRadiosKeywordReader(text_whitespace_file) as okr:
        params = okr.parameters()
    assert params["RotX"][1] == "   XX"


def test_text_roundtrip_preserves_whitespace(text_whitespace_file, tmp_path):
    out = tmp_path / "out.k"
    with OpenRadiosKeywordReader(text_whitespace_file) as okr:
        okr.write(str(out))
    assert "   XX" in out.read_text()


# ---------------------------------------------------------------------------
# Fix 3 — TEXT: Length field stored
# ---------------------------------------------------------------------------

def test_text_length_stored(text_whitespace_file):
    with OpenRadiosKeywordReader(text_whitespace_file) as okr:
        block = next(b for b in okr._blocks if b.name == "RotX")
    assert block.text_length == 5


def test_text_length_zero_when_absent(tmp_path):
    # TEXT parameter with no Length field (Length defaults to 0 = full line)
    content = "/PARAMETER/GLOBAL/TEXT/1\ntitle\nMyVar\nsome text\n/END\n"
    p = tmp_path / "nolen.k"
    p.write_text(content)
    with OpenRadiosKeywordReader(p) as okr:
        block = next(b for b in okr._blocks if b.name == "MyVar")
    assert block.text_length == 0


# ---------------------------------------------------------------------------
# Fix 4 — Multi-line REAL_EXPR / INT_EXPR expressions
# ---------------------------------------------------------------------------

MULTILINE_EXPR_FILE = """\
#RADIOSS STARTER
/PARAMETER/GLOBAL/REAL/1
base value A
A                  2.0
/PARAMETER/GLOBAL/REAL/2
base value B
B                  3.0
/PARAMETER/GLOBAL/REAL_EXPR/3
complex expression
RESULT    A*B
+A/B
/END
"""


@pytest.fixture()
def multiline_expr_file(tmp_path):
    p = tmp_path / "multiline.k"
    p.write_text(MULTILINE_EXPR_FILE)
    return p


def test_multiline_expr_parsed(multiline_expr_file):
    with OpenRadiosKeywordReader(multiline_expr_file) as okr:
        params = okr.parameters()
    assert params["RESULT"][0] == "REAL_EXPR"
    assert "A*B" in params["RESULT"][1]
    assert "+A/B" in params["RESULT"][1]


def test_multiline_expr_set_clears_continuations(multiline_expr_file, tmp_path):
    out = tmp_path / "out.k"
    with OpenRadiosKeywordReader(multiline_expr_file) as okr:
        okr.set_parameters({"RESULT": "A+B"})
        okr.write(str(out))
    content = out.read_text()
    assert "A+B" in content
    # Continuation line must be gone (replaced by blank line)
    assert "+A/B" not in content
