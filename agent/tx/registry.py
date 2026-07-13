"""Video-file registry for the TX generator: list decodable clips in the TX dir."""
import os

VIDEO_EXTS = (".mp4", ".mkv", ".avi", ".mov", ".ts", ".m4v", ".webm")


def scan_video_files(dir_path):
    """[{name, size, mtime}] for video files directly in dir_path, sorted by name.
    Missing/unreadable dir -> []. Hidden files, non-video extensions, and subdirs skipped."""
    out = []
    try:
        entries = os.listdir(dir_path)
    except OSError:
        return out
    for name in entries:
        if name.startswith("."):
            continue
        if not name.lower().endswith(VIDEO_EXTS):
            continue
        path = os.path.join(dir_path, name)
        try:
            st = os.stat(path)
        except OSError:
            continue
        if not os.path.isfile(path):
            continue
        out.append({"name": name, "size": int(st.st_size), "mtime": int(st.st_mtime)})
    out.sort(key=lambda f: f["name"])
    return out
