import os
import subprocess
from pathlib import Path

from fastapi import HTTPException
from pydantic import BaseModel

from app.core.errors import AppError


DEFAULT_CLONE_BASE_PATH = "~/.codepractice/repos"


class RepoCloneRequest(BaseModel):
    github_url: str
    clone_path: str | None = None


class RepoCloneResponse(BaseModel):
    repo_path: str
    already_exists: bool = False


def clone_repo(payload: RepoCloneRequest) -> RepoCloneResponse:
    github_url = payload.github_url.strip()
    if not github_url.startswith("https://github.com/"):
        raise AppError("CLONE_FAILED", "Invalid GitHub URL.")

    clone_path = _resolve_clone_path(github_url, payload.clone_path)
    if clone_path.exists():
        raise HTTPException(
            status_code=409,
            detail={
                "error_code": "REPO_ALREADY_EXISTS",
                "message": "Repository already cloned.",
                "repo_path": str(clone_path),
            },
        )

    clone_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        result = subprocess.run(
            ["git", "clone", "--depth", "1", github_url, str(clone_path)],
            timeout=120,
            capture_output=True,
        )
    except FileNotFoundError as exc:
        raise AppError("CLONE_FAILED", "Git is not installed or not available in PATH.") from exc
    except subprocess.TimeoutExpired as exc:
        raise AppError("CLONE_FAILED", "Repository clone timed out.") from exc

    if result.returncode != 0:
        raise AppError("CLONE_FAILED", "Repository clone failed.")

    return RepoCloneResponse(repo_path=str(clone_path), already_exists=False)


def _resolve_clone_path(github_url: str, clone_path: str | None) -> Path:
    if clone_path and clone_path.strip():
        return Path(clone_path).expanduser().resolve()

    base_path = Path(os.getenv("GITHUB_CLONE_BASE_PATH", DEFAULT_CLONE_BASE_PATH)).expanduser().resolve()
    return base_path / _repo_name_from_url(github_url)


def _repo_name_from_url(github_url: str) -> str:
    repo_name = github_url.rstrip("/").split("/")[-1]
    if repo_name.endswith(".git"):
        repo_name = repo_name[:-4]

    if not repo_name:
        raise AppError("CLONE_FAILED", "Invalid GitHub URL.")

    return repo_name
