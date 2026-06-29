from collections import Counter
from pathlib import Path


MAX_LLM_FILE_BYTES = 50 * 1024
MIN_SOURCE_LINES = 20
TEST_PATH_MARKERS = {"test", "tests", "spec", "mock", "fixture"}
CONFIG_PATH_MARKERS = {"migration", "config", "setting"}
CONFIG_FILENAMES = {"setup.py", "setup.cfg", "conftest.py"}
EXCLUDE_DIRECTORIES = {
    "demo",
    "demos",
    "docs",
    "doc",
    "configs",
    "config",
    "cfg",
    "examples",
    "example",
    "scripts",
    "script",
    "tools",
    "tool",
    "tests",
    "test",
    ".github",
    "assets",
    "resources",
}


def prefilter_files(repo_path: str, source_paths: list[str]) -> tuple[list[str], list[dict]]:
    """
    Returns:
        suitable_paths: LLM assess에 보낼 파일 목록
        skipped: 사전 제외된 파일 [{"path": ..., "reason": ...}]
    """
    repo_root = Path(repo_path)
    suitable_paths: list[str] = []
    skipped: list[dict] = []

    for source_path in source_paths:
        reason = _skip_reason(repo_root, source_path)
        if reason is None:
            suitable_paths.append(source_path)
        else:
            skipped.append({"path": source_path, "reason": reason})

    return suitable_paths, skipped


def prefilter_reason_counts(skipped: list[dict]) -> dict[str, int]:
    return dict(Counter(str(item.get("reason") or "기타") for item in skipped))


def _skip_reason(repo_root: Path, source_path: str) -> str | None:
    path = (repo_root / source_path).resolve()
    name = path.name
    name_lower = name.lower()
    normalized_path = source_path.replace("\\", "/").lower()
    relative_parts = Path(source_path.replace("\\", "/")).parts

    if name.startswith("__init__"):
        return "초기화 파일"
    if len(relative_parts) > 1 and relative_parts[0].lower() in EXCLUDE_DIRECTORIES:
        return f"{relative_parts[0]}/ 디렉토리 파일"
    if name_lower in CONFIG_FILENAMES:
        return "설정 파일"
    if name_lower == "__main__.py":
        return "진입점 파일"
    if any(marker in normalized_path for marker in TEST_PATH_MARKERS):
        return "테스트 파일"
    if any(marker in normalized_path for marker in CONFIG_PATH_MARKERS):
        return "설정/마이그레이션 파일"

    try:
        stat = path.stat()
    except OSError:
        return None

    if stat.st_size == 0:
        return "빈 파일"
    if stat.st_size > MAX_LLM_FILE_BYTES:
        return "파일이 너무 큼"

    try:
        line_count = len(path.read_text(encoding="utf-8", errors="ignore").splitlines())
    except OSError:
        return None
    if line_count < MIN_SOURCE_LINES:
        return "너무 짧은 파일"

    return None
