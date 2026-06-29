import os
import shutil
import subprocess
import uuid
from pathlib import Path
from time import perf_counter

from pydantic import BaseModel
from sqlalchemy import insert, select, update

from app.core.errors import AppError
from app.db.database import engine, files, problems, submissions
from app.services.llm_grader import grade_llm_submission
from app.services.problem_tree import unlock_dependents
from app.services.project_service import _get_project_row, _utc_now
from app.services.repo_analyzer import EXCLUDED_NAMES


RUNNER_SUBMIT_TIMEOUT_SECONDS = int(os.getenv("RUNNER_SUBMIT_TIMEOUT_SECONDS", "30"))


class ProblemSubmitRequest(BaseModel):
    code: str
    overwrite: bool = False


class ProblemSubmitResponse(BaseModel):
    passed: bool
    feedback: str | None = None
    score: int | None = None
    test_cases: list[dict] | None = None
    stdout: str | None
    stderr: str | None
    duration_ms: int
    saved_path: str | None
    grading_method: str


def submit_problem(problem_id: str, payload: ProblemSubmitRequest) -> ProblemSubmitResponse:
    problem = _get_problem_row(problem_id)
    if problem["status"] == "locked":
        raise AppError("PROBLEM_LOCKED", "이 문제를 풀려면 먼저 의존하는 문제를 완료하세요.")
    project = _get_project_row(problem["project_id"])

    if problem["grading_method"] == "llm":
        return _submit_llm_problem(problem, project, payload)

    return _submit_pytest_problem(problem, project, payload)


def _submit_pytest_problem(problem, project, payload: ProblemSubmitRequest) -> ProblemSubmitResponse:
    submission_id = str(uuid.uuid4())
    temp_dir = Path("/tmp") / f"runner_{submission_id}"

    started = perf_counter()
    try:
        _prepare_runner_directory(Path(project["repo_path"]), temp_dir)
        _write_submission_code(temp_dir, problem["source_path"], payload.code)
        result = subprocess.run(
            ["python", "-m", "pytest", problem["test_path"], "-q", "--tb=short"],
            cwd=temp_dir,
            timeout=RUNNER_SUBMIT_TIMEOUT_SECONDS,
            capture_output=True,
            text=True,
        )
        duration_ms = int((perf_counter() - started) * 1000)
    except subprocess.TimeoutExpired as exc:
        duration_ms = int((perf_counter() - started) * 1000)
        _record_submission(
            submission_id=submission_id,
            problem_id=problem["id"],
            code=payload.code,
            passed=False,
            stdout=_timeout_output(exc.stdout),
            stderr=_timeout_output(exc.stderr) or "Runner timed out.",
            duration_ms=duration_ms,
        )
        raise AppError("RUNNER_TIMEOUT", "Runner timed out.")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

    passed = result.returncode == 0
    saved_path = _save_passed_submission(project, problem, payload) if passed else None

    _record_submission(
        submission_id=submission_id,
        problem_id=problem["id"],
        code=payload.code,
        passed=passed,
        stdout=result.stdout,
        stderr=result.stderr,
        duration_ms=duration_ms,
    )

    if passed:
        _mark_problem_passed(problem)

    return ProblemSubmitResponse(
        passed=passed,
        feedback=None,
        score=None,
        test_cases=None,
        stdout=result.stdout,
        stderr=result.stderr,
        duration_ms=duration_ms,
        saved_path=saved_path,
        grading_method="pytest",
    )


def _submit_llm_problem(problem, project, payload: ProblemSubmitRequest) -> ProblemSubmitResponse:
    submission_id = str(uuid.uuid4())
    started = perf_counter()
    full_file_code = _read_problem_source(Path(project["repo_path"]), problem["source_path"])
    grade = grade_llm_submission(
        problem["original_code"] or "",
        payload.code,
        problem["target_symbol"],
        full_file_code=full_file_code,
        source_path=problem["source_path"],
    )
    duration_ms = int((perf_counter() - started) * 1000)
    saved_path = _save_passed_submission(project, problem, payload) if grade.passed else None

    _record_submission(
        submission_id=submission_id,
        problem_id=problem["id"],
        code=payload.code,
        passed=grade.passed,
        stdout=None,
        stderr=None,
        duration_ms=duration_ms,
    )

    if grade.passed:
        _mark_problem_passed(problem)

    return ProblemSubmitResponse(
        passed=grade.passed,
        feedback=grade.feedback,
        score=grade.score,
        test_cases=[result.__dict__ for result in grade.test_cases] if grade.test_cases is not None else None,
        stdout=None,
        stderr=None,
        duration_ms=duration_ms,
        saved_path=saved_path,
        grading_method="llm",
    )


def _get_problem_row(problem_id: str):
    with engine.begin() as connection:
        row = connection.execute(select(problems).where(problems.c.id == problem_id)).mappings().first()

    if row is None:
        raise AppError("PROBLEM_NOT_FOUND", "Problem not found.")
    return row


def _read_problem_source(repo_path: Path, source_path: str) -> str:
    path = (repo_path / source_path).resolve()
    try:
        path.relative_to(repo_path.resolve())
    except ValueError:
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _prepare_runner_directory(repo_path: Path, temp_dir: Path) -> None:
    if temp_dir.exists():
        shutil.rmtree(temp_dir)
    shutil.copytree(repo_path, temp_dir, ignore=_ignore_excluded_names)


def _ignore_excluded_names(_: str, names: list[str]) -> set[str]:
    return {name for name in names if name in EXCLUDED_NAMES}


def _write_submission_code(temp_dir: Path, source_path: str, code: str) -> None:
    destination = (temp_dir / source_path).resolve()
    try:
        destination.relative_to(temp_dir.resolve())
    except ValueError:
        raise AppError("PROBLEM_NOT_FOUND", "Source file not found.")

    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(code, encoding="utf-8")


def _save_passed_submission(project, problem, payload: ProblemSubmitRequest) -> str | None:
    practice_root = Path(project["practice_root_path"])
    destination = practice_root / problem["source_path"]
    if destination.exists() and not payload.overwrite:
        return None

    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(payload.code, encoding="utf-8")
    return problem["source_path"]


def _mark_problem_passed(problem) -> None:
    now = _utc_now()
    with engine.begin() as connection:
        connection.execute(update(problems).where(problems.c.id == problem["id"]).values(status="passed", updated_at=now))
        connection.execute(update(files).where(files.c.id == problem["file_id"]).values(status="passed", updated_at=now))
    unlock_dependents(problem["project_id"], problem["id"])


def _record_submission(
    submission_id: str,
    problem_id: str,
    code: str,
    passed: bool,
    stdout: str | None,
    stderr: str | None,
    duration_ms: int,
) -> None:
    with engine.begin() as connection:
        connection.execute(
            insert(submissions).values(
                id=submission_id,
                problem_id=problem_id,
                code=code,
                passed=1 if passed else 0,
                stdout=stdout,
                stderr=stderr,
                duration_ms=duration_ms,
                created_at=_utc_now(),
            )
        )


def _timeout_output(output) -> str:
    if output is None:
        return ""
    if isinstance(output, bytes):
        return output.decode(errors="replace")
    return str(output)
