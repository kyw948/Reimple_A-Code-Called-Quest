from pathlib import Path


def find_test_file(repo_path: Path, source_path: str) -> str | None:
    source = Path(source_path)
    stem = source.stem
    test_file_name = f"test_{stem}.py"

    candidates = [
        repo_path / "tests" / test_file_name,
        repo_path / "test" / test_file_name,
        repo_path / source.parent / test_file_name,
    ]

    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            return candidate.relative_to(repo_path).as_posix()

    matches = sorted(repo_path.rglob(test_file_name), key=lambda path: path.relative_to(repo_path).as_posix().lower())
    for match in matches:
        if match.is_file():
            return match.relative_to(repo_path).as_posix()

    alternate_pattern = f"*_{stem}_test.py"
    matches = sorted(repo_path.rglob(alternate_pattern), key=lambda path: path.relative_to(repo_path).as_posix().lower())
    for match in matches:
        if match.is_file():
            return match.relative_to(repo_path).as_posix()

    return None
