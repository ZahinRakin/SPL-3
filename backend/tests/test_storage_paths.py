"""Evidence paths remain usable when commands run from backend/ instead of the root."""
import uuid
from pathlib import Path

import pytest

from backend.services import storage


def test_upload_destination_is_independent_of_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    case_id, doc_id = uuid.uuid4(), uuid.uuid4()
    original = storage.document_path(case_id, doc_id, "evidence.txt")
    monkeypatch.chdir(tmp_path)

    assert storage.document_path(case_id, doc_id, "evidence.txt") == original
    assert original.is_absolute()


@pytest.mark.parametrize("absolute", [False, True])
def test_existing_evidence_can_be_found_and_removed_from_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, absolute: bool,
) -> None:
    project = tmp_path / "project"
    backend = project / "backend"
    backend.mkdir(parents=True)
    evidence = project / "data/uploads/evidence.txt"
    evidence.parent.mkdir(parents=True)
    evidence.write_text("test evidence", encoding="utf-8")
    stored_path = str(evidence) if absolute else "data/uploads/evidence.txt"
    monkeypatch.setattr(storage.settings, "BASE_DIR", project)
    monkeypatch.chdir(backend)

    resolved = storage.resolve_stored_path(stored_path)
    assert resolved.read_text(encoding="utf-8") == "test evidence"
    resolved.unlink()
    assert not evidence.exists()
