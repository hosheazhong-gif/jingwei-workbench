from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from app.adapters.local_source import CaptureError, sha256_file
from app.adapters.sqlite_repository import SqliteRepository
from app.api import ReadOnlyHttpServer
from app.application.capture_source import capture_local_source
from app.application.create_project import create_project
from app.application.import_sample import import_sample
from app.projections.report import build_report_projection


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_PATH = PROJECT_ROOT / "samples/synthetic_case/consulting_fixture_v0.1.json"


class CaptureSourceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.database_path = Path(self.temp_dir.name) / "jingwei.sqlite3"
        self.repository = SqliteRepository(self.database_path)
        self.repository.migrate()
        self.project_id = import_sample(self.repository, SAMPLE_PATH)
        self.incoming = Path(self.temp_dir.name) / "replacement.txt"
        self.incoming.write_text("macro-market-replacement\n", encoding="utf-8")

    def test_capture_creates_new_source_without_overwriting_old_one(self) -> None:
        old = self.repository.get_source("S-007")
        result = capture_local_source(
            self.repository,
            self.project_id,
            self.incoming,
            title="替代宏观市场材料",
            supersedes_source_id="S-007",
        )
        new_id = result["source"]["id"]
        new = self.repository.get_source(new_id)
        still_old = self.repository.get_source("S-007")
        copy_path = self.repository.files_root / str(new["snapshot_path"])

        self.assertEqual(new_id, "S-009")
        self.assertEqual(new["supersedes_source_id"], "S-007")
        self.assertEqual(new["availability"], "available")
        self.assertEqual(new["content_hash"], sha256_file(self.incoming))
        self.assertEqual(still_old["availability"], old["availability"])
        self.assertEqual(still_old["content_hash"], old["content_hash"])
        self.assertEqual(still_old["snapshot_path"], old["snapshot_path"])
        self.assertTrue(copy_path.is_file())
        self.assertEqual(sha256_file(copy_path), new["content_hash"])
        self.assertNotEqual(copy_path.resolve(), self.incoming.resolve())
        self.assertEqual(result["excerpt_candidates"], [])

    def test_upload_creates_new_source_without_overwriting_old_one(self) -> None:
        old = self.repository.get_source("S-007")
        payload = b"uploaded-macro-bytes\n"
        result = capture_local_source(
            self.repository,
            self.project_id,
            uploaded_name="replacement.txt",
            uploaded_bytes=payload,
            title="上传宏观材料",
            supersedes_source_id="S-007",
        )
        new = self.repository.get_source(result["source"]["id"])
        still_old = self.repository.get_source("S-007")
        copy_path = self.repository.files_root / str(new["snapshot_path"])
        self.assertEqual(new["supersedes_source_id"], "S-007")
        self.assertEqual(new["file_name"], "replacement.txt")
        self.assertEqual(new["original_path"], "replacement.txt")
        self.assertEqual(new["content_hash"], sha256_file(copy_path))
        self.assertEqual(copy_path.read_bytes(), payload)
        self.assertEqual(still_old["content_hash"], old["content_hash"])
        self.assertEqual(still_old["snapshot_path"], old["snapshot_path"])
        self.assertEqual(result["excerpt_candidates"], [])

    def test_upload_rejects_empty_or_both_inputs(self) -> None:
        with self.assertRaisesRegex(CaptureError, "文件内容为空"):
            capture_local_source(
                self.repository,
                self.project_id,
                uploaded_name="empty.txt",
                uploaded_bytes=b"",
            )
        with self.assertRaisesRegex(CaptureError, "须提供本机路径或上传文件"):
            capture_local_source(
                self.repository,
                self.project_id,
                self.incoming,
                uploaded_name="both.txt",
                uploaded_bytes=b"data",
            )
        self.assertEqual(len(self.repository.list_source_ids(self.project_id)), 8)

    def test_second_capture_keeps_the_first_controlled_copy(self) -> None:
        first = capture_local_source(self.repository, self.project_id, self.incoming)
        first_copy = Path(first["controlled_copy"])
        first_hash = first["source"]["content_hash"]
        self.incoming.write_text("second-version\n", encoding="utf-8")
        second = capture_local_source(self.repository, self.project_id, self.incoming)
        self.assertNotEqual(first["source"]["id"], second["source"]["id"])
        self.assertTrue(first_copy.is_file())
        self.assertEqual(sha256_file(first_copy), first_hash)
        self.assertNotEqual(second["source"]["content_hash"], first_hash)
        self.assertTrue(Path(second["controlled_copy"]).is_file())

    def test_missing_file_does_not_create_a_source(self) -> None:
        with self.assertRaises(CaptureError):
            capture_local_source(
                self.repository,
                self.project_id,
                Path(self.temp_dir.name) / "missing.txt",
            )
        self.assertEqual(len(self.repository.list_source_ids(self.project_id)), 8)

    def test_new_project_does_not_reuse_synthetic_source_ids(self) -> None:
        created = create_project(
            self.repository,
            name="第二个题目",
            original_context="新题目补材料。",
        )
        result = capture_local_source(
            self.repository,
            created["project_id"],
            self.incoming,
            title="新题目材料",
        )
        self.assertEqual(result["source"]["id"], "S-009")
        self.assertEqual(result["source"]["project_id"], created["project_id"])
        self.assertEqual(self.repository.get_source("S-001")["project_id"], self.project_id)
        self.assertEqual(len(self.repository.list_source_ids(self.project_id)), 8)
        self.assertEqual(len(self.repository.list_source_ids(created["project_id"])), 1)

    def test_report_object_ids_stay_the_same_after_capture(self) -> None:
        capture_local_source(self.repository, self.project_id, self.incoming)
        report = build_report_projection(self.repository, self.project_id)
        self.assertEqual(
            {block["id"] for block in report["blocks"]},
            {"DB-001", "DB-002", "DB-003", "DB-004"},
        )

    def test_http_new_project_capture_does_not_collide(self) -> None:
        created = create_project(
            self.repository,
            name="HTTP 新题目",
            original_context="补材料不能撞号。",
        )
        server = ReadOnlyHttpServer(self.repository, host="127.0.0.1", port=0)
        server.start()
        self.addCleanup(server.stop)
        status, payload = _post_json(
            f"{server.origin}/projects/{created['project_id']}/sources",
            {"path": str(self.incoming), "title": "新题目材料"},
        )
        self.assertEqual(status, 201)
        self.assertEqual(payload["source"]["id"], "S-009")
        self.assertEqual(payload["source"]["project_id"], created["project_id"])
        self.assertEqual(self.repository.get_source("S-001")["project_id"], self.project_id)

    def test_http_post_captures_local_file(self) -> None:
        server = ReadOnlyHttpServer(self.repository, host="127.0.0.1", port=0)
        server.start()
        self.addCleanup(server.stop)
        status, payload = _post_json(
            f"{server.origin}/projects/{self.project_id}/sources",
            {
                "path": str(self.incoming),
                "title": "HTTP 捕获材料",
                "supersedes_source_id": "S-007",
            },
        )
        self.assertEqual(status, 201)
        self.assertEqual(payload["source"]["supersedes_source_id"], "S-007")
        self.assertEqual(
            payload["source"]["content_hash"],
            sha256_file(self.incoming),
        )
        stored = self.repository.get_source(payload["source"]["id"])
        self.assertIsNotNone(stored)
        self.assertTrue((self.repository.files_root / stored["snapshot_path"]).is_file())

    def test_http_upload_captures_bytes_without_path(self) -> None:
        server = ReadOnlyHttpServer(self.repository, host="127.0.0.1", port=0)
        server.start()
        self.addCleanup(server.stop)
        status, payload = _post_multipart(
            f"{server.origin}/projects/{self.project_id}/sources",
            fields={"title": "HTTP 上传材料", "supersedes_source_id": "S-007"},
            filename="../upload.txt",
            content=b"http-upload-body\n",
        )
        self.assertEqual(status, 201)
        self.assertEqual(payload["source"]["file_name"], "upload.txt")
        self.assertEqual(payload["source"]["supersedes_source_id"], "S-007")
        stored = self.repository.get_source(payload["source"]["id"])
        copy_path = self.repository.files_root / stored["snapshot_path"]
        self.assertEqual(copy_path.read_bytes(), b"http-upload-body\n")
        self.assertEqual(self.repository.get_source("S-007")["id"], "S-007")
        missing = _post_multipart(
            f"{server.origin}/projects/{self.project_id}/sources",
            fields={},
            filename="",
            content=b"no-name",
        )
        self.assertEqual(missing[0], 400)

    def test_http_get_lists_source_titles(self) -> None:
        server = ReadOnlyHttpServer(self.repository, host="127.0.0.1", port=0)
        server.start()
        self.addCleanup(server.stop)
        with urlopen(f"{server.origin}/projects/{self.project_id}/sources") as response:
            payload = json.loads(response.read().decode("utf-8"))
        titles = [item["title"] for item in payload["sources"]]
        self.assertIn("中国温控物流市场总产值数据", titles)
        expired = next(
            item for item in payload["sources"] if item["title"] == "中国温控物流市场总产值数据"
        )
        self.assertEqual(expired["availability_label"], "文件路径已失效")
        self.assertNotIn("生产型租户占比可以作为客户提供信息进入报告", json.dumps(payload, ensure_ascii=False))

    def test_files_and_database_can_be_deleted_after_capture(self) -> None:
        extra_db = Path(self.temp_dir.name) / "capture-release.sqlite3"
        repository = SqliteRepository(extra_db)
        repository.migrate()
        import_sample(repository, SAMPLE_PATH)
        capture_local_source(repository, self.project_id, self.incoming)
        extra_db.unlink()
        self.assertFalse(extra_db.exists())
        shutil.rmtree(repository.files_root)
        self.assertFalse(repository.files_root.exists())


def _post_json(url: str, payload: dict) -> tuple[int, dict]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = Request(
        url,
        data=data,
        method="POST",
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    try:
        with urlopen(request) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        body = json.loads(error.read().decode("utf-8"))
        error.close()
        return error.code, body


def _post_multipart(
    url: str,
    *,
    fields: dict[str, str],
    filename: str,
    content: bytes,
) -> tuple[int, dict]:
    boundary = "----JingweiTestBoundary"
    chunks: list[bytes] = []
    for name, value in fields.items():
        chunks.append(
            (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
                f"{value}\r\n"
            ).encode("utf-8")
        )
    chunks.append(
        (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
            "Content-Type: application/octet-stream\r\n\r\n"
        ).encode("utf-8")
        + content
        + b"\r\n"
    )
    chunks.append(f"--{boundary}--\r\n".encode("utf-8"))
    body = b"".join(chunks)
    request = Request(
        url,
        data=body,
        method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    try:
        with urlopen(request) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        payload = json.loads(error.read().decode("utf-8"))
        error.close()
        return error.code, payload


if __name__ == "__main__":
    unittest.main()
