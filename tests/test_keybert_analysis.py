"""
Unit tests for coyote.analysis.nlp.keybert_analysis (Unit 3c of the 0.5
refactor). Pure host tests — keybert/sklearn/numpy/spaCy are stubbed.
"""
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(
    0, str(Path(__file__).parent.parent / "images" / "core" / "core_analysis")
)

from coyote.analysis.nlp import keybert_analysis as ka  # noqa: E402


# --- spaCy stand-ins --------------------------------------------------------

class Tok:
    def __init__(self, text, pos="NOUN", alpha=True):
        self.lower_ = text.lower()
        self.pos_ = pos
        self.is_alpha = alpha


class Chunk:
    def __init__(self, *tokens):
        self._tokens = tokens

    def __iter__(self):
        return iter(self._tokens)


class StubDoc:
    def __init__(self, *chunks):
        self.noun_chunks = chunks


class StubNLP:
    def __init__(self, doc, max_length=1_000_000):
        self._doc = doc
        self.max_length = max_length
        self.calls = []

    def __call__(self, text):
        self.calls.append(text)
        return self._doc


class StubKeyBERT:
    def __init__(self, results=()):
        self.results = list(results)
        self.calls = []

    def extract_keywords(self, text, **kwargs):
        self.calls.append((text, kwargs))
        return self.results


@pytest.fixture(autouse=True)
def reset_singleton():
    ka._reset_for_tests()
    yield
    ka._reset_for_tests()


@pytest.fixture
def stub_ml(monkeypatch):
    """Inject numpy + sklearn CountVectorizer stand-ins."""
    np_stub = MagicMock()
    reshaped = object()
    np_stub.asarray.return_value.reshape.return_value = reshaped
    monkeypatch.setitem(sys.modules, "numpy", np_stub)

    class StubCV:
        def __init__(self, analyzer):
            self.analyzer = analyzer

    sk_text = types.SimpleNamespace(CountVectorizer=StubCV)
    monkeypatch.setitem(sys.modules, "sklearn", MagicMock())
    monkeypatch.setitem(sys.modules, "sklearn.feature_extraction", MagicMock())
    monkeypatch.setitem(sys.modules, "sklearn.feature_extraction.text", sk_text)
    return types.SimpleNamespace(np=np_stub, reshaped=reshaped)


def install_keybert(monkeypatch, results=()):
    stub = StubKeyBERT(results)
    monkeypatch.setattr(ka, "_keybert", stub)
    return stub


DOC = StubDoc(Chunk(Tok("open"), Tok("education")), Chunk(Tok("library")))


class TestUnavailabilityPaths:
    def test_empty_text_returns_empty(self):
        assert ka.extract_keywords("", [0.1], StubNLP(DOC)) == []
        assert ka.extract_keywords("   ", [0.1], StubNLP(DOC)) == []

    def test_none_embedding_returns_empty_without_parsing(self, monkeypatch):
        install_keybert(monkeypatch)
        nlp = StubNLP(DOC)
        assert ka.extract_keywords("some text", None, nlp) == []
        assert nlp.calls == []

    def test_none_nlp_returns_empty(self, monkeypatch):
        install_keybert(monkeypatch)
        assert ka.extract_keywords("some text", [0.1], None) == []

    def test_keybert_unavailable_returns_empty(self, monkeypatch):
        monkeypatch.setattr(ka, "_keybert_failed", True)
        assert ka.extract_keywords("some text", [0.1], StubNLP(DOC)) == []

    def test_no_candidates_skips_keybert(self, monkeypatch):
        stub = install_keybert(monkeypatch)
        nlp = StubNLP(StubDoc())  # no noun chunks
        assert ka.extract_keywords("some text", [0.1], nlp) == []
        assert stub.calls == []

    def test_keybert_exception_returns_empty(self, monkeypatch, stub_ml):
        stub = install_keybert(monkeypatch)
        stub.extract_keywords = lambda *a, **kw: (_ for _ in ()).throw(
            RuntimeError("boom")
        )
        assert ka.extract_keywords("some text", [0.1], StubNLP(DOC)) == []


class TestCandidateFiltering:
    def test_determiners_and_pronouns_stripped(self):
        doc = StubDoc(Chunk(Tok("the", pos="DET"), Tok("library")),
                      Chunk(Tok("it", pos="PRON")))
        assert ka._noun_chunk_candidates(doc) == ["library"]

    def test_non_alpha_chunks_dropped(self):
        doc = StubDoc(Chunk(Tok("AT&T", alpha=False)),
                      Chunk(Tok("2024", alpha=False), Tok("report")),
                      Chunk(Tok("education")))
        assert ka._noun_chunk_candidates(doc) == ["education"]

    def test_long_chunks_dropped(self):
        five = Chunk(*[Tok(f"w{i}") for i in range(5)])
        four = Chunk(*[Tok(f"v{i}") for i in range(4)])
        assert ka._noun_chunk_candidates(StubDoc(five, four)) == ["v0 v1 v2 v3"]

    def test_lowercased_and_deduplicated_in_order(self):
        doc = StubDoc(Chunk(Tok("Open"), Tok("Education")),
                      Chunk(Tok("library")),
                      Chunk(Tok("open"), Tok("education")))
        assert ka._noun_chunk_candidates(doc) == ["open education", "library"]


