"""Cold chat turns must share one Chroma client and one embedding model."""

from __future__ import annotations

import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

from memory import vector


def test_concurrent_first_queries_construct_one_client(tmp_path, monkeypatch):
    """Prevent parallel recall/library lookup from observing unfinished Rust bindings."""
    constructed = []
    gate = threading.Barrier(6)

    def create_client(**kwargs):
        """Expose a construction window so the test exercises overlapping callers."""
        instance = object()
        constructed.append(instance)
        time.sleep(0.03)
        return instance

    def query_client(_):
        """Start all requests together, as the conversational context assembler does."""
        gate.wait(timeout=5)
        return vector._get_client()

    monkeypatch.setattr(vector, "_client", None)
    monkeypatch.setattr(vector, "chroma_dir", lambda: tmp_path / "chroma")
    monkeypatch.setitem(sys.modules, "chromadb", SimpleNamespace(PersistentClient=create_client))
    with ThreadPoolExecutor(max_workers=6) as pool:
        clients = list(pool.map(query_client, range(6)))
    assert len(constructed) == 1
    assert all(client is constructed[0] for client in clients)


def test_concurrent_first_embeddings_load_one_model(tmp_path, monkeypatch):
    """Keep parallel context sources from duplicating a heavy ONNX model load."""
    constructed = []
    gate = threading.Barrier(6)

    class Embedder:
        """A small model stand-in with a deliberately overlapping load window."""

        def __init__(self, **kwargs):
            constructed.append(self)
            time.sleep(0.03)

        def embed(self, texts):
            """Return fixed vectors so this check isolates construction, not model quality."""
            return [[1.0, 0.0] for _ in texts]

    def query_embedding(_):
        """Start embedding requests together on independent worker threads."""
        gate.wait(timeout=5)
        return vector._embed(["a query"])

    monkeypatch.setattr(vector, "_embedder", None)
    monkeypatch.setattr(vector, "fastembed_cache_dir", lambda: tmp_path / "fastembed")
    monkeypatch.setitem(sys.modules, "fastembed", SimpleNamespace(TextEmbedding=Embedder))
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(query_embedding, range(6)))
    assert len(constructed) == 1
    assert results == [[[1.0, 0.0]]] * 6
