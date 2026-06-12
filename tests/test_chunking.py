"""
Unit tests for coyote.analysis.nlp.chunking (Unit 3a of the 0.5 refactor).

Pure tests — no model, no spaCy. max_tokens is passed explicitly except in
the embedder-default tests, which monkeypatch the model accessor.
"""
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(
    0, str(Path(__file__).parent.parent / "images" / "core" / "core_analysis")
)

from coyote.analysis.nlp.chunking import chunk_text  # noqa: E402


def words_of(chunks):
    return [w for c in chunks for w in c.split()]


class TestEmptyAndTrivialInput:
    def test_empty_string_returns_no_chunks(self):
        assert chunk_text("", max_tokens=10) == []

    def test_whitespace_only_returns_no_chunks(self):
        assert chunk_text("  \n\n \t \n ", max_tokens=10) == []

    def test_short_text_single_chunk(self):
        assert chunk_text("Hello world.", max_tokens=10) == ["Hello world."]

    def test_whitespace_padding_stripped(self):
        assert chunk_text("\n\n  word  \n\n", max_tokens=10) == ["word"]


class TestParagraphPacking:
    def test_small_paragraphs_pack_into_one_chunk(self):
        text = "one two three\n\nfour five six"
        chunks = chunk_text(text, max_tokens=10)
        assert chunks == ["one two three\n\nfour five six"]

    def test_paragraphs_not_split_mid_paragraph(self):
        # 6 + 6 words against a budget of 10: each paragraph must stay
        # intact in its own chunk rather than splitting mid-paragraph.
        p1 = "a b c d e f"
        p2 = "g h i j k l"
        chunks = chunk_text(f"{p1}\n\n{p2}", max_tokens=10)
        assert chunks == [p1, p2]

    def test_all_chunks_within_budget(self):
        text = "\n\n".join(f"word{i} word{i} word{i}" for i in range(20))
        chunks = chunk_text(text, max_tokens=7)
        assert chunks
        assert all(len(c.split()) <= 7 for c in chunks)

    def test_content_preserved_across_chunks(self):
        text = "\n\n".join(f"alpha{i} beta{i}" for i in range(10))
        chunks = chunk_text(text, max_tokens=5)
        assert words_of(chunks) == text.split()

    def test_no_empty_chunks(self):
        text = "para one\n\n   \n\npara two\n\n\n\npara three"
        chunks = chunk_text(text, max_tokens=3)
        assert all(c.strip() for c in chunks)


class TestSentenceFallback:
    def test_oversized_paragraph_splits_on_sentences(self):
        # One 12-word paragraph, budget 9: falls back to 4-word sentences.
        para = "one two three four. five six seven eight. nine ten eleven twelve."
        chunks = chunk_text(para, max_tokens=9)
        assert len(chunks) == 2
        assert all(len(c.split()) <= 9 for c in chunks)
        assert sorted(words_of(chunks)) == sorted(para.split())

    def test_sentences_kept_intact_when_they_fit(self):
        para = "one two three four. five six seven eight."
        chunks = chunk_text(para, max_tokens=5)
        assert chunks[0].startswith("one two three four.")
        assert "five six seven eight." in chunks[1]


class TestHardSplit:
    def test_oversized_sentence_hard_splits_in_order(self):
        sentence = " ".join(f"w{i}" for i in range(30))  # no punctuation
        chunks = chunk_text(sentence, max_tokens=8)
        assert all(len(c.split()) <= 8 for c in chunks)
        assert words_of(chunks) == sentence.split()

    def test_giant_single_word_bisected(self):
        char_counter = len  # every character is a token
        chunks = chunk_text("x" * 100, max_tokens=10, count_tokens=char_counter)
        assert all(len(c) <= 10 for c in chunks)
        assert "".join(chunks) == "x" * 100

    def test_pathological_counter_terminates(self):
        # A counter that never fits still terminates via length-1 pieces.
        always_over = lambda t: 99  # noqa: E731
        chunks = chunk_text("ab cd", max_tokens=1, count_tokens=always_over)
        assert chunks
        assert "".join(words_of(chunks)) == "abcd"


class TestCountTokensInjection:
    def test_custom_counter_changes_packing(self):
        text = "aaaa bb\n\ncc dd"
        by_words = chunk_text(text, max_tokens=4)
        by_chars = chunk_text(text, max_tokens=7, count_tokens=len)
        assert by_words == ["aaaa bb\n\ncc dd"]  # 2 + 2 words, packs whole
        assert by_chars == ["aaaa bb", "cc dd"]  # 7 + 5 chars, splits


class TestArguments:
    def test_unknown_boundary_raises(self):
        with pytest.raises(ValueError, match="boundary"):
            chunk_text("text", max_tokens=10, boundary="semantic")

    def test_nonpositive_max_tokens_raises(self):
        with pytest.raises(ValueError, match="max_tokens"):
            chunk_text("text", max_tokens=0)


class TestEmbedderDefault:
    def test_max_tokens_derived_from_model(self, monkeypatch):
        import coyote.coyote_embedder as embedder

        stub = types.SimpleNamespace(max_seq_length=12)
        monkeypatch.setattr(embedder, "_get_model", lambda: stub)
        sentence = " ".join(f"w{i}" for i in range(11))  # 11 > 12 - 2
        chunks = chunk_text(sentence)
        assert len(chunks) == 2
        assert all(len(c.split()) <= 10 for c in chunks)

    def test_model_unavailable_raises(self, monkeypatch):
        import coyote.coyote_embedder as embedder

        monkeypatch.setattr(embedder, "_get_model", lambda: None)
        with pytest.raises(RuntimeError, match="max_tokens"):
            chunk_text("text")
