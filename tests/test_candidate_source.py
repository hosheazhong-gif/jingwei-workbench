from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from app.adapters.local_source import CaptureError, sha256_file
from app.adapters.sqlite_repository import SqliteRepository
from app.api import ReadOnlyHttpServer
from app.application.attach_claim import ClaimAttachError, attach_claim_to_block
from app.application.candidate_source import (
    CandidateSourceError,
    capture_web_candidate,
    discard_web_candidate,
    open_web_candidate,
    promote_web_candidate,
)
from app.application.import_sample import import_sample
from app.projections.candidates import build_candidate_source_projection
from app.projections.report import build_report_projection


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_PATH = PROJECT_ROOT / "samples/synthetic_case/consulting_fixture_v0.1.json"


class MemoryWebAdapter:
    key = "web_page"

    def snapshot(self, url: str, project_files: Path) -> dict:
        project_files = Path(project_files)
        project_files.mkdir(parents=True, exist_ok=True)
        destination = project_files / "snapshot.bin"
        destination.write_bytes(
            "<html><body><p>opened</p>"
            "<p>这份假快照要有一段够长的正文，工作台才认它存下了可读内容，"
            "否则「看快照 / 从快照扒原话」两个键都不该出现。</p>"
            "</body></html>".encode("utf-8")
        )
        return {
            "file_name": "snapshot.bin",
            "original_url": url,
            "snapshot_path": destination,
            "content_hash": sha256_file(destination),
            "availability": "available",
        }


class FailingWebAdapter:
    key = "web_page"

    def snapshot(self, url: str, project_files: Path) -> dict:
        raise CaptureError("打开后未能保存快照：无法读取该网页")


class CandidateSourceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.database_path = Path(self.temp_dir.name) / "jingwei.sqlite3"
        self.repository = SqliteRepository(self.database_path)
        self.repository.migrate()
        self.project_id = import_sample(self.repository, SAMPLE_PATH)

    def test_capture_is_not_a_source_and_does_not_change_synthetic(self) -> None:
        before_text = _block_text(self.repository, "DB-001")
        before_status = _claim_status(self.repository, "C-002")
        before_sources = self.repository.list_source_ids(self.project_id)
        result = capture_web_candidate(
            self.repository,
            self.project_id,
            url="https://example.com/notice",
            title="待打开公告",
            note="只是候选，不是主张。",
        )
        self.assertEqual(result["candidate"]["status"], "captured")
        self.assertFalse(result["candidate"]["can_promote"])
        self.assertEqual(result["confirmation"]["record_kind"], "capture_candidate")
        self.assertEqual(result["confirmation"]["candidate_id"], result["candidate_id"])
        self.assertEqual(self.repository.list_source_ids(self.project_id), before_sources)
        self.assertEqual(_block_text(self.repository, "DB-001"), before_text)
        self.assertEqual(_claim_status(self.repository, "C-002"), before_status)
        with self.assertRaises(ClaimAttachError):
            attach_claim_to_block(
                self.repository,
                "DB-001",
                source_id=result["candidate_id"],
                excerpt="不能从未打开链接写主张。",
                text="未打开链接不是证据。",
                epistemic_type="factual_claim",
            )

    def test_promote_requires_open_then_creates_source(self) -> None:
        captured = capture_web_candidate(
            self.repository,
            self.project_id,
            url="https://example.com/policy",
            title="政策页",
        )
        with self.assertRaises(CandidateSourceError):
            promote_web_candidate(
                self.repository,
                captured["candidate_id"],
                adapter=MemoryWebAdapter(),
            )
        opened = open_web_candidate(self.repository, captured["candidate_id"])
        self.assertEqual(opened["candidate"]["status"], "opened")
        self.assertTrue(opened["candidate"]["can_promote"])
        self.assertEqual(opened["confirmation"]["record_kind"], "open_candidate")
        self.assertEqual(opened["confirmation"]["candidate_id"], captured["candidate_id"])
        before_status = _claim_status(self.repository, "C-002")
        promoted = promote_web_candidate(
            self.repository,
            captured["candidate_id"],
            adapter=MemoryWebAdapter(),
        )
        source = self.repository.get_source(promoted["source_id"])
        self.assertEqual(source["kind"], "web_page")
        self.assertEqual(source["original_url"], "https://example.com/policy")
        self.assertEqual(source["availability"], "available")
        self.assertIsNotNone(source["content_hash"])
        self.assertEqual(_claim_status(self.repository, "C-002"), before_status)
        self.assertEqual(
            build_report_projection(self.repository, self.project_id)["blocks"][0]["id"],
            "DB-001",
        )
        listing = build_candidate_source_projection(self.repository, self.project_id)
        self.assertEqual(listing["candidates"][0]["status"], "promoted")
        self.assertFalse(listing["candidates"][0]["can_promote"])

    def test_failed_snapshot_still_promotes_without_rewrite(self) -> None:
        captured = capture_web_candidate(
            self.repository,
            self.project_id,
            url="https://example.com/missing",
        )
        open_web_candidate(self.repository, captured["candidate_id"])
        promoted = promote_web_candidate(
            self.repository,
            captured["candidate_id"],
            adapter=FailingWebAdapter(),
        )
        source = self.repository.get_source(promoted["source_id"])
        self.assertEqual(source["availability"], "path_expired")
        self.assertIsNone(source["content_hash"])
        self.assertEqual(_claim_status(self.repository, "C-002"), "captured")

    def test_rejects_non_http_and_duplicate_active_url(self) -> None:
        with self.assertRaises(CandidateSourceError):
            capture_web_candidate(self.repository, self.project_id, url="file:///tmp/x")
        capture_web_candidate(
            self.repository, self.project_id, url="https://example.com/dup"
        )
        with self.assertRaises(CandidateSourceError):
            capture_web_candidate(
                self.repository, self.project_id, url="https://example.com/dup"
            )

    def test_discard_hides_candidate_without_rewriting_source_or_draft(self) -> None:
        captured = capture_web_candidate(
            self.repository,
            self.project_id,
            url="https://example.com/skip",
        )
        before_status = _claim_status(self.repository, "C-002")
        before_text = build_report_projection(self.repository, self.project_id)["blocks"][0][
            "current_text"
        ]
        result = discard_web_candidate(self.repository, captured["candidate_id"])
        self.assertEqual(result["confirmation"]["record_kind"], "discard_candidate")
        listing = build_candidate_source_projection(self.repository, self.project_id)
        discarded = next(
            item for item in listing["candidates"] if item["id"] == captured["candidate_id"]
        )
        self.assertEqual(discarded["status"], "discarded")
        self.assertFalse(discarded["can_discard"])
        self.assertEqual(_claim_status(self.repository, "C-002"), before_status)
        self.assertEqual(
            build_report_projection(self.repository, self.project_id)["blocks"][0]["current_text"],
            before_text,
        )
        promoted = capture_web_candidate(
            self.repository, self.project_id, url="https://example.com/keep"
        )
        open_web_candidate(self.repository, promoted["candidate_id"])
        promote_web_candidate(
            self.repository,
            promoted["candidate_id"],
            adapter=MemoryWebAdapter(),
        )
        with self.assertRaisesRegex(CandidateSourceError, "已升为来源"):
            discard_web_candidate(self.repository, promoted["candidate_id"])


class CandidateSourceHttpTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.database_path = Path(self.temp_dir.name) / "jingwei.sqlite3"
        self.repository = SqliteRepository(self.database_path)
        self.repository.migrate()
        self.project_id = import_sample(self.repository, SAMPLE_PATH)
        self.server = ReadOnlyHttpServer(self.repository, host="127.0.0.1", port=0)
        self.server.start()
        self.addCleanup(self.server.stop)

    def test_http_capture_open_and_reject_unopened_promote(self) -> None:
        empty_status, empty = _http_json(
            "GET",
            self.server.origin + f"/projects/{self.project_id}/candidate-sources",
        )
        self.assertEqual(empty_status, 200)
        self.assertEqual(empty["candidates"], [])
        create_status, created = _http_json(
            "POST",
            self.server.origin + f"/projects/{self.project_id}/candidate-sources",
            {"url": "https://example.com/http-candidate", "title": "HTTP 候选"},
        )
        self.assertEqual(create_status, 201)
        candidate_id = created["candidate_id"]
        promote_status, promote_payload = _http_json(
            "POST",
            self.server.origin + f"/candidate-sources/{candidate_id}/promote",
            {},
        )
        self.assertEqual(promote_status, 400)
        self.assertIn("须先打开", promote_payload["error"])
        open_status, opened = _http_json(
            "POST",
            self.server.origin + f"/candidate-sources/{candidate_id}/open",
            {},
        )
        self.assertEqual(open_status, 200)
        self.assertEqual(opened["url"], "https://example.com/http-candidate")
        self.assertEqual(_claim_status(self.repository, "C-002"), "captured")


def _block_text(repository: SqliteRepository, block_id: str) -> str:
    with repository.connect() as connection:
        return connection.execute(
            "SELECT current_text FROM deliverable_blocks WHERE id = ?",
            (block_id,),
        ).fetchone()["current_text"]


def _claim_status(repository: SqliteRepository, claim_id: str) -> str:
    with repository.connect() as connection:
        return connection.execute(
            "SELECT verification_status FROM claims WHERE id = ?",
            (claim_id,),
        ).fetchone()["verification_status"]


def _http_json(method: str, url: str, payload: dict | None = None) -> tuple[int, dict]:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(url, data=body, method=method)
    if payload is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urlopen(request) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        payload = json.loads(error.read().decode("utf-8"))
        error.close()
        return error.code, payload


if __name__ == "__main__":
    unittest.main()
