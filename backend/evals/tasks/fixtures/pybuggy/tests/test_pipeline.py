from pybuggy.pipeline import normalize_records


def test_normalize_records_skips_empty_values():
    assert normalize_records(["1", "", "2", "  ", "3"]) == [1, 2, 3]
