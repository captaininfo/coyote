"""
Unit tests for coyote_embedder.embed_document / embed_document_with_text /
get_model (Unit 3b of the 0.5 refactor).

Stub-model tests run anywhere (no sentence-transformers). The Gate 3.2
fixture test needs the real model and is skip-marked: it requires the
sentence_transformers package AND SENTENCE_TRANSFORMERS_HOME (baked into
the core container, unset on hosts) so it can never trigger a surprise
model download on a host run. In-container:
    docker run --rm --network host -e HF_HUB_OFFLINE=1 ... pytest
"""
import math
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(
    0, str(Path(__file__).parent.parent / "images" / "core" / "core_analysis")
)

import coyote.coyote_embedder as embedder  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "long_tutorial_article.txt"


def normalize(vec):
    n = math.sqrt(sum(x * x for x in vec))
    return [x / n for x in vec]


class StubModel:
    """Deterministic stand-in: word-count tokenizer, scripted vectors."""

    def __init__(self, vectors, max_seq_length=8):
        self.vectors = vectors
        self.max_seq_length = max_seq_length
        self.encode_calls = []

    def tokenizer(self, text, add_special_tokens=False, verbose=True):
        return {"input_ids": text.split()}

    def encode(self, texts, convert_to_numpy=True):
        self.encode_calls.append(list(texts))
        assert len(texts) <= len(self.vectors), "stub got more chunks than scripted"
        return self.vectors[: len(texts)]


@pytest.fixture
def use_stub(monkeypatch):
    def install(stub):
        monkeypatch.setattr(embedder, "_model", stub)
        monkeypatch.setattr(embedder, "_model_load_failed", False)
        return stub
    return install


class TestEmbedDocument:
    def test_empty_text_returns_none(self, use_stub):
        use_stub(StubModel([[1.0, 0.0]]))
        assert embedder.embed_document("") is None
        assert embedder.embed_document("   \n\n ") is None

    def test_model_unavailable_returns_none(self, monkeypatch):
        monkeypatch.setattr(embedder, "_model", None)
        monkeypatch.setattr(embedder, "_model_load_failed", True)
        assert embedder.embed_document("some text") is None

    def test_single_chunk_returns_normalized_vector(self, use_stub):
        use_stub(StubModel([[3.0, 4.0, 0.0]]))
        result = embedder.embed_document("short text")
        assert result == pytest.approx([0.6, 0.8, 0.0])

    def test_multi_chunk_pooling_math(self, use_stub):
        # max_seq_length=8 -> budget 6; two 4-word paragraphs don't pack.
        stub = use_stub(StubModel([[3.0, 4.0, 0.0], [0.0, 0.0, 2.0]]))
        text = "one two three four\n\nfive six seven eight"
        result = embedder.embed_document(text)
        assert len(stub.encode_calls) == 1 and len(stub.encode_calls[0]) == 2
        # normalized chunks [0.6, 0.8, 0] and [0, 0, 1];
        # mean [0.3, 0.4, 0.5] renormalized.
        assert result == pytest.approx(normalize([0.3, 0.4, 0.5]))

    def test_result_is_unit_norm(self, use_stub):
        use_stub(StubModel([[5.0, 0.0], [0.0, 0.1]]))
        result = embedder.embed_document("one two three four\n\nfive six seven eight")
        assert math.sqrt(sum(x * x for x in result)) == pytest.approx(1.0)

    def test_zero_vector_chunk_returns_none_not_nan(self, use_stub):
        # A zero-norm pool has no direction: graceful handling is None
        # (the null-embedding signal), never NaN.
        use_stub(StubModel([[0.0, 0.0, 0.0]]))
        assert embedder.embed_document("some text") is None

    def test_canceling_chunks_return_none(self, use_stub):
        use_stub(StubModel([[1.0, 0.0], [-1.0, 0.0]]))
        text = "one two three four\n\nfive six seven eight"
        assert embedder.embed_document(text) is None

    def test_encode_failure_returns_none(self, use_stub):
        stub = use_stub(StubModel([[1.0, 0.0]]))
        stub.encode = lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("boom"))
        assert embedder.embed_document("some text") is None


class TestMaxChunksCap:
    def test_cap_pools_first_chunks_and_warns(self, use_stub, monkeypatch, caplog):
        monkeypatch.setattr(embedder, "MAX_CHUNKS", 2)
        stub = use_stub(StubModel([[1.0, 0.0], [0.0, 1.0]]))
        text = "\n\n".join(f"p{i}a p{i}b p{i}c p{i}d" for i in range(4))  # 4 chunks
        with caplog.at_level("WARNING"):
            result = embedder.embed_document_with_text(text)
        assert result is not None
        embedding, embedded_text = result
        assert len(stub.encode_calls[0]) == 2  # only the first 2 encoded
        assert embedded_text == "p0a p0b p0c p0d\n\np1a p1b p1c p1d"
        assert "MAX_CHUNKS" in caplog.text

    def test_no_truncation_returns_input_verbatim(self, use_stub):
        use_stub(StubModel([[1.0, 0.0], [0.0, 1.0]]))
        text = "one two three four\n\nfive six seven eight"
        _, embedded_text = embedder.embed_document_with_text(text)
        assert embedded_text == text


class TestGetModel:
    def test_returns_singleton(self, use_stub):
        stub = use_stub(StubModel([[1.0]]))
        assert embedder.get_model() is stub

    def test_returns_none_on_load_failure(self, monkeypatch):
        monkeypatch.setattr(embedder, "_model", None)
        monkeypatch.setattr(embedder, "_model_load_failed", True)
        assert embedder.get_model() is None


@pytest.mark.skipif(
    not os.getenv("SENTENCE_TRANSFORMERS_HOME"),
    reason="real-model test; needs the container's baked model "
           "(SENTENCE_TRANSFORMERS_HOME unset)",
)
class TestGate32Fixture:
    """Gate 3.2: pooled embedding differs from first-chunk-only embedding
    on the committed long-tutorial fixture. Threshold 0.90 set empirically
    by pre-flight 5 (2026-06-11). Re-run on Unit 10 embedder swaps."""

    def test_pooled_differs_from_first_chunk(self):
        pytest.importorskip("sentence_transformers")
        from coyote.analysis.nlp.chunking import chunk_text

        model = embedder.get_model()
        if model is None:
            pytest.skip("embedding model unavailable")
        text = FIXTURE.read_text()

        def count_tokens(t):
            return len(model.tokenizer(t, add_special_tokens=False)["input_ids"])

        chunks = chunk_text(
            text, max_tokens=model.max_seq_length - 2, count_tokens=count_tokens
        )
        assert len(chunks) >= 5, "fixture must be long enough to multi-chunk"

        pooled = embedder.embed_document(text)
        assert pooled is not None
        first = normalize([float(x) for x in
                           model.encode(chunks[0], convert_to_numpy=True)])
        cosine = sum(a * b for a, b in zip(pooled, first))
        assert cosine < 0.90, f"pooling effect not measurable: cosine={cosine:.4f}"
