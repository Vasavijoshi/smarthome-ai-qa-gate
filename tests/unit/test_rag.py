"""Unit tests for the chunker and the retriever."""

import pytest

from assistant.config import KB_DIR
from assistant.rag import Chunk, Retriever, chunk_document, load_chunks, section_of, slugify, split_words

pytestmark = pytest.mark.unit

DOC = (
    "# Title\n\nNotice text.\n\n"
    "## Timer\nThe longest timer is 480 minutes.\n\n"
    "## Error codes\nE1 means low pressure.\n"
)


@pytest.mark.parametrize(
    "heading, slug",
    [
        ("Timer", "timer"),
        ("Error codes", "error_codes"),
        ("PIN reset by the owner", "pin_reset_by_the_owner"),
        ("Locking and unlocking", "locking_and_unlocking"),
        ("  Auto-lock ", "auto_lock"),
    ],
)
def test_slugify(heading, slug):
    assert slugify(heading) == slug


@pytest.mark.parametrize(
    "chunk_id, section",
    [
        ("vayu_f1_fan#timer", "vayu_f1_fan#timer"),
        ("vayu_f1_fan#troubleshooting/2", "vayu_f1_fan#troubleshooting"),
    ],
)
def test_section_of(chunk_id, section):
    assert section_of(chunk_id) == section


def test_short_text_is_one_window():
    words = ["w"] * 200
    assert split_words(words, 200, 40) == [words]


def test_long_text_windows_respect_size_and_overlap():
    words = [str(i) for i in range(450)]
    windows = split_words(words, 200, 40)
    assert all(len(w) <= 200 for w in windows)
    assert windows[1][:40] == windows[0][-40:]
    assert windows[-1][-1] == "449"


def test_chunk_ids_follow_document_hash_section():
    chunks = chunk_document("vayu_f1_fan", DOC, 200, 40)
    assert [c.chunk_id for c in chunks] == ["vayu_f1_fan#timer", "vayu_f1_fan#error_codes"]
    assert chunks[0].text.startswith("Timer: ")


def test_preamble_before_first_heading_is_not_indexed():
    chunks = chunk_document("doc", DOC, 200, 40)
    assert all("Notice text" not in c.text for c in chunks)


def test_split_section_gets_numbered_suffix():
    doc = "## Long\n" + " ".join(["word"] * 250)
    ids = [c.chunk_id for c in chunk_document("doc", doc, 200, 40)]
    assert ids == ["doc#long/1", "doc#long/2"]


def test_v1_chunks_are_at_most_200_words():
    chunks = load_chunks(KB_DIR, 200, 40)
    body_lengths = [len(c.text.split(": ", 1)[1].split()) for c in chunks]
    assert max(body_lengths) <= 200
    assert len({c.chunk_id for c in chunks}) == len(chunks)


def test_v3_chunking_has_fewer_chunks_than_v1():
    assert len(load_chunks(KB_DIR, 400, 40)) < len(load_chunks(KB_DIR, 200, 40))


def test_tfidf_retriever_ranks_matching_chunk_first():
    chunks = [
        Chunk("a#timer", "Timer: the longest timer is 480 minutes"),
        Chunk("b#jars", "Jars: three steel jars"),
    ]
    results = Retriever(chunks, "tfidf").search("how long can the timer run", k=2)
    assert results[0].chunk_id == "a#timer"
    assert results[0].score > results[1].score


def test_retriever_returns_k_results():
    chunks = load_chunks(KB_DIR, 200, 40)
    assert len(Retriever(chunks, "tfidf").search("fan timer", k=3)) == 3


def test_unknown_retriever_kind_raises():
    with pytest.raises(ValueError):
        Retriever([Chunk("a#b", "text")], "bm25")
