from __future__ import annotations

from dataclasses import dataclass

from testmind.models.retrieval import RetrievalScope


@dataclass(frozen=True, slots=True)
class RequestContext:
    """Trusted, server-derived context passed to subgraphs at invocation time."""

    retrieval_scope: RetrievalScope | None = None
