import os
from registry import scan_video_files


def test_lists_only_videos_sorted(tmp_path):
    (tmp_path / "b.mp4").write_bytes(b"12345")
    (tmp_path / "a.MKV").write_bytes(b"1")          # case-insensitive ext
    (tmp_path / "notes.txt").write_text("x")        # non-video: skipped
    (tmp_path / ".hidden.mp4").write_bytes(b"1")    # hidden: skipped
    (tmp_path / "sub").mkdir()                        # dir: skipped
    out = scan_video_files(str(tmp_path))
    assert [f["name"] for f in out] == ["a.MKV", "b.mp4"]      # sorted by name
    b = next(f for f in out if f["name"] == "b.mp4")
    assert b["size"] == 5 and isinstance(b["mtime"], int)


def test_missing_dir_returns_empty():
    assert scan_video_files("/no/such/dir/xyz") == []