class TestExtraction:
    def test_happy_path_wiring(self, monkeypatch, stub_ml):
        stub = install_keybert(
            monkeypatch, [("open education", 0.71), ("library", 0.33)]
        )
        result = ka.extract_keywords("raw page text", [0.1, 0.2], StubNLP(DOC))
        assert result == [("open education", 0.71), ("library", 0.33)]
        assert all(isinstance(s, float) for _, s in result)

        text_arg, kwargs = stub.calls[0]
        assert text_arg == "raw page text"
        assert kwargs["use_mmr"] is True
        assert kwargs["diversity"] == ka.DEFAULT_MMR_LAMBDA
        assert kwargs["top_n"] == 20
        assert kwargs["doc_embeddings"] is stub_ml.reshaped
        # analyzer closes over the pre-computed candidates and returns
        # them verbatim — never re-parses its input.
        analyzer = kwargs["vectorizer"].analyzer
        assert analyzer("anything") == ["open education", "library"]

    def test_embedding_converted_via_asarray_reshape(self, monkeypatch, stub_ml):
        install_keybert(monkeypatch, [("library", 0.5)])
        ka.extract_keywords("text", [0.1, 0.2], StubNLP(DOC))
        args, kwargs = stub_ml.np.asarray.call_args
        assert args[0] == [0.1, 0.2]
        stub_ml.np.asarray.return_value.reshape.assert_called_once_with(1, -1)

    def test_overlong_text_truncated_to_nlp_max_length(self, monkeypatch, stub_ml,
                                                       caplog):
        install_keybert(monkeypatch, [("library", 0.5)])
        nlp = StubNLP(DOC, max_length=10)
        with caplog.at_level("WARNING"):
            ka.extract_keywords("x" * 25, [0.1], nlp)
        assert nlp.calls == ["x" * 10]
        assert "max_length" in caplog.text

    def test_explicit_params_passed_through(self, monkeypatch, stub_ml):
        stub = install_keybert(monkeypatch, [("library", 0.5)])
        ka.extract_keywords("text", [0.1], StubNLP(DOC), top_n=5, mmr_lambda=0.7)
        _, kwargs = stub.calls[0]
        assert kwargs["top_n"] == 5
        assert kwargs["diversity"] == 0.7


class TestSingleton:
    def test_init_wraps_shared_model(self, monkeypatch):
        import coyote.coyote_embedder as embedder

        shared = object()
        captured = {}

        class FakeKeyBERT:
            def __init__(self, model):
                captured["model"] = model

        monkeypatch.setitem(
            sys.modules, "keybert", types.SimpleNamespace(KeyBERT=FakeKeyBERT)
        )
        monkeypatch.setattr(embedder, "_model", shared)
        monkeypatch.setattr(embedder, "_model_load_failed", False)
        instance = ka._get_keybert()
        assert isinstance(instance, FakeKeyBERT)
        assert captured["model"] is shared
        assert ka._get_keybert() is instance  # cached

    def test_no_model_returns_none_without_latching(self, monkeypatch):
        import coyote.coyote_embedder as embedder

        monkeypatch.setitem(
            sys.modules, "keybert", types.SimpleNamespace(KeyBERT=object)
        )
        monkeypatch.setattr(embedder, "_model", None)
        monkeypatch.setattr(embedder, "_model_load_failed", True)
        assert ka._get_keybert() is None
        assert ka._keybert_failed is False  # retryable

    def test_import_failure_latches(self, monkeypatch):
        # sys.modules[name] = None makes `from keybert import ...` raise
        # ImportError deterministically, whatever the host has installed.
        monkeypatch.setitem(sys.modules, "keybert", None)
        assert ka._get_keybert() is None
        assert ka._keybert_failed is True

    def test_reset_for_tests(self):
        ka._keybert = object()
        ka._keybert_failed = True
        ka._reset_for_tests()
        assert ka._keybert is None
        assert ka._keybert_failed is False


class TestEnvFloat:
    def test_unset_uses_fallback(self, monkeypatch):
        monkeypatch.delenv("SOME_LAMBDA", raising=False)
        assert ka._env_float("SOME_LAMBDA", 0.6) == 0.6

    def test_valid_value_parsed(self, monkeypatch):
        monkeypatch.setenv("SOME_LAMBDA", "0.45")
        assert ka._env_float("SOME_LAMBDA", 0.6) == 0.45

    def test_malformed_value_falls_back(self, monkeypatch):
        monkeypatch.setenv("SOME_LAMBDA", "not-a-float")
        assert ka._env_float("SOME_LAMBDA", 0.6) == 0.6
