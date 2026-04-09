"""Tests for ai-service debug match diagnostics."""

from __future__ import annotations

import json
from types import SimpleNamespace

from ai_service.domain.job import Job
from ai_service.domain.user import User, UserPreferences
from ai_service.port.repository import JobEmbeddingRecord
from ai_service.usecase.debug_match import DebugMatchUseCase
from ai_service.util.debug_http_server import build_debug_handler


def test_debug_match_returns_threshold_failure_explanation() -> None:
    user_repo = SimpleNamespace(
        get_by_id=lambda user_id: User(
            id=user_id,
            telegram_id=1,
            profile_text="Python backend developer with FastAPI and PostgreSQL",
            embedding=[1.0, 0.0, 0.0],
            preferences=UserPreferences(include_keywords=("python", "postgresql")),
        ),
        get_embedding=lambda _user_id: [1.0, 0.0, 0.0],
    )
    job = Job(
        id=5,
        source="kwork",
        url="https://kwork.ru/projects/5",
        title="FastAPI backend with PostgreSQL",
        description="Need FastAPI and PostgreSQL developer",
        raw_html="",
        budget="15000",
    )
    job_repo = SimpleNamespace(
        get_by_url=lambda _url: job,
        get_embedding=lambda _job_id: JobEmbeddingRecord(
            embedding=[0.4, 0.9, 0.0],
            metadata={"classification": {"technologies": ["fastapi", "postgresql"], "project_type": "web"}},
        ),
    )
    reranker = SimpleNamespace(score_pairs=lambda pairs: [0.58] if pairs else [])

    use_case = DebugMatchUseCase(
        user_repo,
        job_repo,
        reranker=reranker,
        similarity_threshold=0.62,
        rerank_threshold=0.60,
    )

    result = use_case.execute(1, "https://kwork.ru/projects/5")

    assert result["project"]["title"] == "FastAPI backend with PostgreSQL"
    assert result["embedding_similarity"] < 0.62
    assert result["rerank_score"] == 0.58
    assert result["preference_filter"]["passed"] is True
    assert result["profile_stack"] == ["python", "postgresql"]
    assert result["job_stack"] == ["fastapi", "postgresql"]
    assert result["stack_intersection"] == ["postgresql"]
    assert "ANN порог" in result["conclusion"]


def test_debug_http_handler_returns_json_payload() -> None:
    class FakeDebugUseCase:
        def execute(self, user_id: int, job_url: str) -> dict[str, object]:
            return {"user_id": user_id, "job_url": job_url, "conclusion": "ok"}

    handler_cls = build_debug_handler(FakeDebugUseCase())
    server = SimpleNamespace()

    request = (
        b"GET /internal/debug/match?user_id=7&job_url=https%3A%2F%2Fkwork.ru%2Fprojects%2F7 HTTP/1.1\r\n"
        b"Host: localhost\r\n\r\n"
    )

    class FakeSocket:
        def __init__(self, payload: bytes) -> None:
            import io

            self._rfile = io.BytesIO(payload)
            self._wfile = io.BytesIO()

        def makefile(self, mode: str, *args, **kwargs):  # noqa: ANN002, ANN003
            if "r" in mode:
                return self._rfile
            return self._wfile

        def sendall(self, data: bytes) -> None:
            self._wfile.write(data)

        def close(self) -> None:
            return

    sock = FakeSocket(request)
    handler_cls(sock, ("127.0.0.1", 12345), server)
    body = sock._wfile.getvalue().split(b"\r\n\r\n", 1)[1]
    payload = json.loads(body.decode("utf-8"))
    assert payload["user_id"] == 7
    assert payload["job_url"] == "https://kwork.ru/projects/7"
