import unittest
from collections.abc import Mapping
from typing import Final, cast
from unittest.mock import patch

from eba_pipeline.index.embeddings import EmbeddingGenerationError, generate_embeddings


def unit_vector(*values: float) -> list[float]:
    return list(values)


class FakeResponse:
    status_code: int
    _payload: Mapping[str, object] | None
    text: str

    def __init__(self, *, status_code: int = 200, payload: Mapping[str, object] | None = None, text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self) -> Mapping[str, object]:
        if self._payload is None:
            raise ValueError("invalid json")
        return self._payload


class EmbeddingGenerationTests(unittest.TestCase):
    def test_generate_embeddings_batches_input_and_preserves_order(self) -> None:
        chunks = [
            {"text": "alpha"},
            {"text": "beta"},
            {"text": "gamma"},
        ]
        calls: list[dict[str, object]] = []

        def fake_post(url: str, *, json: Mapping[str, object], timeout: int) -> FakeResponse:
            calls.append({"url": url, "json": json, "timeout": timeout})
            inputs = json["input"]
            if inputs == ["alpha", "beta"]:
                return FakeResponse(payload={"embeddings": [unit_vector(1.0, 0.0), unit_vector(0.0, 1.0)]})
            return FakeResponse(payload={"embeddings": [unit_vector(0.70710678, 0.70710678)]})

        with patch("eba_pipeline.index.embeddings.requests.post", side_effect=fake_post):
            vectors = generate_embeddings(chunks, model="custom-embed", ollama_url="http://ollama:11434/", batch_size=2)

        self.assertEqual(
            vectors,
            [
                [1.0, 0.0],
                [0.0, 1.0],
                [0.70710678, 0.70710678],
            ],
        )
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0]["url"], "http://ollama:11434/api/embed")
        self.assertEqual(calls[0]["json"], {"model": "custom-embed", "input": ["alpha", "beta"]})
        self.assertEqual(calls[1]["json"], {"model": "custom-embed", "input": ["gamma"]})

    def test_generate_embeddings_reports_progress_every_100_and_at_completion(self) -> None:
        chunks = [{"text": f"chunk-{index}"} for index in range(205)]

        def fake_post(_url: str, *, json: Mapping[str, object], timeout: int) -> FakeResponse:
            inputs = json["input"]
            input_count = len(cast(list[object], inputs)) if isinstance(inputs, list) else 0
            self.assertEqual(timeout, 60)
            return FakeResponse(payload={"embeddings": [unit_vector(1.0, 0.0) for _ in range(input_count)]})

        with patch("eba_pipeline.index.embeddings.requests.post", side_effect=fake_post):
            with patch("builtins.print") as print_mock:
                vectors = generate_embeddings(
                    chunks,
                    model="custom-embed",
                    ollama_url="http://localhost:11434",
                    batch_size=80,
                )

        self.assertEqual(len(vectors), 205)
        self.assertEqual(
            [call.args[0] for call in print_mock.call_args_list],
            [
                "  Embeddings: 100/205 chunks",
                "  Embeddings: 200/205 chunks",
                "  Embeddings: 205/205 chunks",
            ],
        )

    def test_generate_embeddings_enforces_default_nomic_dimension(self) -> None:
        with patch(
            "eba_pipeline.index.embeddings.requests.post",
            return_value=FakeResponse(payload={"embeddings": [[1.0, 0.0]]}),
        ):
            with self.assertRaisesRegex(EmbeddingGenerationError, "expected 768"):
                _ = generate_embeddings(
                    [{"text": "alpha"}],
                    model="nomic-embed-text",
                    ollama_url="http://localhost:11434",
                    batch_size=1,
                )

    def test_generate_embeddings_retries_and_succeeds_on_third_attempt(self) -> None:
        responses: Final[list[FakeResponse]] = [
            FakeResponse(status_code=500, text="temporary failure"),
            FakeResponse(status_code=502, text="still failing"),
            FakeResponse(payload={"embeddings": [unit_vector(1.0, 0.0)]}),
        ]

        with patch("eba_pipeline.index.embeddings.requests.post", side_effect=responses) as post_mock:
            with patch("eba_pipeline.index.embeddings.time.sleep") as sleep_mock:
                vectors = generate_embeddings([{"text": "alpha"}], model="custom-embed", ollama_url="http://localhost:11434", batch_size=1)

        self.assertEqual(vectors, [[1.0, 0.0]])
        self.assertEqual(post_mock.call_count, 3)
        sleep_mock.assert_any_call(0.5)
        sleep_mock.assert_any_call(1.0)

    def test_generate_embeddings_raises_clear_error_after_retries_exhausted(self) -> None:
        with patch(
            "eba_pipeline.index.embeddings.requests.post",
            return_value=FakeResponse(status_code=503, text="service unavailable"),
        ):
            with patch("eba_pipeline.index.embeddings.time.sleep"):
                with self.assertRaisesRegex(EmbeddingGenerationError, "failed after 3 attempts"):
                    _ = generate_embeddings(
                        [{"text": "alpha"}],
                        model="custom-embed",
                        ollama_url="http://localhost:11434",
                        batch_size=1,
                    )

    def test_generate_embeddings_rejects_non_normalized_vectors(self) -> None:
        with patch(
            "eba_pipeline.index.embeddings.requests.post",
            return_value=FakeResponse(payload={"embeddings": [[2.0, 0.0]]}),
        ):
            with patch("eba_pipeline.index.embeddings.time.sleep"):
                with self.assertRaisesRegex(EmbeddingGenerationError, "L2 norm"):
                    _ = generate_embeddings(
                        [{"text": "alpha"}],
                        model="custom-embed",
                        ollama_url="http://localhost:11434",
                        batch_size=1,
                    )

    def test_generate_embeddings_rejects_missing_chunk_text(self) -> None:
        with self.assertRaisesRegex(ValueError, "missing required string field 'text'"):
            _ = generate_embeddings(
                [{"chunk_id": "no-text"}],
                model="custom-embed",
                ollama_url="http://localhost:11434",
                batch_size=1,
            )

    def test_generate_embeddings_rejects_response_count_mismatch(self) -> None:
        with patch(
            "eba_pipeline.index.embeddings.requests.post",
            return_value=FakeResponse(payload={"embeddings": [unit_vector(1.0, 0.0), unit_vector(0.0, 1.0)]}),
        ):
            with patch("eba_pipeline.index.embeddings.time.sleep"):
                with self.assertRaisesRegex(EmbeddingGenerationError, "count mismatch"):
                    _ = generate_embeddings(
                        [{"text": "alpha"}],
                        model="custom-embed",
                        ollama_url="http://localhost:11434",
                        batch_size=1,
                    )


if __name__ == "__main__":
    _ = unittest.main()
