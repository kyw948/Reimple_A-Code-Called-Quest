import json
import re
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field
from sqlalchemy import delete, func, insert, select, update

from app.core.errors import AppError
from app.db.database import engine, files, projects
from app.services.paper_parser import promote_figures_for_project
from app.services.repo_analyzer import EXCLUDED_NAMES


class ProjectCreateRequest(BaseModel):
    repo_path: str | None = None
    practice_root_path: str | None = None
    target_extensions: list[str] = Field(default_factory=list)
    paper_source: str | None = None
    paper_url: str | None = None
    paper_title: str | None = None
    paper_abstract: str | None = None
    paper_content: str | None = None
    paper_metadata: dict | None = None


class ProjectCreateResponse(BaseModel):
    project_id: str


class ProjectUpdateRequest(BaseModel):
    target_extensions: list[str]


class ProjectUpdateResponse(BaseModel):
    id: str
    repo_path: str
    practice_root_path: str
    target_extensions: list[str]
    updated_at: str


class ProjectSummary(BaseModel):
    id: str
    repo_path: str
    practice_root_path: str
    target_extensions: list[str]
    paper_source: str | None = None
    paper_url: str | None = None
    paper_title: str | None = None
    paper_abstract: str | None = None
    generated_repo_path: str | None = None
    created_at: str
    updated_at: str


class ProjectListResponse(BaseModel):
    projects: list[ProjectSummary]


class FileCounts(BaseModel):
    total: int
    target: int
    passed: int
    pending: int


class ProjectDetail(ProjectSummary):
    file_counts: FileCounts
    analysis_status: str = "pending"
    assess_status: str = "pending"
    project_summary: dict = Field(default_factory=dict)
    architecture: dict = Field(default_factory=dict)
    dependency_graph: dict = Field(default_factory=dict)
    paper_content: str | None = None
    paper_metadata: dict = Field(default_factory=dict)


class ProjectSetupResponse(BaseModel):
    copied_files: int
    skipped_files: int


def create_project(payload: ProjectCreateRequest) -> ProjectCreateResponse:
    is_paper_project = bool(payload.paper_source)
    repo_path = None if is_paper_project else _resolve_existing_repo(payload.repo_path or "")
    raw_practice_root_path = (payload.practice_root_path or "").strip()
    if raw_practice_root_path:
        practice_root_path = Path(raw_practice_root_path).expanduser().resolve()
    elif is_paper_project:
        practice_root_path = Path(get_default_paper_practice_root(payload.paper_title or "paper")).resolve()
    else:
        practice_root_path = Path(get_default_practice_root(str(repo_path))).resolve()
    practice_root_path.mkdir(parents=True, exist_ok=True)

    project_id = str(uuid.uuid4())
    now = _utc_now()

    paper_metadata = dict(payload.paper_metadata or {})
    figure_count = 0
    if is_paper_project:
        figure_count = promote_figures_for_project(project_id, paper_metadata.get("figure_token"))
        if figure_count:
            paper_metadata["figure_count"] = figure_count
        paper_metadata.pop("figure_token", None)

    with engine.begin() as connection:
        connection.execute(
            insert(projects).values(
                id=project_id,
                repo_path="" if repo_path is None else str(repo_path),
                practice_root_path=str(practice_root_path),
                target_extensions=json.dumps(payload.target_extensions),
                paper_source=payload.paper_source,
                paper_url=payload.paper_url,
                paper_title=payload.paper_title,
                paper_abstract=payload.paper_abstract,
                paper_content=payload.paper_content,
                paper_metadata=json.dumps(paper_metadata, ensure_ascii=False),
                generated_repo_path=None,
                analysis_status="pending",
                created_at=now,
                updated_at=now,
            )
        )

    return ProjectCreateResponse(project_id=project_id)


def get_default_practice_root(repo_path: str) -> str:
    repo_name = Path(repo_path).name
    base = Path.home() / ".codepractice" / "practice" / repo_name
    base.mkdir(parents=True, exist_ok=True)
    return str(base)


def get_default_paper_practice_root(paper_title: str) -> str:
    slug = _slugify(paper_title) or "paper"
    base = Path.home() / ".codepractice" / "practice" / slug
    base.mkdir(parents=True, exist_ok=True)
    return str(base)


def list_projects() -> ProjectListResponse:
    with engine.begin() as connection:
        rows = connection.execute(select(projects).order_by(projects.c.created_at.desc())).mappings().all()

    return ProjectListResponse(projects=[_project_summary(row) for row in rows])


def get_project_detail(project_id: str) -> ProjectDetail:
    project = _get_project_row(project_id)
    return ProjectDetail(
        **_project_summary(project).model_dump(),
        file_counts=_file_counts(project_id),
        analysis_status=project["analysis_status"],
        assess_status=project["assess_status"] or "pending",
        project_summary=_loads_object(project["project_summary"]),
        architecture=_loads_object(project["architecture"]),
        dependency_graph=_loads_object(project["dependency_graph"]),
        paper_content=project["paper_content"],
        paper_metadata=_loads_object(project["paper_metadata"]),
    )


