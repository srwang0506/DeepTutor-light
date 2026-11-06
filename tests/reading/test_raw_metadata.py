"""External-drive metadata must never replace the user's original document."""

from deeptutor.reading.store import ReadingStore


def test_raw_file_ignores_macos_metadata(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "._paper.pdf").write_bytes(b"AppleDouble metadata")
    (raw / ".DS_Store").write_bytes(b"Finder metadata")
    original = raw / "paper.pdf"
    original.write_bytes(b"%PDF-1.7 original")
    assert ReadingStore._find_file_in_dir(raw) == original


def test_metadata_alone_is_not_a_readable_original(tmp_path):
    (tmp_path / "._paper.pdf").write_bytes(b"AppleDouble metadata")
    assert ReadingStore._find_file_in_dir(tmp_path) is None
