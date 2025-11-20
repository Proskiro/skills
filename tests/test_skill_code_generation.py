import pytest
from unittest.mock import patch

# Import your functions here
from my_tools.tools import (
    extract_root_code,
    generate_skill_code,
    convert_isced_to_k,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_hash():
    """
    Force hash_suffix() to always return '123' for test determinism.
    """
    with patch("my_tools.tools.hash_suffix", return_value="123"):
        yield


@pytest.fixture
def empty_lookup():
    return {}


# ---------------------------------------------------------------------------
# S TREE TESTS
# ---------------------------------------------------------------------------

def test_s_root_remains_as_is(empty_lookup, mock_hash):
    uri = "http://data.europa.eu/esco/skill/S3.1"
    assert extract_root_code(uri) == "S3.1"
    assert generate_skill_code(uri, [], empty_lookup) == "S3.1"


def test_s_child_inherits_parent(empty_lookup, mock_hash):
    uri = "<uuid>"
    broader = ["http://data.europa.eu/esco/skill/S3.1"]
    code = generate_skill_code(uri, broader, empty_lookup)
    assert code == "S3.1.123"


def test_s_prefers_coded_parent(empty_lookup, mock_hash):
    uri = "<child>"
    broader = [
        "http://data.europa.eu/esco/skill/uuid1",
        "http://data.europa.eu/esco/skill/S1.3.5"
    ]
    code = generate_skill_code(uri, broader, empty_lookup)
    assert code == "S1.3.5.123"


def test_s_uses_lookup_if_present(mock_hash):
    uri = "<child>"
    parent = "<uuid-parent>"
    lookup = {parent: "S9.4.1"}
    broader = [parent]
    code = generate_skill_code(uri, broader, lookup)
    assert code == "S9.4.1.123"


# ---------------------------------------------------------------------------
# K TREE – ISCED-F TESTS
# ---------------------------------------------------------------------------

def test_k_isced_root_00(empty_lookup):
    assert convert_isced_to_k("00") == "K00"


def test_k_isced_001_becomes_K00_1(empty_lookup):
    assert convert_isced_to_k("001") == "K00.1"


def test_k_isced_0011_becomes_K00_1_1(empty_lookup):
    assert convert_isced_to_k("0011") == "K00.1.1"


def test_k_child_under_isced_parent(mock_hash, empty_lookup):
    uri = "<uuid>"
    broader = ["http://data.europa.eu/esco/isced-f/0011"]
    code = generate_skill_code(uri, broader, empty_lookup)
    assert code == "K00.1.1.123"


def test_k_prefers_isced_when_mixed(mock_hash, empty_lookup):
    uri = "<uuid>"
    broader = [
        "http://data.europa.eu/esco/isced-f/081",
        "http://data.europa.eu/esco/skill/<uuid>"
    ]
    code = generate_skill_code(uri, broader, empty_lookup)
    assert code == "K08.1.123"   # 081 → K08.1


def test_k_child_inherits_cached_parent(mock_hash):
    parent = "<uuid-parent>"
    lookup = {parent: "K08.1.482"}
    uri = "<uuid-child>"
    broader = [parent]
    code = generate_skill_code(uri, broader, lookup)
    assert code == "K08.1.482.123"


def test_k_fallback_under_k_ancestor(mock_hash):
    """
    Even if immediate parent has no code,
    but ancestor is K, child should inherit K structure.
    """
    lookup = {"<uuid-parent>": "K04.1"}
    uri = "<uuid-child>"
    broader = ["<uuid-parent>"]
    code = generate_skill_code(uri, broader, lookup)
    assert code == "K04.1.123"


# ---------------------------------------------------------------------------
# L TREE (Languages)
# ---------------------------------------------------------------------------

def test_l_root_stays_as_is(empty_lookup, mock_hash):
    uri = "http://data.europa.eu/esco/skill/L2"
    code = generate_skill_code(uri, [], empty_lookup)
    assert code == "L2"


def test_l_child_inherits_parent(empty_lookup, mock_hash):
    uri = "<uuid>"
    broader = ["http://data.europa.eu/esco/skill/L2"]
    code = generate_skill_code(uri, broader, empty_lookup)
    assert code == "L2.123"


def test_l_prefers_l_over_uuid(empty_lookup, mock_hash):
    uri = "<uuid>"
    broader = [
        "<uuid-parent>",
        "http://data.europa.eu/esco/skill/L5.1"
    ]
    code = generate_skill_code(uri, broader, empty_lookup)
    assert code == "L5.1.123"


# ---------------------------------------------------------------------------
# UUID FALLBACK TESTS
# ---------------------------------------------------------------------------

def test_uuid_root(empty_lookup, mock_hash):
    uri = "<uuid>"
    code = generate_skill_code(uri, [], empty_lookup)
    assert code.startswith("U")


def test_uuid_parent_with_no_code(empty_lookup, mock_hash):
    uri = "<child>"
    broader = ["<parent-uuid>"]
    code = generate_skill_code(uri, broader, empty_lookup)
    assert code == "U.123"


def test_uuid_parent_with_cached_code(mock_hash):
    parent = "<p>"
    lookup = {parent: "U45"}
    uri = "<child>"
    broader = [parent]
    code = generate_skill_code(uri, broader, lookup)
    assert code == "U45.123"


# ---------------------------------------------------------------------------
# MULTI-PARENT (CONFUSION) TESTS
# ---------------------------------------------------------------------------

def test_mult_parent_isced_over_s_and_uuid(mock_hash, empty_lookup):
    uri = "<uuid>"
    broader = [
        "http://data.europa.eu/esco/isced-f/041",
        "http://data.europa.eu/esco/skill/S2.1.3",
        "<uuid>"
    ]
    code = generate_skill_code(uri, broader, empty_lookup)
    assert code == "K04.1.123"   # ISCED wins → 041 → K04.1


def test_mult_parent_s_over_uuid(mock_hash, empty_lookup):
    uri = "<uuid>"
    broader = [
        "<uuid>",
        "http://data.europa.eu/esco/skill/S1.5"
    ]
    code = generate_skill_code(uri, broader, empty_lookup)
    assert code == "S1.5.123"


def test_mult_parent_two_uuid_fallback(mock_hash, empty_lookup):
    uri = "<uuid>"
    broader = ["uuid1", "uuid2"]
    code = generate_skill_code(uri, broader, empty_lookup)
    assert code.startswith("U")
