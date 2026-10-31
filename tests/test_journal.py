import pytest

from vwmcp.journal import Journal, JournalError


@pytest.fixture
def j(tmp_path):
    return Journal(tmp_path / "journal.jsonl")


def test_pop_returns_most_recent_entry_for_document(j):
    j.record("Plot.vwx", "vw_set_record", [{"op": "fields", "uuid": "a", "record": "R", "before": {"x": "1"}}])
    j.record("Plot.vwx", "vw_insert_symbol", [{"op": "created", "uuid": "b"}])
    assert j.pop("Plot.vwx")["tool"] == "vw_insert_symbol"
    assert j.pop("Plot.vwx")["tool"] == "vw_set_record"
    with pytest.raises(JournalError, match="nothing to revert"):
        j.pop("Plot.vwx")


def test_entries_are_scoped_to_their_document(j):
    j.record("Other.vwx", "vw_set_record", [{"op": "fields", "uuid": "a", "record": "R", "before": {}}])
    with pytest.raises(JournalError, match="nothing to revert"):
        j.pop("Plot.vwx")
    assert j.pop("Other.vwx")["document"] == "Other.vwx"


def test_raw_python_is_a_barrier(j):
    j.record("Plot.vwx", "vw_set_record", [{"op": "fields", "uuid": "a", "record": "R", "before": {}}])
    j.record("Plot.vwx", "vw_exec_python", None)
    with pytest.raises(JournalError, match="vw_exec_python"):
        j.pop("Plot.vwx")
    # The barrier is consumed so the user isn't stuck behind it forever.
    assert j.pop("Plot.vwx")["tool"] == "vw_set_record"


def test_journal_survives_restart(tmp_path):
    Journal(tmp_path / "j.jsonl").record("P.vwx", "vw_set_record", [{"op": "created", "uuid": "z"}])
    assert Journal(tmp_path / "j.jsonl").pop("P.vwx")["ops"] == [{"op": "created", "uuid": "z"}]


def test_peek_does_not_consume(j):
    j.record("P.vwx", "vw_insert_symbol", [{"op": "created", "uuid": "z"}])
    assert j.peek("P.vwx")["tool"] == "vw_insert_symbol"
    assert j.pop("P.vwx")["tool"] == "vw_insert_symbol"


def test_journal_drops_the_oldest_entries_when_it_gets_too_big(tmp_path, monkeypatch):
    from vwmcp import journal as J

    monkeypatch.setattr(J, "MAX_BYTES", 2000)
    j = J.Journal(tmp_path / "j.jsonl")
    for i in range(5):
        j.record("D.vwx", "t%d" % i, [{"op": "created", "uuid": "x" * 600}])
    entries = j._load()
    assert 1 <= len(entries) < 5 and entries[-1]["tool"] == "t4"
    j.record("D.vwx", "big", [{"op": "created", "uuid": "y" * 5000}])
    assert [e["tool"] for e in j._load()] == ["big"]  # the newest always stays
