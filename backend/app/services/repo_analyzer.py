from collections import Counter
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from app.core.errors import AppError


EXCLUDED_NAMES = {".git", "__pycache__", ".pytest_cache", ".venv", "venv", "node_modules", ".DS_Store"}


class FileTreeNode(BaseModel):
    name: str
    path: str
    type: Literal["file", "directory"]
    children: list["FileTreeNode"] | None = None
    extension: str | None = None
    size_bytes: int | None = None


class RepoAnalyzeRequest(BaseModel):
    repo_path: str


class RepoAnalyzeResponse(BaseModel):
    repo_path: str
    file_tree: list[FileTreeNode]
    extension_stats: dict[str, int]


def analyze_repo(repo_path: str) -> RepoAnalyzeResponse:
    root = Path(repo_path).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        raise AppError("INVALID_REPO_PATH", "Repo path does not exist.")

    extension_stats: Counter[str] = Counter()
    file_tree = [_build_node(child, root, extension_stats) for child in _iter_visible_children(root)]

    return RepoAnalyzeResponse(
        repo_path=str(root),
        file_tree=file_tree,
        extension_stats=dict(sorted(extension_stats.items())),
    )


def _iter_visible_children(path: Path) -> list[Path]:
    children = [child for child in path.iterdir() if child.name not in EXCLUDED_NAMES]
    return sorted(children, key=lambda child: (child.is_file(), child.name.lower()))


def _build_node(path: Path, root: Path, extension_stats: Counter[str]) -> FileTreeNode:
    relative_path = path.relative_to(root).as_posix()

    if path.is_dir():
        return FileTreeNode(
            name=path.name,
            path=relative_path,
            type="directory",
            children=[_build_node(child, root, extension_stats) for child in _iter_visible_children(path)],
        )

    extension = path.suffix
    if extension:
        extension_stats[extension] += 1

    return FileTreeNode(
        name=path.name,
        path=relative_path,
        type="file",
        extension=extension,
        size_bytes=path.stat().st_size,
    )
