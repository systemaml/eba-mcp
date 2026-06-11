import json
import unittest
from collections.abc import Mapping
from io import StringIO
from typing import cast
from contextlib import redirect_stdout
from unittest.mock import patch

import requests

from eba_pipeline.parser.repair import (
    RepairValidationError,
    repair_low_confidence_chunks,
    validate_repair_json,
)


class FakeResponse:
    status_code: int
    _payload: Mapping[str, object]
    text: str

    def __init__(self, payload: Mapping[str, object], *, status_code: int = 200, text: str = "") -> None:
        self._payload = payload
        self.status_code = status_code
        self.text = text

    def json(self) -> Mapping[str, object]:
        return self._payload


def chunk(sequence_no: int, text: str, confidence: float) -> dict[str, object]:
    return {
        "chunk_id": f"chunk-{sequence_no}",
        "eba_id": "EBA/GL/2022/05",
        "language": "en",
        "section_path": "",
        "paragraph_ref": None,
        "page_start": 1,
        "page_end": 1,
        "section_ref": None,
        "section_title": None,
        "section_level": None,
        "parent_section_ref": None,
        "document_region": "body",
        "metadata_confidence": confidence,
        "metadata_source": "deterministic",
        "text": text,
        "text_hash": "hash",
        "chunk_type": "paragraph",
        "sequence_no": sequence_no,
    }


class LlmRepairTests(unittest.TestCase):
    def test_valid_json_acceptance_updates_only_low_confidence_span(self) -> None:
        chunks = [
            chunk(1, "4.2 The role and responsibilities of the AML/CFT compliance officer", 0.72),
            chunk(2, "Institutions should define responsibilities clearly.", 0.7),
            chunk(3, "5. High confidence deterministic paragraph", 0.95),
        ]
        repair = json.dumps(
            {
                "sections": [
                    {
                        "section_ref": "4.2",
                        "title": "The role and responsibilities of the AML/CFT compliance officer",
                        "level": 2,
                        "parent_section_ref": "4",
                    }
                ],
                "regions": [{"start_sequence_no": 1, "end_sequence_no": 2, "document_region": "body"}],
            }
        )
        calls: list[dict[str, object]] = []

        def fake_post(url: str, *, json: Mapping[str, object], timeout: int) -> FakeResponse:
            calls.append({"url": url, "json": json, "timeout": timeout})
            return FakeResponse({"response": repair})

        with patch("eba_pipeline.parser.repair.requests.post", side_effect=fake_post):
            repaired = repair_low_confidence_chunks(chunks, ollama_url="http://ollama:11434", model="custom-repair")

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["url"], "http://ollama:11434/api/generate")
        request_json = cast(Mapping[str, object], calls[0]["json"])
        self.assertEqual(request_json["model"], "custom-repair")
        prompt = cast(str, request_json["prompt"])
        self.assertIn("Return one JSON object only: no markdown, no prose, no code fences", prompt)
        self.assertIn("Use this exact schema and no other keys", prompt)
        self.assertIn("If the source span does not contain a valid numeric section ref and exact title", prompt)
        self.assertEqual(repaired[0]["section_ref"], "4.2")
        self.assertEqual(repaired[1]["metadata_source"], "llm_repair")
        self.assertEqual(repaired[2]["metadata_source"], "deterministic")

    def test_default_confident_chunks_never_call_ollama(self) -> None:
        chunks = [chunk(1, "1. Confident paragraph", 0.8), chunk(2, "2. Also confident", 1.0)]
        with patch("eba_pipeline.parser.repair.requests.post") as post_mock:
            repaired = repair_low_confidence_chunks(chunks, confidence_threshold=0.8)
        post_mock.assert_not_called()
        self.assertEqual(repaired, chunks)

    def test_custom_confidence_threshold_expands_repair_candidates(self) -> None:
        chunks = [chunk(1, "4.2 The role and responsibilities of the AML/CFT compliance officer", 0.85)]
        repair = json.dumps(
            {
                "sections": [
                    {
                        "section_ref": "4.2",
                        "title": "The role and responsibilities of the AML/CFT compliance officer",
                        "level": 2,
                        "parent_section_ref": "4",
                    }
                ],
                "regions": [{"start_sequence_no": 1, "end_sequence_no": 1, "document_region": "body"}],
            }
        )

        with patch("eba_pipeline.parser.repair.requests.post", return_value=FakeResponse({"response": repair})) as post_mock:
            repaired = repair_low_confidence_chunks(chunks, confidence_threshold=0.9)

        post_mock.assert_called_once()
        self.assertEqual(repaired[0]["metadata_source"], "llm_repair")
        self.assertEqual(repaired[0]["section_ref"], "4.2")

    def test_ollama_unreachable_warns_and_preserves_deterministic_output(self) -> None:
        chunks = [chunk(1, "4.2 The role and responsibilities of the AML/CFT compliance officer", 0.5)]
        with patch("eba_pipeline.parser.repair.requests.post", side_effect=requests.ConnectionError("offline")):
            with self.assertWarnsRegex(RuntimeWarning, "Skipping LLM repair"):
                repaired = repair_low_confidence_chunks(chunks)
        self.assertEqual(repaired, chunks)

    def test_progress_logging_reports_span_confidence_and_failures(self) -> None:
        chunks = [chunk(1, "4.2 The role and responsibilities of the AML/CFT compliance officer", 0.5)]
        output = StringIO()
        with patch("eba_pipeline.parser.repair.requests.post", side_effect=requests.Timeout("read timed out")):
            with self.assertWarnsRegex(RuntimeWarning, "Skipping LLM repair"):
                with redirect_stdout(output):
                    repaired = repair_low_confidence_chunks(chunks, log_progress=True, progress_label="EBA/GL/2022/05")

        self.assertEqual(repaired, chunks)
        log = output.getvalue()
        self.assertIn("LLM repair for EBA/GL/2022/05: 1 low-confidence spans, 1 chunks below", log)
        self.assertIn("seq=1-1 chunks=1 confidence=min=0.50 avg=0.50 max=0.50", log)
        self.assertIn("skipped", log)
        self.assertIn("Timeout: read timed out", log)

    def test_validation_failure_warning_includes_response_excerpt(self) -> None:
        chunks = [chunk(1, "4.2 The role and responsibilities of the AML/CFT compliance officer", 0.5)]
        invalid_repair = json.dumps({"sections": [], "regions": [], "notes": "not allowed"})
        with patch("eba_pipeline.parser.repair.requests.post", return_value=FakeResponse({"response": invalid_repair})):
            with self.assertWarnsRegex(RuntimeWarning, "response excerpt"):
                repaired = repair_low_confidence_chunks(chunks)

        self.assertEqual(repaired, chunks)

    def test_validation_rejects_extra_fields_and_hallucinated_titles(self) -> None:
        span = [chunk(1, "4.2 The role and responsibilities of the AML/CFT compliance officer", 0.72)]
        extra_field = json.dumps({"sections": [], "regions": [], "unexpected": True})
        with self.assertRaisesRegex(RepairValidationError, "exactly sections and regions"):
            _ = validate_repair_json(extra_field, span)

        hallucinated = json.dumps(
            {
                "sections": [
                    {"section_ref": "4.3", "title": "Invented title", "level": 2, "parent_section_ref": "4"}
                ],
                "regions": [{"start_sequence_no": 1, "end_sequence_no": 1, "document_region": "body"}],
            }
        )
        with self.assertRaisesRegex(RepairValidationError, "not found in source text"):
            _ = validate_repair_json(hallucinated, span)


if __name__ == "__main__":
    _ = unittest.main()