def update_project(project_id: str, payload: ProjectUpdateRequest) -> ProjectUpdateResponse:
    project = _get_project_row(project_id)
    target_extensions = _normalize_extensions(payload.target_extensions)
    now = _utc_now()

    with engine.begin() as connection:
        connection.execute(
            update(projects)
            .where(projects.c.id == project_id)
            .values(target_extensions=json.dumps(target_extensions), updated_at=now)
        )

    return ProjectUpdateResponse(
        id=project["id"],
        repo_path=project["repo_path"],
        practice_root_path=project["practice_root_path"],
        target_extensions=target_extensions,
        updated_at=now,
    )


def setup_project(project_id: str) -> ProjectSetupResponse:
    project = _get_project_row(project_id)
    if project["paper_source"] and not project["repo_path"]:
        return ProjectSetupResponse(copied_files=0, skipped_files=0)

    repo_path = Path(project["repo_path"])
    practice_root_path = Path(project["practice_root_path"])
    target_extensions = set(json.loads(project["target_extensions"]))
    if project["paper_source"] and not target_extensions:
        target_extensions = {".py"}

    copied_files = 0
    skipped_files = 0
    now = _utc_now()

    with engine.begin() as connection:
        connection.execute(delete(files).where(files.c.project_id == project_id))

        for source_file in _iter_repo_files(repo_path):
            relative_path = source_file.relative_to(repo_path).as_posix()
            extension = source_file.suffix
            is_target = extension in target_extensions
            status = "pending" if is_target else "skipped"

            connection.execute(
                insert(files).values(
                    id=str(uuid.uuid4()),
                    project_id=project_id,
                    source_path=relative_path,
                    extension=extension,
                    is_target=1 if is_target else 0,
                    status=status,
                    created_at=now,
                    updated_at=now,
                )
            )

            if is_target:
                skipped_files += 1
                continue

            destination = practice_root_path / relative_path
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_file, destination)
            copied_files += 1

    return ProjectSetupResponse(copied_files=copied_files, skipped_files=skipped_files)


def _resolve_existing_repo(repo_path: str) -> Path:
    resolved = Path(repo_path).expanduser().resolve()
    if not resolved.exists() or not resolved.is_dir():
        raise AppError("INVALID_REPO_PATH", "Repo path does not exist.")
    return resolved


def _get_project_row(project_id: str):
    with engine.begin() as connection:
        row = connection.execute(select(projects).where(projects.c.id == project_id)).mappings().first()

    if row is None:
        raise AppError("PROJECT_NOT_FOUND", "Project not found.")
    return row


def _project_summary(row) -> ProjectSummary:
    return ProjectSummary(
        id=row["id"],
        repo_path=row["repo_path"],
        practice_root_path=row["practice_root_path"],
        target_extensions=json.loads(row["target_extensions"]),
        paper_source=row["paper_source"],
        paper_url=row["paper_url"],
        paper_title=row["paper_title"],
        paper_abstract=row["paper_abstract"],
        generated_repo_path=row["generated_repo_path"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _normalize_extensions(extensions: list[str]) -> list[str]:
    normalized = []
    seen = set()

    for extension in extensions:
        value = extension.strip().lower()
        if not value:
            continue
        if not value.startswith("."):
            value = f".{value}"
        if value in seen:
            continue
        normalized.append(value)
        seen.add(value)

    return normalized


def _file_counts(project_id: str) -> FileCounts:
    with engine.begin() as connection:
        total = connection.execute(select(func.count()).select_from(files).where(files.c.project_id == project_id)).scalar_one()
        target = connection.execute(
            select(func.count()).select_from(files).where(files.c.project_id == project_id, files.c.is_target == 1)
        ).scalar_one()
        passed = connection.execute(
            select(func.count()).select_from(files).where(files.c.project_id == project_id, files.c.status == "passed")
        ).scalar_one()
        pending = connection.execute(
            select(func.count()).select_from(files).where(files.c.project_id == project_id, files.c.status == "pending")
        ).scalar_one()

    return FileCounts(total=total, target=target, passed=passed, pending=pending)


def _loads_object(raw: str | None) -> dict:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _slugify(value: str) -> str:
    lowered = value.strip().lower()
    lowered = re.sub(r"[^a-z0-9가-힣]+", "-", lowered)
    lowered = re.sub(r"-+", "-", lowered).strip("-")
    return lowered[:80]


def _iter_repo_files(root: Path) -> list[Path]:
    return sorted(
        (path for path in root.rglob("*") if path.is_file() and not _is_excluded(path, root)),
        key=lambda path: path.relative_to(root).as_posix().lower(),
    )


def _is_excluded(path: Path, root: Path) -> bool:
    return any(part in EXCLUDED_NAMES for part in path.relative_to(root).parts)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
