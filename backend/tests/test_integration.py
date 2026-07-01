import json
import os
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select

from app.core.errors import AppError
from app.db import database
from app.main import app
from app.services import (
    context_collector,
    file_assessor,
    file_prefilter,
    hint_generator,
    llm_client,
    llm_grader,
    paper_codegen,
    paper_parser,
    paper_planner,
    problem_generator,
    project_analyzer,
    project_service,
    problem_tree,
    repo_cloner,
    runner,
    warmup_generator,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SAMPLE_REPO = PROJECT_ROOT / "samples" / "python_basic"
TEMP_SAMPLE_FILES = [
    SAMPLE_REPO / "src" / "config_tmp.py",
    SAMPLE_REPO / "src" / "large_tmp.py",
    SAMPLE_REPO / "src" / "llm_only_tmp.py",
    SAMPLE_REPO / "src" / "llm_submit_tmp.py",
    SAMPLE_REPO / "src" / "multi_tmp.py",
    SAMPLE_REPO / "src" / "partial_tmp.py",
    SAMPLE_REPO / "src" / "imported_tmp.py",
    SAMPLE_REPO / "src" / "caller_tmp.py",
    SAMPLE_REPO / "src" / "class_tmp.py",
    SAMPLE_REPO / "src" / "non_python_tmp.js",
    *[SAMPLE_REPO / "src" / f"batch_{index}_tmp.py" for index in range(6)],
]

requires_gemini = pytest.mark.skipif(
    not os.environ.get("GEMINI_API_KEY"),
    reason="GEMINI_API_KEY not set",
)


def _fake_problem_markdown(prompt: str) -> str:
    return (
        "### add_numbers 함수 구현\n\n"
        "이 함수는 프로그램 안에서 입력 값을 계산 가능한 결과로 바꾸는 연습 대상입니다.\n\n"
        "#### 입력\n"
        "- 함수 시그니처에 정의된 파라미터를 받습니다.\n\n"
        "#### 출력\n"
        "- 계산된 결과를 반환합니다.\n\n"
        "#### 동작\n"
        "- 입력 값을 확인하고 기대한 규칙에 따라 결과를 만듭니다.\n\n"
        "#### 참고\n"
        "- 난이도는 테스트에서 고정된 설명으로 대체됩니다.\n\n"
        "#### 주의사항\n"
        "- 원본 구현 코드는 포함하지 않습니다.\n"
    )


@pytest.fixture(autouse=True)
def cleanup_temp_sample_files():
    for path in TEMP_SAMPLE_FILES:
        path.unlink(missing_ok=True)
    yield
    for path in TEMP_SAMPLE_FILES:
        path.unlink(missing_ok=True)


@pytest.fixture()
def client(tmp_path, monkeypatch):
    test_engine = create_engine(
        f"sqlite:///{tmp_path / 'test.db'}",
        connect_args={"check_same_thread": False},
    )

    monkeypatch.setattr(database, "engine", test_engine)
    monkeypatch.setattr(file_assessor, "engine", test_engine)
    monkeypatch.setattr(hint_generator, "engine", test_engine)
    monkeypatch.setattr(project_analyzer, "engine", test_engine)
    monkeypatch.setattr(project_service, "engine", test_engine)
    monkeypatch.setattr(paper_codegen, "engine", test_engine)
    monkeypatch.setattr(paper_planner, "engine", test_engine)
    monkeypatch.setattr(problem_generator, "engine", test_engine)
    monkeypatch.setattr(problem_tree, "engine", test_engine)
    monkeypatch.setattr(runner, "engine", test_engine)
    monkeypatch.setattr(warmup_generator, "engine", test_engine)
    monkeypatch.setattr(problem_generator, "_call_problem_assets_llm", lambda prompt: None)
    monkeypatch.setattr(problem_generator, "_call_problem_description_llm", _fake_problem_markdown)

    database.metadata.create_all(bind=test_engine)

    with TestClient(app) as test_client:
        yield test_client

    test_engine.dispose()


@pytest.fixture()
def practice_root(tmp_path):
    return tmp_path / "practice_output"


def test_analyze_valid_repo(client):
    response = client.post("/api/repos/analyze", json={"repo_path": str(SAMPLE_REPO)})

    assert response.status_code == 200
    body = response.json()
    assert body["extension_stats"] == {".md": 1, ".py": 4, ".toml": 1}
    paths = _tree_paths(body["file_tree"])
    assert "src/math_utils.py" in paths
    assert all(".git" not in path for path in paths)
    assert all("__pycache__" not in path for path in paths)


def test_analyze_invalid_repo(client):
    response = client.post("/api/repos/analyze", json={"repo_path": str(SAMPLE_REPO / "missing")})

    assert response.status_code == 400
    assert response.json()["error_code"] == "INVALID_REPO_PATH"


def test_clone_repo_success(client, tmp_path, monkeypatch):
    clone_base = tmp_path / "repos"
    calls = []

    def fake_run(command, timeout, capture_output):
        calls.append((command, timeout, capture_output))
        Path(command[-1]).mkdir(parents=True)
        return repo_cloner.subprocess.CompletedProcess(command, 0)

    monkeypatch.setenv("GITHUB_CLONE_BASE_PATH", str(clone_base))
    monkeypatch.setattr(repo_cloner.subprocess, "run", fake_run)

    response = client.post(
        "/api/repos/clone",
        json={"github_url": "https://github.com/vitae-transformer/vitpose"},
    )

    expected_path = clone_base / "vitpose"
    assert response.status_code == 200
    assert response.json() == {"repo_path": str(expected_path.resolve()), "already_exists": False}
    assert calls == [
        (
            ["git", "clone", "--depth", "1", "https://github.com/vitae-transformer/vitpose", str(expected_path.resolve())],
            120,
            True,
        )
    ]


def test_clone_repo_invalid_url(client):
    response = client.post("/api/repos/clone", json={"github_url": "https://invalid-url"})

    assert response.status_code == 422
    assert response.json()["error_code"] == "CLONE_FAILED"


def test_clone_repo_already_exists(client, tmp_path, monkeypatch):
    clone_base = tmp_path / "repos"
    repo_path = clone_base / "vitpose"
    repo_path.mkdir(parents=True)
    monkeypatch.setenv("GITHUB_CLONE_BASE_PATH", str(clone_base))

    response = client.post(
        "/api/repos/clone",
        json={"github_url": "https://github.com/vitae-transformer/vitpose"},
    )

    assert response.status_code == 409
    body = response.json()
    assert body["error_code"] == "REPO_ALREADY_EXISTS"
    assert body["message"] == "Repository already cloned."
    assert body["repo_path"] == str(repo_path.resolve())


def test_clone_repo_git_missing(client, tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_CLONE_BASE_PATH", str(tmp_path / "repos"))

    def fake_run(command, timeout, capture_output):
        raise FileNotFoundError

    monkeypatch.setattr(repo_cloner.subprocess, "run", fake_run)

    response = client.post(
        "/api/repos/clone",
        json={"github_url": "https://github.com/vitae-transformer/vitpose"},
    )

    assert response.status_code == 422
    assert response.json()["error_code"] == "CLONE_FAILED"


def test_parse_arxiv_paper(client, monkeypatch):
    arxiv_xml = """<?xml version="1.0" encoding="UTF-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <entry>
        <title>ViTPose: Simple Vision Transformer Baselines for Human Pose Estimation</title>
        <summary>ViTPose abstract text.</summary>
        <published>2022-04-26T00:00:00Z</published>
        <author><name>Yufei Xu</name></author>
      </entry>
    </feed>
    """

    class FakeResponse:
        def __init__(self, text="", content=b""):
            self.text = text
            self.content = content

        def raise_for_status(self):
            return None

    def fake_get(url, timeout):
        if "export.arxiv.org" in url:
            return FakeResponse(text=arxiv_xml)
        return FakeResponse(content=b"%PDF")

    monkeypatch.setattr(paper_parser.requests, "get", fake_get)
    monkeypatch.setattr(paper_parser, "extract_pdf_text", lambda _: "Full paper text")

    response = client.post("/api/papers/parse", json={"arxiv_url": "https://arxiv.org/abs/2204.12484"})

    assert response.status_code == 200
    data = response.json()
    assert data["source"] == "arxiv"
    assert data["title"].startswith("ViTPose")
    assert data["abstract"] == "ViTPose abstract text."
    assert data["content"] == "Full paper text"


def test_parse_invalid_arxiv_url(client):
    response = client.post("/api/papers/parse", json={"arxiv_url": "https://example.com/not-arxiv"})

    assert response.status_code == 400
    assert response.json()["error_code"] == "INVALID_ARXIV_URL"


def test_parse_uploaded_pdf(client, monkeypatch):
    monkeypatch.setattr(paper_parser, "extract_pdf_text", lambda _: "Uploaded Paper Title\n\nAbstract\nBody")

    response = client.post(
        "/api/papers/parse",
        files={"file": ("paper.pdf", b"%PDF", "application/pdf")},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["source"] == "pdf"
    assert data["title"] == "Uploaded Paper Title"
    assert data["content"].startswith("Uploaded Paper Title")


def test_create_paper_project(client, tmp_path, monkeypatch):
    default_base = tmp_path / "paper_practice"
    monkeypatch.setattr(
        project_service,
        "get_default_paper_practice_root",
        lambda paper_title: str(default_base / "vitpose-paper"),
    )

    response = client.post(
        "/api/projects",
        json={
            "paper_source": "arxiv",
            "paper_url": "https://arxiv.org/abs/2204.12484",
            "paper_title": "ViTPose Paper",
            "paper_abstract": "A pose estimation paper.",
            "paper_content": "Full paper content",
            "paper_metadata": {"authors": ["Yufei Xu"], "year": "2022"},
        },
    )

    assert response.status_code == 200
    project_id = response.json()["project_id"]
    detail = client.get(f"/api/projects/{project_id}")
    assert detail.status_code == 200
    data = detail.json()
    assert data["repo_path"] == ""
    assert data["paper_source"] == "arxiv"
    assert data["paper_title"] == "ViTPose Paper"
    assert data["paper_content"] == "Full paper content"
    assert data["paper_metadata"]["authors"] == ["Yufei Xu"]
    assert Path(data["practice_root_path"]) == (default_base / "vitpose-paper").resolve()


def test_plan_paper_project(client, tmp_path, monkeypatch):
    monkeypatch.setattr(
        project_service,
        "get_default_paper_practice_root",
        lambda paper_title: str(tmp_path / "paper_practice"),
    )
    project_response = client.post(
        "/api/projects",
        json={
            "paper_source": "arxiv",
            "paper_url": "https://arxiv.org/abs/2204.12484",
            "paper_title": "ViTPose Paper",
            "paper_abstract": "A pose estimation paper.",
            "paper_content": "Full paper content",
            "paper_metadata": {"authors": ["Yufei Xu"], "year": "2022"},
        },
    )
    project_id = project_response.json()["project_id"]
    calls: list[dict] = []

    def fake_validated_call(prompt, system_instruction, required_fields, max_retries=1):
        calls.append(required_fields)
        if "summary" in required_fields:
            return {
                "summary": "ViTPose 핵심 구현 계획",
                "domain": "computer_vision",
                "framework": "pytorch",
                "components": [
                    {
                        "name": "Patch Embedding",
                        "description": "이미지를 패치 토큰으로 변환",
                        "category": "model_component",
                        "importance": "core",
                    }
                ],
                "key_algorithms": ["Vision Transformer patch embedding"],
                "required_libraries": ["torch", "numpy"],
            }
        if "files" in required_fields:
            return {
                "files": [
                    {
                        "path": "models/embedding.py",
                        "description": "Patch Embedding 구현",
                        "classes": ["PatchEmbedding"],
                        "functions": [],
                        "depends_on": [],
                    }
                ]
            }
        return {
            "implementation_order": ["config.py", "models/embedding.py"],
            "specifications": [
                {
                    "file": "models/embedding.py",
                    "class": "PatchEmbedding",
                    "methods": {
                        "forward": {
                            "params": {"x": "Tensor [B,C,H,W]"},
                            "returns": "Tensor [B,N,D]",
                            "algorithm": "Conv2d 후 flatten",
                            "paper_reference": "Section 3",
                        }
                    },
                }
            ],
        }

    monkeypatch.setattr(paper_planner.llm_client, "call_gemini_with_validation", fake_validated_call)

    response = client.post(f"/api/papers/{project_id}/plan")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "planned"
    assert data["overall_plan"]["components"][0]["name"] == "Patch Embedding"
    assert data["architecture"]["files"][0]["path"] == "models/embedding.py"
    assert data["logic_design"]["implementation_order"] == ["config.py", "models/embedding.py"]
    assert len(calls) == 3

    detail = client.get(f"/api/projects/{project_id}").json()
    assert detail["analysis_status"] == "planned"
    assert detail["project_summary"]["summary"] == "ViTPose 핵심 구현 계획"
    assert detail["architecture"]["files"][0]["classes"] == ["PatchEmbedding"]
    assert detail["dependency_graph"]["specifications"][0]["class"] == "PatchEmbedding"

    cached = client.post(f"/api/papers/{project_id}/plan")
    assert cached.status_code == 200
    assert len(calls) == 3


def test_plan_rejects_non_paper_project(client, practice_root):
    project_id = _create_project(client, practice_root)

    response = client.post(f"/api/papers/{project_id}/plan")

    assert response.status_code == 400
    assert response.json()["error_code"] == "INVALID_PROJECT_MODE"


def test_generate_paper_code_background(client, tmp_path, monkeypatch):
    project_id = _create_planned_paper_project(client, tmp_path, monkeypatch)
    generated_base = tmp_path / "generated"

    def fake_save(project_id_arg, generated_files):
        base = generated_base / project_id_arg
        base.mkdir(parents=True, exist_ok=True)
        for file_path, code in generated_files.items():
            destination = base / file_path
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(code, encoding="utf-8")
        (base / "pyproject.toml").write_text('[tool.pytest.ini_options]\npythonpath = ["."]\n', encoding="utf-8")
        return str(base)

    def fake_gemini(prompt, system_instruction=None, response_mime_type=None):
        if "config.py" in prompt:
            return "MODEL_DIM: int = 128\nNUM_HEADS: int = 4\n"
        return (
            "from config import MODEL_DIM\n\n"
            "class PatchEmbedding:\n"
            "    \"\"\"Patch embedding placeholder.\"\"\"\n"
            "    def __init__(self, dim: int = MODEL_DIM) -> None:\n"
            "        self.dim = dim\n\n"
            "    def forward(self, x):\n"
            "        return x\n"
        )

    monkeypatch.setattr(paper_codegen, "save_generated_code", fake_save)
    monkeypatch.setattr(paper_codegen.llm_client, "call_gemini", fake_gemini)

    start = client.post(f"/api/papers/{project_id}/generate-code")
    assert start.status_code == 200
    assert start.json()["status"] == "started"

    status = None
    for _ in range(30):
        response = client.get(f"/api/papers/{project_id}/codegen-status")
        assert response.status_code == 200
        status = response.json()
        if status["status"] == "completed":
            break
        time.sleep(0.05)

    assert status is not None
    assert status["status"] == "completed"
    assert status["generated_files"] == 2
    assert status["completed_files"] == ["config.py", "models/embedding.py"]
    assert "config.py" in status["files"]
    assert "models/embedding.py" in status["files"]

    detail = client.get(f"/api/projects/{project_id}").json()
    assert detail["analysis_status"] == "code_generated"
    assert Path(detail["repo_path"]).exists()
    assert detail["generated_repo_path"] == detail["repo_path"]
    assert (Path(detail["repo_path"]) / "models" / "embedding.py").exists()

    setup = client.post(f"/api/projects/{project_id}/setup")
    assert setup.status_code == 200
    after_setup = client.get(f"/api/projects/{project_id}").json()
    assert after_setup["file_counts"]["target"] >= 2


def test_generate_paper_code_requires_plan(client, tmp_path, monkeypatch):
    monkeypatch.setattr(
        project_service,
        "get_default_paper_practice_root",
        lambda paper_title: str(tmp_path / "paper_practice"),
    )
    project = client.post(
        "/api/projects",
        json={
            "paper_source": "arxiv",
            "paper_title": "Unplanned Paper",
            "paper_content": "content",
        },
    )
    project_id = project.json()["project_id"]

    response = client.post(f"/api/papers/{project_id}/generate-code")

    assert response.status_code == 400
    assert response.json()["error_code"] == "PAPER_PLAN_REQUIRED"


def test_create_project(client, practice_root):
    project_id = _create_project(client, practice_root)

    assert project_id


def test_create_project_uses_default_practice_root(client, tmp_path, monkeypatch):
    default_base = tmp_path / "default_practice"

    def fake_default_practice_root(repo_path: str) -> str:
        return str(default_base / Path(repo_path).name)

    monkeypatch.setattr(project_service, "get_default_practice_root", fake_default_practice_root)

    response = client.post(
        "/api/projects",
        json={
            "repo_path": str(SAMPLE_REPO),
            "target_extensions": [".py"],
        },
    )

    assert response.status_code == 200
    project_id = response.json()["project_id"]
    detail = client.get(f"/api/projects/{project_id}")
    assert detail.status_code == 200
    assert Path(detail.json()["practice_root_path"]) == (default_base / SAMPLE_REPO.name).resolve()
    assert (default_base / SAMPLE_REPO.name).is_dir()


def test_list_projects(client, practice_root):
    project_id = _create_project(client, practice_root)

    response = client.get("/api/projects")

    assert response.status_code == 200
    assert any(project["id"] == project_id for project in response.json()["projects"])


def test_get_project(client, practice_root):
    project_id = _create_project(client, practice_root)

    response = client.get(f"/api/projects/{project_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == project_id
    assert body["file_counts"] == {"total": 0, "target": 0, "passed": 0, "pending": 0}


def test_get_project_not_found(client):
    response = client.get("/api/projects/not-found")

    assert response.status_code == 404
    assert response.json()["error_code"] == "PROJECT_NOT_FOUND"


def test_update_project_target_extensions(client, practice_root):
    project_id = _create_project(client, practice_root)

    response = client.put(
        f"/api/projects/{project_id}",
        json={"target_extensions": [".py", "c", ".PY", "", ".md"]},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == project_id
    assert body["target_extensions"] == [".py", ".c", ".md"]
    assert body["updated_at"]

    detail = client.get(f"/api/projects/{project_id}")
    assert detail.status_code == 200
    assert detail.json()["target_extensions"] == [".py", ".c", ".md"]


def test_setup_project(client, practice_root):
    project_id = _create_project(client, practice_root)

    response = client.post(f"/api/projects/{project_id}/setup")

    assert response.status_code == 200
    assert response.json() == {"copied_files": 2, "skipped_files": 4}
    assert (practice_root / "README.md").exists()
    assert (practice_root / "pyproject.toml").exists()
    assert not list(practice_root.rglob("*.py"))


def test_setup_project_uses_updated_target_extensions(client, practice_root):
    project_id = _create_project(client, practice_root)
    update = client.put(f"/api/projects/{project_id}", json={"target_extensions": [".md", ".toml"]})
    assert update.status_code == 200

    response = client.post(f"/api/projects/{project_id}/setup")

    assert response.status_code == 200
    assert response.json() == {"copied_files": 4, "skipped_files": 2}
    assert not (practice_root / "README.md").exists()
    assert not (practice_root / "pyproject.toml").exists()
    assert (practice_root / "src" / "math_utils.py").exists()
    with database.engine.begin() as connection:
        rows = connection.execute(
            select(database.files.c.source_path, database.files.c.is_target, database.files.c.status)
            .where(database.files.c.project_id == project_id)
            .order_by(database.files.c.source_path)
        ).all()

    by_path = {row.source_path: row for row in rows}
    assert by_path["README.md"].is_target == 1
    assert by_path["README.md"].status == "pending"
    assert by_path["pyproject.toml"].is_target == 1
    assert by_path["pyproject.toml"].status == "pending"
    assert by_path["src/math_utils.py"].is_target == 0
    assert by_path["src/math_utils.py"].status == "skipped"


def test_update_target_extensions(client, practice_root):
    """PUT /api/projects/{id} updates target_extensions."""
    project_id = _create_project(client, practice_root)

    response = client.put(f"/api/projects/{project_id}", json={"target_extensions": [".py", ".md"]})

    assert response.status_code == 200
    assert response.json()["target_extensions"] == [".py", ".md"]


def test_project_analyze_steps_and_cache(client, practice_root, monkeypatch):
    """POST /api/projects/{id}/analyze stores Step 1~3 results and reuses cached data."""
    project_id = _create_project(client, practice_root)
    calls: list[str] = []

    def fake_project_analysis(prompt, system_instruction=None, **kwargs):
        calls.append(prompt)
        return json.dumps(
            {
                "project_summary": "python_basic은 기본 Python 함수를 연습하는 샘플 repository입니다.",
                "domain": "other",
                "framework": "other",
                "key_components": ["utils"],
                "datasets": [],
                "main_contribution": "간단한 함수 구현 연습",
            }
        )

    monkeypatch.setattr(project_analyzer.llm_client, "call_gemini", fake_project_analysis)

    response = client.post(f"/api/projects/{project_id}/analyze")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["project_summary"]["framework"] == "other"
    assert "other" in body["architecture"]["modules"]
    assert "test" in body["architecture"]["modules"]
    assert "implementation_order" in body["dependency_graph"]
    assert len(calls) == 1

    with database.engine.begin() as connection:
        row = connection.execute(select(database.projects).where(database.projects.c.id == project_id)).mappings().first()
    assert row["analysis_status"] == "completed"
    assert json.loads(row["project_summary"])["domain"] == "other"
    assert "src/math_utils.py" in json.loads(row["architecture"])["modules"]["other"]["files"]
    assert json.loads(row["dependency_graph"])["symbols"]

    cached = client.post(f"/api/projects/{project_id}/analyze")

    assert cached.status_code == 200
    assert cached.json()["project_summary"]["project_summary"].startswith("python_basic")
    assert len(calls) == 1


def test_prefilter_excludes_first_level_directories(tmp_path):
    """demo/docs/configs 같은 1단계 디렉토리 파일은 assess 전에 제외된다."""
    repo = tmp_path / "repo"
    (repo / "configs").mkdir(parents=True)
    (repo / "configs" / "dataset.py").write_text("\n".join(f"line {index}" for index in range(25)), encoding="utf-8")
    (repo / "demo").mkdir()
    (repo / "demo" / "run.py").write_text("\n".join(f"line {index}" for index in range(25)), encoding="utf-8")
    (repo / "train.py").write_text("\n".join(f"line {index}" for index in range(25)), encoding="utf-8")

    suitable, skipped = file_prefilter.prefilter_files(
        str(repo),
        ["configs/dataset.py", "demo/run.py", "train.py"],
    )

    assert suitable == ["train.py"]
    reasons = {item["path"]: item["reason"] for item in skipped}
    assert reasons["configs/dataset.py"] == "configs/ 디렉토리 파일"
    assert reasons["demo/run.py"] == "demo/ 디렉토리 파일"


def test_module_classification_first_directory_priority():
    """첫 디렉토리가 configs이면 하위 datasets보다 config 분류가 우선한다."""
    assert project_analyzer._classify_module("configs/_base_/datasets/300w.py") == "config"
    assert project_analyzer._classify_module("mmpose/models/backbone.py") == "model"
    assert project_analyzer._classify_module("train.py") == "training"


def test_dependency_implementation_order_excludes_non_problem_modules(tmp_path):
    """implementation_order에는 config/test/other/evaluation 모듈 파일을 넣지 않는다."""
    repo = tmp_path / "repo"
    (repo / "configs").mkdir(parents=True)
    (repo / "configs" / "settings.py").write_text("def config_value():\n    return 1\n", encoding="utf-8")
    (repo / "demo").mkdir()
    (repo / "demo" / "run.py").write_text("def run_demo():\n    return 1\n", encoding="utf-8")
    (repo / "models").mkdir()
    (repo / "models" / "net.py").write_text("def forward(x):\n    return x\n", encoding="utf-8")

    graph = project_analyzer.build_dependency_graph(
        repo,
        ["configs/settings.py", "demo/run.py", "models/net.py"],
    )

    files_in_order = {entry["file"] for entry in graph["implementation_order"]}
    assert "models/net.py" in files_in_order
    assert "configs/settings.py" not in files_in_order
    assert "demo/run.py" not in files_in_order


def test_setup_with_custom_extensions(client, practice_root):
    """Selecting .py and .md marks both as target files."""
    project_id = _create_project(client, practice_root)
    update = client.put(f"/api/projects/{project_id}", json={"target_extensions": [".py", ".md"]})
    assert update.status_code == 200

    setup = client.post(f"/api/projects/{project_id}/setup")

    assert setup.status_code == 200
    with database.engine.begin() as connection:
        rows = connection.execute(
            select(database.files.c.source_path, database.files.c.is_target, database.files.c.status).where(
                database.files.c.project_id == project_id
            )
        ).all()

    by_path = {row.source_path: row for row in rows}
    assert by_path["README.md"].is_target == 1
    assert by_path["README.md"].status == "pending"
    assert by_path["src/math_utils.py"].is_target == 1
    assert by_path["src/math_utils.py"].status == "pending"
    assert by_path["pyproject.toml"].is_target == 0
    assert by_path["pyproject.toml"].status == "skipped"


def test_assess_project_files(client, practice_root, monkeypatch):
    project_id = _create_project_with_setup(client, practice_root)
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(file_assessor, "ASSESS_DELAY_SECONDS", 0)
    monkeypatch.setattr(
        file_assessor.llm_client,
        "call_gemini",
        lambda prompt, system_instruction=None: json.dumps(
            [
                {
                    "source_path": "src/math_utils.py",
                    "suitable": True,
                    "reason": "명확한 입출력을 가진 함수 발견",
                    "recommended_symbols": [
                        {
                            "symbol": "add_numbers",
                            "difficulty": "easy",
                            "reason": "간단한 산술 로직",
                            "problem_type": "function_blank",
                        }
                    ],
                }
            ]
        ),
    )

    response = client.post(f"/api/projects/{project_id}/assess")

    assert response.status_code == 200
    body = response.json()
    assessments = {assessment["source_path"]: assessment for assessment in body["assessments"]}
    assert assessments["src/math_utils.py"] == {
        "source_path": "src/math_utils.py",
        "suitable": True,
        "reason": "명확한 입출력을 가진 함수 발견",
        "recommended_symbols": [
            {
                "symbol": "add_numbers",
                "difficulty": "easy",
                "reason": "간단한 산술 로직",
                "problem_type": "function_blank",
                "role_in_project": None,
                "depends_on": [],
                "used_by": [],
            }
        ],
    }
    with database.engine.begin() as connection:
        row = connection.execute(
            select(database.files.c.suitable, database.files.c.suitable_reason).where(
                database.files.c.project_id == project_id,
                database.files.c.source_path == "src/math_utils.py",
            )
        ).first()
    assert row.suitable == 1
    assert row.suitable_reason == "명확한 입출력을 가진 함수 발견"


def test_warmup_questions(client, practice_root, monkeypatch):
    project_id = _create_project(client, practice_root)
    monkeypatch.setattr(
        llm_client,
        "call_gemini_with_validation",
        lambda prompt, system_instruction, required_fields, max_retries=1: {
            "warmup_questions": [
                {
                    "question": "PyTorch에서 순전파를 정의하는 메서드는?",
                    "options": ["predict", "forward", "run", "execute"],
                    "answer": 1,
                    "explanation": "PyTorch 모델은 forward 메서드에 순전파를 정의합니다.",
                }
            ]
        },
    )

    response = client.post(f"/api/projects/{project_id}/warmup")

    assert response.status_code == 200
    body = response.json()
    assert body["questions"][0]["id"] == 0
    question = body["questions"][0]
    assert 0 <= question["answer"] < len(question["options"])
    assert question["options"][question["answer"]] == "forward"
    assert len(question["options"]) == 4


def test_assess_status_and_background_start(client, practice_root, monkeypatch):
    project_id = _create_project_with_setup(client, practice_root)

    status = client.get(f"/api/projects/{project_id}/assess/status")
    assert status.status_code == 200
    assert status.json()["status"] == "pending"
    assert status.json()["total"] == 4

    class ImmediateThread:
        def __init__(self, target, args, daemon):
            self.target = target
            self.args = args

        def start(self):
            self.target(*self.args)

    def fake_assess(project_id, payload):
        file_assessor._update_assess_status(project_id, "completed")
        return file_assessor.ProjectAssessResponse(assessments=[])

    monkeypatch.setattr(file_assessor.threading, "Thread", ImmediateThread)
    monkeypatch.setattr(file_assessor, "assess_project_files", fake_assess)

    started = client.post(f"/api/projects/{project_id}/assess/start")

    assert started.status_code == 200
    assert started.json() == {"status": "started"}
    completed = client.get(f"/api/projects/{project_id}/assess/status")
    assert completed.status_code == 200
    assert completed.json()["status"] == "completed"


def test_deep_assess_uses_project_context_and_stores_symbol_metadata(client, practice_root, monkeypatch):
    project_id = _create_project_with_setup(client, practice_root)
    captured_prompts: list[str] = []
    with database.engine.begin() as connection:
        connection.execute(
            database.projects.update()
            .where(database.projects.c.id == project_id)
            .values(
                project_summary=json.dumps(
                    {
                        "project_summary": "샘플 AI 논문 구현 repository",
                        "domain": "other",
                        "framework": "other",
                    },
                    ensure_ascii=False,
                ),
                architecture=json.dumps(
                    {
                        "modules": {
                            "model": {
                                "description": "모델/함수 구현",
                                "files": ["src/math_utils.py"],
                            }
                        }
                    },
                    ensure_ascii=False,
                ),
                dependency_graph=json.dumps(
                    {
                        "dependencies": {
                            "src/math_utils.py::add_numbers": ["src/string_utils.py::capitalize_words"]
                        },
                        "implementation_order": [
                            {"symbol": "add_numbers", "file": "src/math_utils.py", "depth": 1}
                        ],
                    },
                    ensure_ascii=False,
                ),
                analysis_status="completed",
            )
        )

    def fake_deep_assess(prompt, system_instruction=None, **kwargs):
        captured_prompts.append(prompt)
        return json.dumps(
            [
                {
                    "source_path": "src/math_utils.py",
                    "suitable": True,
                    "reason": "프로젝트 맥락상 핵심 연습 함수",
                    "recommended_symbols": [
                        {
                            "symbol": "add_numbers",
                            "difficulty": "easy",
                            "problem_type": "function_blank",
                            "reason": "기본 연산 흐름",
                            "role_in_project": "샘플 모델 계산의 기본 연산 모듈",
                            "depends_on": [],
                            "used_by": ["training_step"],
                        }
                    ],
                }
            ]
        )

    monkeypatch.setattr(file_assessor, "ASSESS_DELAY_SECONDS", 0)
    monkeypatch.setattr(file_assessor.llm_client, "call_gemini", fake_deep_assess)

    response = client.post(f"/api/projects/{project_id}/assess", json={"source_paths": ["src/math_utils.py"], "force": True})

    assert response.status_code == 200
    detailed_prompt = captured_prompts[-1]
    assert "## 프로젝트 개요" in detailed_prompt
    assert "## 이 파일들이 속한 모듈" in detailed_prompt
    assert "## 의존성 정보" in detailed_prompt
    symbol = response.json()["assessments"][0]["recommended_symbols"][0]
    assert symbol["role_in_project"] == "샘플 모델 계산의 기본 연산 모듈"
    assert symbol["used_by"] == ["training_step"]

    with database.engine.begin() as connection:
        row = connection.execute(
            select(database.files.c.recommended_symbols).where(
                database.files.c.project_id == project_id,
                database.files.c.source_path == "src/math_utils.py",
            )
        ).first()
    saved_symbol = json.loads(row.recommended_symbols)[0]
    assert saved_symbol["role_in_project"] == "샘플 모델 계산의 기본 연산 모듈"
    assert saved_symbol["depends_on"] == []
    assert saved_symbol["used_by"] == ["training_step"]


def test_assess_status_returns_incremental_candidates(client, practice_root, monkeypatch):
    project_id = _create_project_with_setup(client, practice_root)
    monkeypatch.setattr(file_assessor, "ASSESS_DELAY_SECONDS", 0)
    monkeypatch.setattr(
        file_assessor.llm_client,
        "call_gemini",
        lambda prompt, system_instruction=None, **kwargs: json.dumps(
            [
                {
                    "source_path": "src/math_utils.py",
                    "suitable": True,
                    "reason": "연습 후보",
                    "recommended_symbols": [
                        {
                            "symbol": "add_numbers",
                            "difficulty": "easy",
                            "reason": "기본 산술",
                            "problem_type": "function_blank",
                        }
                    ],
                }
            ]
        ),
    )

    assess = client.post(f"/api/projects/{project_id}/assess", json={"source_paths": ["src/math_utils.py"]})
    assert assess.status_code == 200

    status = client.get(f"/api/projects/{project_id}/assess/status")

    assert status.status_code == 200
    body = status.json()
    assert body["suitable_count"] >= 1
    assert body["candidates"][0]["source_path"] == "src/math_utils.py"
    assert body["candidates"][0]["symbol"] == "add_numbers"


def test_assess_filters_nonexistent_recommended_symbols(client, practice_root, monkeypatch):
    project_id = _create_project_with_setup(client, practice_root)
    with database.engine.begin() as connection:
        connection.execute(
            database.projects.update()
            .where(database.projects.c.id == project_id)
            .values(
                dependency_graph=json.dumps(
                    {
                        "symbols": {
                            "src/math_utils.py::add_numbers": {"type": "function"},
                        },
                        "dependencies": {},
                        "implementation_order": [],
                    },
                    ensure_ascii=False,
                )
            )
        )

    monkeypatch.setattr(file_assessor, "ASSESS_DELAY_SECONDS", 0)
    monkeypatch.setattr(
        file_assessor.llm_client,
        "call_gemini",
        lambda prompt, system_instruction=None, **kwargs: json.dumps(
            [
                {
                    "source_path": "src/math_utils.py",
                    "suitable": True,
                    "reason": "추측된 함수",
                    "recommended_symbols": [
                        {
                            "symbol": "PatchEmbed.forward",
                            "difficulty": "medium",
                            "reason": "존재하지 않는 심볼",
                            "problem_type": "function_blank",
                        }
                    ],
                }
            ]
        ),
    )

    response = client.post(
        f"/api/projects/{project_id}/assess",
        json={"source_paths": ["src/math_utils.py"], "force": True},
    )

    assert response.status_code == 200
    assessment = response.json()["assessments"][0]
    assert assessment["suitable"] is False
    assert assessment["recommended_symbols"] == []
    assert assessment["reason"] == "실제 소스에서 추천 심볼을 찾지 못했습니다"


def test_problem_tree_columns_exist(client):
    assert "parent_id" in database.problems.c
    assert "weight" in database.problems.c
    assert "depth" in database.problems.c
    assert "unlock_dependencies" in database.problems.c
    assert "role_in_project" in database.problems.c


def test_assess_prefilter_skips_unsuitable_files(client, practice_root):
    config_file = SAMPLE_REPO / "src" / "config_tmp.py"
    config_file.write_text("VALUE = 1\n", encoding="utf-8")
    project_id = _create_project_with_setup(client, practice_root)

    response = client.post(
        f"/api/projects/{project_id}/assess",
        json={"source_paths": ["src/config_tmp.py"]},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["prefilter_skipped"] == 1
    assert body["prefilter_reasons"]
    assert body["assessments"][0]["suitable"] is False
    with database.engine.begin() as connection:
        row = connection.execute(
            select(database.files.c.suitable, database.files.c.suitable_reason).where(
                database.files.c.project_id == project_id,
                database.files.c.source_path == "src/config_tmp.py",
            )
        ).first()
    assert row.suitable == 0
    assert row.suitable_reason in body["prefilter_reasons"]


def test_assess_batches_files(client, practice_root, monkeypatch):
    source_paths = []
    for index in range(6):
        path = SAMPLE_REPO / "src" / f"batch_{index}_tmp.py"
        path.write_text(
            f"def func_{index}(value: int) -> int:\n"
            "    result = value\n"
            "    if value < 0:\n"
            "        result = 0\n"
            "    return result\n"
            + "\n".join(f"# filler {line}" for line in range(20)),
            encoding="utf-8",
        )
        source_paths.append(f"src/batch_{index}_tmp.py")

    project_id = _create_project_with_setup(client, practice_root)
    calls = []

    def fake_batch_assess(prompt, system_instruction=None):
        calls.append(prompt)
        results = []
        for index, source_path in enumerate(source_paths):
            if source_path in prompt:
                results.append(
                    {
                        "source_path": source_path,
                        "suitable": True,
                        "reason": "연습 가능",
                        "recommended_symbols": [
                            {
                                "symbol": f"func_{index}",
                                "difficulty": "easy",
                                "problem_type": "function_blank",
                                "reason": "분기 로직",
                            }
                        ],
                    }
                )
        return json.dumps(results)

    monkeypatch.setattr(file_assessor, "ASSESS_DELAY_SECONDS", 0)
    monkeypatch.setattr(file_assessor.llm_client, "call_gemini", fake_batch_assess)

    response = client.post(f"/api/projects/{project_id}/assess", json={"source_paths": source_paths})

    assert response.status_code == 200
    assert len(calls) == 3
    assert len(response.json()["assessments"]) == 6
    assert all(assessment["suitable"] for assessment in response.json()["assessments"])


def test_assess_quick_scan_skips_files_before_detailed_analysis(client, practice_root, monkeypatch):
    project_id = _create_project_with_setup(client, practice_root)
    calls: list[str] = []

    def fake_quick_then_detailed(prompt, system_instruction=None, **kwargs):
        calls.append(prompt)
        if "파일 목록:" in prompt:
            return json.dumps(
                [
                    {
                        "source_path": "src/math_utils.py",
                        "verdict": "skip",
                        "reason": "빠른 스캔에서 단순 유틸로 제외",
                    }
                ]
            )
        raise AssertionError("Detailed assess should not be called for skipped files")

    monkeypatch.setattr(file_assessor.llm_client, "call_gemini", fake_quick_then_detailed)

    response = client.post(f"/api/projects/{project_id}/assess", json={"source_paths": ["src/math_utils.py"], "force": True})

    assert response.status_code == 200
    assert len(calls) == 1
    assert response.json()["assessments"][0]["suitable"] is False
    assert response.json()["assessments"][0]["reason"] == "빠른 스캔에서 단순 유틸로 제외"


def test_assess_uses_pending_target_files_when_body_empty(client, practice_root, monkeypatch):
    project_id = _create_project_with_setup(client, practice_root)

    def fake_assess(prompt, system_instruction=None):
        return json.dumps(
            [
                {
                    "source_path": "src/math_utils.py",
                    "suitable": True,
                    "reason": "연습 가능",
                    "recommended_symbols": [
                        {
                            "symbol": "add_numbers",
                            "difficulty": "easy",
                            "problem_type": "function_blank",
                            "reason": "명확한 입출력",
                        }
                    ],
                }
            ]
        )

    monkeypatch.setattr(file_assessor.llm_client, "call_gemini", fake_assess)

    response = client.post(f"/api/projects/{project_id}/assess")

    assert response.status_code == 200
    paths = {assessment["source_path"] for assessment in response.json()["assessments"]}
    assert "src/math_utils.py" in paths


def test_assess_reuses_cached_results(client, practice_root, monkeypatch):
    project_id = _create_project_with_setup(client, practice_root)
    calls = 0

    def fake_assess(prompt, system_instruction=None):
        nonlocal calls
        calls += 1
        return json.dumps(
            {
                "source_path": "src/math_utils.py",
                "suitable": True,
                "reason": "캐시 대상",
                "recommended_symbols": [
                    {
                        "symbol": "add_numbers",
                        "difficulty": "easy",
                        "problem_type": "function_blank",
                        "reason": "명확한 입출력",
                    }
                ],
            }
        )

    monkeypatch.setattr(file_assessor.llm_client, "call_gemini", fake_assess)
    first = client.post(f"/api/projects/{project_id}/assess", json={"source_paths": ["src/math_utils.py"]})
    second = client.post(f"/api/projects/{project_id}/assess", json={"source_paths": ["src/math_utils.py"]})

    assert first.status_code == 200
    assert second.status_code == 200
    assert calls == 2
    assert second.json()["assessments"][0]["recommended_symbols"][0]["symbol"] == "add_numbers"


def test_generate_uses_saved_recommended_symbols(client, practice_root, monkeypatch):
    project_id = _create_project_with_setup(client, practice_root)
    monkeypatch.setattr(
        file_assessor.llm_client,
        "call_gemini",
        lambda prompt, system_instruction=None: json.dumps(
            {
                "source_path": "src/math_utils.py",
                "suitable": True,
                "reason": "저장된 추천",
                "recommended_symbols": [
                    {
                        "symbol": "add_numbers",
                        "difficulty": "easy",
                        "problem_type": "function_blank",
                        "reason": "명확한 입출력",
                    }
                ],
            }
        ),
    )
    assess = client.post(f"/api/projects/{project_id}/assess", json={"source_paths": ["src/math_utils.py"]})
    assert assess.status_code == 200

    generate = client.post(
        f"/api/projects/{project_id}/problems/generate",
        json={"source_path": "src/math_utils.py"},
    )

    assert generate.status_code == 200
    assert generate.json()["target_symbol"] == "add_numbers"


def test_prepare_practice_returns_candidates_without_generating_problems(client, practice_root, monkeypatch):
    project_id = _create_project_with_setup(client, practice_root)
    monkeypatch.setattr(
        file_assessor.llm_client,
        "call_gemini",
        lambda prompt, system_instruction=None, **kwargs: json.dumps(
            [
                {
                    "source_path": "src/math_utils.py",
                    "suitable": True,
                    "reason": "연습 후보",
                    "recommended_symbols": [
                        {
                            "symbol": "add_numbers",
                            "difficulty": "easy",
                            "problem_type": "function_blank",
                            "reason": "기본 산술",
                            "role_in_project": "샘플 계산의 기본 함수",
                            "depends_on": [],
                            "used_by": ["average"],
                        }
                    ],
                }
            ]
        ),
    )
    assess = client.post(f"/api/projects/{project_id}/assess", json={"source_paths": ["src/math_utils.py"]})
    assert assess.status_code == 200

    prepare = client.post(f"/api/projects/{project_id}/prepare")

    assert prepare.status_code == 200
    body = prepare.json()
    assert body["candidates"] == [
        {
            "source_path": "src/math_utils.py",
            "symbol": "add_numbers",
            "difficulty": "easy",
            "problem_type": "function_blank",
            "role_in_project": "샘플 계산의 기본 함수",
            "depends_on": [],
            "used_by": ["average"],
            "status": "unlocked",
            "depth": 0,
            "generated": False,
            "problem_id": None,
            "parent_id": None,
        }
    ]
    problems_response = client.get(f"/api/projects/{project_id}/problems")
    assert problems_response.status_code == 200
    assert problems_response.json()["problems"] == []


def test_generate_class_method_symbol(client, practice_root):
    source_file = SAMPLE_REPO / "src" / "class_tmp.py"
    source_file.write_text(
        "class MultiHeadAttention:\n"
        "    def forward(self, query: int, key: int) -> int:\n"
        "        score = query + key\n"
        "        if score < 0:\n"
        "            return 0\n"
        "        return score\n",
        encoding="utf-8",
    )
    project_id = _create_project_with_setup(client, practice_root)

    response = client.post(
        f"/api/projects/{project_id}/problems/generate",
        json={
            "source_path": "src/class_tmp.py",
            "target_symbols": [{"symbol": "MultiHeadAttention.forward", "difficulty": "medium"}],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["target_symbol"] == "MultiHeadAttention.forward"
    detail = client.get(f"/api/problems/{body['problem_id']}").json()
    assert "def forward" in detail["starter_code"]
    assert "raise NotImplementedError" in detail["starter_code"]
    assert "score = query + key" in detail["original_code"]


def test_skipped_dependency_does_not_lock_candidate(client, practice_root):
    project_id = _create_project_with_setup(client, practice_root)
    with database.engine.begin() as connection:
        file_row = connection.execute(
            select(database.files.c.id).where(
                database.files.c.project_id == project_id,
                database.files.c.source_path == "src/math_utils.py",
            )
        ).mappings().first()
        connection.execute(
            database.files.update()
            .where(database.files.c.id == file_row["id"])
            .values(
                suitable=1,
                recommended_symbols=json.dumps(
                    [
                        {"symbol": "Missing.concat", "difficulty": "easy", "status": "skipped"},
                        {
                            "symbol": "MultiHeadAttention.forward",
                            "difficulty": "hard",
                            "depends_on": ["Missing.concat"],
                        },
                    ],
                    ensure_ascii=False,
                ),
            )
        )
        connection.execute(
            database.projects.update()
            .where(database.projects.c.id == project_id)
            .values(
                dependency_graph=json.dumps(
                    {
                        "implementation_order": [
                            {"symbol": "MultiHeadAttention.forward", "file": "src/math_utils.py", "depth": 1}
                        ]
                    },
                    ensure_ascii=False,
                )
            )
        )

    response = client.post(f"/api/projects/{project_id}/prepare")

    assert response.status_code == 200
    by_symbol = {candidate["symbol"]: candidate for candidate in response.json()["candidates"]}
    assert by_symbol["Missing.concat"]["status"] == "skipped"
    assert by_symbol["MultiHeadAttention.forward"]["status"] == "unlocked"


def test_problem_tree_generation_and_unlock_flow(client, practice_root):
    project_id = _create_project_with_setup(client, practice_root)
    with database.engine.begin() as connection:
        connection.execute(
            database.projects.update()
            .where(database.projects.c.id == project_id)
            .values(
                project_summary=json.dumps(
                    {"project_summary": "python_basic 샘플 프로젝트", "domain": "other", "framework": "other"},
                    ensure_ascii=False,
                ),
                architecture=json.dumps(
                    {
                        "modules": {
                            "model": {
                                "description": "샘플 함수 구현 모듈",
                                "files": ["src/math_utils.py", "src/string_utils.py"],
                            },
                            "config": {"description": "설정", "files": ["pyproject.toml"]},
                        },
                        "non_problem_files": ["pyproject.toml"],
                    },
                    ensure_ascii=False,
                ),
                dependency_graph=json.dumps(
                    {
                        "implementation_order": [
                            {"symbol": "add_numbers", "file": "src/math_utils.py", "depth": 0},
                            {"symbol": "reverse_string", "file": "src/string_utils.py", "depth": 1},
                        ],
                        "dependencies": {
                            "src/string_utils.py::reverse_string": ["src/math_utils.py::add_numbers"]
                        },
                    },
                    ensure_ascii=False,
                ),
                analysis_status="completed",
            )
        )

    math_generate = client.post(
        f"/api/projects/{project_id}/problems/generate",
        json={
            "source_path": "src/math_utils.py",
            "target_symbols": [
                {
                    "symbol": "add_numbers",
                    "difficulty": "easy",
                    "problem_type": "function_blank",
                    "role_in_project": "기본 산술 연산",
                    "depends_on": [],
                    "used_by": ["reverse_string"],
                }
            ],
        },
    )
    assert math_generate.status_code == 200
    math_problem_id = math_generate.json()["problem_id"]

    string_generate = client.post(
        f"/api/projects/{project_id}/problems/generate",
        json={
            "source_path": "src/string_utils.py",
            "target_symbols": [
                {
                    "symbol": "reverse_string",
                    "difficulty": "easy",
                    "problem_type": "function_blank",
                    "role_in_project": "문자열 전처리의 기본 변환",
                    "depends_on": ["add_numbers"],
                    "used_by": [],
                }
            ],
        },
    )
    assert string_generate.status_code == 200
    string_problem_id = string_generate.json()["problem_id"]

    listing = client.get(f"/api/projects/{project_id}/problems")
    assert listing.status_code == 200
    body = listing.json()
    assert body["modules"]
    assert "overall_progress" in body
    by_symbol = {problem["target_symbol"]: problem for problem in body["problems"]}
    assert by_symbol["add_numbers"]["status"] == "unlocked"
    assert by_symbol["add_numbers"]["depth"] == 0
    assert by_symbol["add_numbers"]["parent_id"] == body["modules"][0]["id"]
    assert by_symbol["reverse_string"]["status"] == "locked"
    assert by_symbol["reverse_string"]["depth"] == 1
    assert by_symbol["reverse_string"]["unlock_dependencies"] == [math_problem_id]
    assert by_symbol["reverse_string"]["role_in_project"] == "문자열 전처리의 기본 변환"

    locked_submit = client.post(
        f"/api/problems/{string_problem_id}/submit",
        json={"code": (SAMPLE_REPO / "src" / "string_utils.py").read_text(encoding="utf-8"), "overwrite": False},
    )
    assert locked_submit.status_code == 400
    assert locked_submit.json()["error_code"] == "PROBLEM_LOCKED"

    passed_submit = client.post(
        f"/api/problems/{math_problem_id}/submit",
        json={"code": _math_solution(), "overwrite": False},
    )
    assert passed_submit.status_code == 200
    assert passed_submit.json()["passed"] is True

    unlocked_listing = client.get(f"/api/projects/{project_id}/problems")
    unlocked_by_symbol = {problem["target_symbol"]: problem for problem in unlocked_listing.json()["problems"]}
    assert unlocked_by_symbol["reverse_string"]["status"] == "unlocked"
    assert unlocked_listing.json()["overall_progress"] > 0


def test_assess_project_files_without_api_key(client, practice_root, monkeypatch):
    project_id = _create_project_with_setup(client, practice_root)
    monkeypatch.setattr(file_assessor.llm_client, "get_gemini_api_key", lambda: "")

    response = client.post(
        f"/api/projects/{project_id}/assess",
        json={"source_paths": ["src/math_utils.py"]},
    )

    assert response.status_code == 200
    assessment = response.json()["assessments"][0]
    assert assessment["suitable"] is None
    assert "분석 오류" in assessment["reason"]


def test_gemini_api_key_is_read_at_call_time(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "call-time-key")

    assert llm_client.get_gemini_api_key() == "call-time-key"


def test_assess_project_files_too_large(client, practice_root, monkeypatch):
    project_id = _create_project_with_setup(client, practice_root)
    large_file = SAMPLE_REPO / "src" / "large_tmp.py"
    large_file.write_text("x = 1\n" * 9000, encoding="utf-8")
    try:
        setup = client.post(f"/api/projects/{project_id}/setup")
        assert setup.status_code == 200

        response = client.post(
            f"/api/projects/{project_id}/assess",
            json={"source_paths": ["src/large_tmp.py"]},
        )

        assert response.status_code == 200
        assert response.json()["assessments"][0] == {
            "source_path": "src/large_tmp.py",
            "suitable": False,
            "reason": "파일이 너무 큼",
            "recommended_symbols": [],
        }
    finally:
        large_file.unlink(missing_ok=True)


@requires_gemini
def test_assess_suitable_file(client, practice_root):
    """src/math_utils.py can be assessed by Gemini."""
    project_id = _create_project_with_setup(client, practice_root)

    response = client.post(
        f"/api/projects/{project_id}/assess",
        json={"source_paths": ["src/math_utils.py"]},
    )

    assert response.status_code == 200
    assessment = response.json()["assessments"][0]
    assert assessment["source_path"] == "src/math_utils.py"
    assert isinstance(assessment["suitable"], bool)
    assert assessment["reason"]
    assert isinstance(assessment["recommended_symbols"], list)


@requires_gemini
def test_assess_unsuitable_file(client, practice_root):
    """A config-like file can be assessed as unsuitable by Gemini."""
    config_file = SAMPLE_REPO / "src" / "config_tmp.py"
    config_file.write_text("API_URL = 'https://example.com'\nTIMEOUT_SECONDS = 5\n", encoding="utf-8")
    project_id = _create_project_with_setup(client, practice_root)

    response = client.post(
        f"/api/projects/{project_id}/assess",
        json={"source_paths": ["src/config_tmp.py"]},
    )

    assert response.status_code == 200
    assessment = response.json()["assessments"][0]
    assert assessment["source_path"] == "src/config_tmp.py"
    assert isinstance(assessment["suitable"], bool)
    assert assessment["reason"]


def test_assess_large_file(client, practice_root):
    """Files over 50KB are marked unsuitable without calling Gemini."""
    large_file = SAMPLE_REPO / "src" / "large_tmp.py"
    large_file.write_text("x = 1\n" * 9000, encoding="utf-8")
    project_id = _create_project_with_setup(client, practice_root)

    response = client.post(
        f"/api/projects/{project_id}/assess",
        json={"source_paths": ["src/large_tmp.py"]},
    )

    assert response.status_code == 200
    assessment = response.json()["assessments"][0]
    assert assessment["suitable"] is False
    assert "파일" in assessment["reason"] or "file" in assessment["reason"].lower()


def test_assess_no_api_key(client, practice_root, monkeypatch):
    """Missing Gemini key defers the file instead of crashing the assess flow."""
    project_id = _create_project_with_setup(client, practice_root)
    monkeypatch.setattr(file_assessor.llm_client, "get_gemini_api_key", lambda: "")

    response = client.post(
        f"/api/projects/{project_id}/assess",
        json={"source_paths": ["src/math_utils.py"]},
    )

    assert response.status_code == 200
    assessment = response.json()["assessments"][0]
    assert assessment["suitable"] is None
    assert "분석 오류" in assessment["reason"]


def test_generate_problem_math(client, practice_root):
    project_id = _create_project_with_setup(client, practice_root)

    response = _generate_problem(client, project_id, "src/math_utils.py")

    assert response.status_code == 200
    body = response.json()
    assert body["target_symbol"]
    assert body["test_path"] == "tests/test_math_utils.py"


def test_generate_problem_string(client, practice_root):
    project_id = _create_project_with_setup(client, practice_root)

    response = _generate_problem(client, project_id, "src/string_utils.py")

    assert response.status_code == 200
    body = response.json()
    assert body["target_symbol"]
    assert body["test_path"] == "tests/test_string_utils.py"


def test_generate_problem_for_non_code_file_returns_not_found(client, practice_root):
    project_id = _create_project_with_setup(client, practice_root)

    response = _generate_problem(client, project_id, "README.md")

    assert response.status_code == 404
    assert response.json()["error_code"] == "PROBLEM_NOT_FOUND"


def test_generate_problem_without_test_uses_llm(client, practice_root):
    source_file = SAMPLE_REPO / "src" / "llm_only_tmp.py"
    source_file.write_text(
        "def normalize_score(value: int) -> int:\n"
        "    if value < 0:\n"
        "        return 0\n"
        "    if value > 100:\n"
        "        return 100\n"
        "    return value\n",
        encoding="utf-8",
    )
    try:
        project_id = _create_project_with_setup(client, practice_root)

        response = client.post(
            f"/api/projects/{project_id}/problems/generate",
            json={
                "source_path": "src/llm_only_tmp.py",
                "target_symbols": [{"symbol": "normalize_score", "difficulty": "easy"}],
            },
        )

        assert response.status_code == 200
        body = response.json()
        assert body["problem_id"]
        assert body["grading_method"] == "llm"
        assert body["problem_type"] == "function_blank"
        assert body["test_path"] is None
        assert body["difficulty"] == "easy"
        assert body["problems"] == [
            {
                "problem_id": body["problem_id"],
                "file_id": body["file_id"],
                "source_path": "src/llm_only_tmp.py",
                "target_symbol": "normalize_score",
                "problem_type": "function_blank",
                "test_path": None,
                "grading_method": "llm",
                "difficulty": "easy",
            }
        ]
        detail = client.get(f"/api/problems/{body['problem_id']}")
        assert detail.status_code == 200
        assert detail.json()["original_code"].startswith("def normalize_score")
    finally:
        source_file.unlink(missing_ok=True)


def test_generate_llm_problem(client, practice_root):
    """A source file without tests generates an LLM-graded problem."""
    problem_id = _create_llm_problem(client, practice_root)

    detail = client.get(f"/api/problems/{problem_id}")

    assert detail.status_code == 200
    assert detail.json()["grading_method"] == "llm"
    assert detail.json()["test_path"] is None


def test_generate_pytest_problem(client, practice_root):
    """A source file with tests generates a pytest-graded problem."""
    problem_id = _create_math_problem(client, practice_root)

    detail = client.get(f"/api/problems/{problem_id}")

    assert detail.status_code == 200
    assert detail.json()["grading_method"] == "pytest"
    assert detail.json()["test_path"] == "tests/test_math_utils.py"


def test_generate_python_problem_uses_single_llm_assets_call(client, practice_root, monkeypatch):
    """Python generate can create prompt and starter_code from one validated LLM response."""
    calls: list[str] = []

    def fake_assets(prompt):
        calls.append(prompt)
        return {
            "prompt": "### add_numbers 함수 구현\n\n#### 입력\n- a, b\n\n#### 출력\n- 합계",
            "starter_code": (
                "def add_numbers(a: int, b: int) -> int:\n"
                "    # TODO: implement this function\n"
                "    raise NotImplementedError\n"
            ),
        }

    monkeypatch.setattr(problem_generator, "_call_problem_assets_llm", fake_assets)
    project_id = _create_project_with_setup(client, practice_root)

    response = client.post(
        f"/api/projects/{project_id}/problems/generate",
        json={
            "source_path": "src/math_utils.py",
            "target_symbols": [{"symbol": "add_numbers", "difficulty": "easy"}],
        },
    )

    assert response.status_code == 200
    detail = client.get(f"/api/problems/{response.json()['problem_id']}")
    assert detail.json()["prompt"].startswith("### add_numbers")
    assert "# TODO: implement this function" in detail.json()["starter_code"]
    assert len(calls) == 1


def test_generate_non_python_problem_uses_llm(client, practice_root, monkeypatch):
    """Non-Python files bypass Python AST and use Gemini-generated problem data."""
    source_file = SAMPLE_REPO / "src" / "non_python_tmp.js"
    source_file.write_text(
        "export function addNumbers(a, b) {\n"
        "  const result = a + b;\n"
        "  return result;\n"
        "}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        problem_generator.llm_client,
        "call_gemini",
        lambda prompt, system_instruction=None, response_mime_type="application/json": json.dumps(
            {
                "original_code": "export function addNumbers(a, b) {\n  const result = a + b;\n  return result;\n}",
                "starter_code": (
                    "export function addNumbers(a, b) {\n"
                    "  // TODO: implement this function\n"
                    "}\n"
                ),
                "prompt": "### addNumbers 함수 구현\n\n#### 입력\n- a, b\n\n#### 출력\n- 합계",
            }
        ),
    )
    create = client.post(
        "/api/projects",
        json={
            "repo_path": str(SAMPLE_REPO),
            "practice_root_path": str(practice_root),
            "target_extensions": [".js"],
        },
    )
    assert create.status_code == 200
    project_id = create.json()["project_id"]
    setup = client.post(f"/api/projects/{project_id}/setup")
    assert setup.status_code == 200

    response = client.post(
        f"/api/projects/{project_id}/problems/generate",
        json={
            "source_path": "src/non_python_tmp.js",
            "target_symbols": [{"symbol": "addNumbers", "difficulty": "easy"}],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["grading_method"] == "llm"
    assert body["test_path"] is None
    detail = client.get(f"/api/problems/{body['problem_id']}").json()
    assert "export function addNumbers" in detail["original_code"]
    assert "// TODO: implement this function" in detail["starter_code"]
    assert detail["prompt"].startswith("### addNumbers")


def test_submit_non_python_llm_problem_skips_python_syntax_check(client, practice_root, monkeypatch):
    """JavaScript submissions should not be rejected by Python compile()."""
    source_file = SAMPLE_REPO / "src" / "non_python_tmp.js"
    source_file.write_text(
        "export function addNumbers(a, b) {\n"
        "  const result = a + b;\n"
        "  return result;\n"
        "}\n",
        encoding="utf-8",
    )
    responses = iter(
        [
            json.dumps(
                {
                    "original_code": "export function addNumbers(a, b) {\n  const result = a + b;\n  return result;\n}",
                    "starter_code": "export function addNumbers(a, b) {\n  // TODO: implement this function\n}\n",
                    "prompt": "### addNumbers 함수 구현",
                }
            ),
            json.dumps({"passed": True, "feedback": "동일하게 동작합니다.", "score": 100}),
        ]
    )
    monkeypatch.setattr(
        problem_generator.llm_client,
        "call_gemini",
        lambda prompt, system_instruction=None, response_mime_type="application/json": next(responses),
    )
    create = client.post(
        "/api/projects",
        json={
            "repo_path": str(SAMPLE_REPO),
            "practice_root_path": str(practice_root),
            "target_extensions": [".js"],
        },
    )
    project_id = create.json()["project_id"]
    assert client.post(f"/api/projects/{project_id}/setup").status_code == 200
    generate = client.post(
        f"/api/projects/{project_id}/problems/generate",
        json={
            "source_path": "src/non_python_tmp.js",
            "target_symbols": [{"symbol": "addNumbers"}],
        },
    )
    assert generate.status_code == 200

    submit = client.post(
        f"/api/problems/{generate.json()['problem_id']}/submit",
        json={"code": "export function addNumbers(a, b) {\n  return a + b;\n}\n", "overwrite": False},
    )

    assert submit.status_code == 200
    body = submit.json()
    assert body["passed"] is True
    assert body["feedback"] == "동일하게 동작합니다."
    assert body["test_cases"] is None
    assert "invalid syntax" not in body["feedback"].lower()


def test_generate_function_partial_problem(client, practice_root, monkeypatch):
    source_file = SAMPLE_REPO / "src" / "partial_tmp.py"
    source_file.write_text(
        "def classify_score(value: int) -> str:\n"
        "    label = 'low'\n"
        "    if value >= 90:\n"
        "        label = 'high'\n"
        "    elif value >= 60:\n"
        "        label = 'medium'\n"
        "    return label\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        problem_generator,
        "_call_partial_starter_llm",
        lambda prompt: (
            "def classify_score(value: int) -> str:\n"
            "    label = 'low'\n"
            "    # TODO: 점수 범위에 맞는 label을 선택하세요\n"
            "    pass\n"
            "    return label\n"
        ),
    )

    project_id = _create_project_with_setup(client, practice_root)
    response = client.post(
        f"/api/projects/{project_id}/problems/generate",
        json={
            "source_path": "src/partial_tmp.py",
            "target_symbols": [
                {"symbol": "classify_score", "difficulty": "medium", "problem_type": "function_partial"}
            ],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["problem_type"] == "function_partial"
    detail = client.get(f"/api/problems/{body['problem_id']}").json()
    assert detail["problem_type"] == "function_partial"
    assert "def classify_score" in detail["starter_code"]
    assert "TODO" in detail["starter_code"]
    assert "pass" in detail["starter_code"]


def test_generate_function_partial_fallbacks_to_blank(client, practice_root, monkeypatch):
    source_file = SAMPLE_REPO / "src" / "partial_tmp.py"
    source_file.write_text(
        "def double_positive(value: int) -> int:\n"
        "    result = value\n"
        "    if value > 0:\n"
        "        result = value * 2\n"
        "    return result\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(problem_generator, "_call_partial_starter_llm", lambda prompt: "not useful")

    project_id = _create_project_with_setup(client, practice_root)
    response = client.post(
        f"/api/projects/{project_id}/problems/generate",
        json={
            "source_path": "src/partial_tmp.py",
            "target_symbols": [
                {"symbol": "double_positive", "difficulty": "easy", "problem_type": "function_partial"}
            ],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["problem_type"] == "function_blank"
    detail = client.get(f"/api/problems/{body['problem_id']}").json()
    assert detail["problem_type"] == "function_blank"
    assert "raise NotImplementedError" in detail["starter_code"]


@requires_gemini
def test_generate_partial_problem(client, practice_root, monkeypatch):
    """function_partial generation keeps a code skeleton with TODO."""
    source_file = SAMPLE_REPO / "src" / "partial_tmp.py"
    source_file.write_text(
        "def bounded(value: int) -> int:\n"
        "    lower = 0\n"
        "    upper = 10\n"
        "    if value < lower:\n"
        "        return lower\n"
        "    if value > upper:\n"
        "        return upper\n"
        "    return value\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        problem_generator,
        "_call_partial_starter_llm",
        lambda prompt: (
            "def bounded(value: int) -> int:\n"
            "    lower = 0\n"
            "    upper = 10\n"
            "    # TODO: lower와 upper 사이로 값을 제한하세요\n"
            "    pass\n"
            "    return value\n"
        ),
    )
    project_id = _create_project_with_setup(client, practice_root)

    response = client.post(
        f"/api/projects/{project_id}/problems/generate",
        json={
            "source_path": "src/partial_tmp.py",
            "target_symbols": [{"symbol": "bounded", "problem_type": "function_partial"}],
        },
    )

    body = client.get(f"/api/problems/{response.json()['problem_id']}").json()
    assert body["problem_type"] == "function_partial"
    assert "lower = 0" in body["starter_code"]
    assert "TODO" in body["starter_code"]


@requires_gemini
def test_partial_starter_contains_function_name(client, practice_root, monkeypatch):
    """Partial starter code contains the target function name."""
    source_file = SAMPLE_REPO / "src" / "partial_tmp.py"
    source_file.write_text(
        "def choose_max(left: int, right: int) -> int:\n"
        "    result = left\n"
        "    if right > left:\n"
        "        result = right\n"
        "    return result\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        problem_generator,
        "_call_partial_starter_llm",
        lambda prompt: "def choose_max(left: int, right: int) -> int:\n    # TODO: 더 큰 값을 선택하세요\n    pass\n",
    )
    project_id = _create_project_with_setup(client, practice_root)

    response = client.post(
        f"/api/projects/{project_id}/problems/generate",
        json={
            "source_path": "src/partial_tmp.py",
            "target_symbols": [{"symbol": "choose_max", "problem_type": "function_partial"}],
        },
    )

    detail = client.get(f"/api/problems/{response.json()['problem_id']}").json()
    assert "def choose_max" in detail["starter_code"]


@requires_gemini
def test_partial_fallback_to_blank(client, practice_root, monkeypatch):
    """Invalid partial starter falls back to function_blank."""
    source_file = SAMPLE_REPO / "src" / "partial_tmp.py"
    source_file.write_text(
        "def negate(value: int) -> int:\n"
        "    result = -value\n"
        "    return result\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(problem_generator, "_call_partial_starter_llm", lambda prompt: "")
    project_id = _create_project_with_setup(client, practice_root)

    response = client.post(
        f"/api/projects/{project_id}/problems/generate",
        json={
            "source_path": "src/partial_tmp.py",
            "target_symbols": [{"symbol": "negate", "problem_type": "function_partial"}],
        },
    )

    detail = client.get(f"/api/problems/{response.json()['problem_id']}").json()
    assert detail["problem_type"] == "function_blank"
    assert "raise NotImplementedError" in detail["starter_code"]


def test_generate_multiple_target_symbols(client, practice_root):
    source_file = SAMPLE_REPO / "src" / "multi_tmp.py"
    source_file.write_text(
        "def clamp_low(value: int) -> int:\n"
        "    if value < 0:\n"
        "        return 0\n"
        "    return value\n\n"
        "def clamp_high(value: int) -> int:\n"
        "    if value > 10:\n"
        "        return 10\n"
        "    return value\n",
        encoding="utf-8",
    )
    try:
        project_id = _create_project_with_setup(client, practice_root)

        response = client.post(
            f"/api/projects/{project_id}/problems/generate",
            json={
                "source_path": "src/multi_tmp.py",
                "target_symbols": [
                    {"symbol": "clamp_low", "difficulty": "easy"},
                    {"symbol": "clamp_high", "difficulty": "medium"},
                ],
            },
        )

        assert response.status_code == 200
        problems = response.json()["problems"]
        assert [problem["target_symbol"] for problem in problems] == ["clamp_low", "clamp_high"]
        assert [problem["grading_method"] for problem in problems] == ["llm", "llm"]
        assert [problem["difficulty"] for problem in problems] == ["easy", "medium"]

        listed = client.get(f"/api/projects/{project_id}/problems")
        assert listed.status_code == 200
        listed_symbols = {problem["target_symbol"]: problem for problem in listed.json()["problems"]}
        assert listed_symbols["clamp_low"]["grading_method"] == "llm"
        assert listed_symbols["clamp_high"]["difficulty"] == "medium"
    finally:
        source_file.unlink(missing_ok=True)


def test_generate_multiple_problems(client, practice_root):
    """Multiple target_symbols create multiple problems."""
    source_file = SAMPLE_REPO / "src" / "multi_tmp.py"
    source_file.write_text(
        "def left(value: int) -> int:\n"
        "    if value < 0:\n"
        "        return 0\n"
        "    return value\n\n"
        "def right(value: int) -> int:\n"
        "    if value > 10:\n"
        "        return 10\n"
        "    return value\n",
        encoding="utf-8",
    )
    project_id = _create_project_with_setup(client, practice_root)

    response = client.post(
        f"/api/projects/{project_id}/problems/generate",
        json={
            "source_path": "src/multi_tmp.py",
            "target_symbols": [
                {"symbol": "left", "difficulty": "easy"},
                {"symbol": "right", "difficulty": "medium"},
            ],
        },
    )

    assert response.status_code == 200
    assert len(response.json()["problems"]) == 2


def test_generate_with_original_code(client, practice_root):
    """LLM problems persist original_code for grading."""
    problem_id = _create_llm_problem(client, practice_root)

    detail = client.get(f"/api/problems/{problem_id}")

    assert detail.status_code == 200
    assert "def normalize_score" in detail.json()["original_code"]


def test_generate_problem_duplicate(client, practice_root):
    project_id = _create_project_with_setup(client, practice_root)

    first = _generate_problem(client, project_id, "src/math_utils.py")
    second = _generate_problem(client, project_id, "src/math_utils.py")

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["problem_id"] == second.json()["problem_id"]
    with database.engine.begin() as connection:
        count = connection.execute(select(func.count()).select_from(database.problems)).scalar_one()
    assert count == 1


def test_get_problems(client, practice_root):
    project_id = _create_project_with_setup(client, practice_root)
    created = _generate_problem(client, project_id, "src/math_utils.py").json()

    response = client.get(f"/api/projects/{project_id}/problems")

    assert response.status_code == 200
    assert any(problem["id"] == created["problem_id"] for problem in response.json()["problems"])


def test_get_problem_detail(client, practice_root):
    project_id = _create_project_with_setup(client, practice_root)
    problem_id = _generate_problem(client, project_id, "src/math_utils.py").json()["problem_id"]

    response = client.get(f"/api/problems/{problem_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["prompt"].startswith("### add_numbers 함수 구현")
    assert "#### 입력" in body["prompt"]
    assert "#### 출력" in body["prompt"]
    assert "#### 동작" in body["prompt"]
    context = json.loads(body["context"])
    assert set(context) == {"imports", "class_init", "callers", "readme_snippet"}
    assert "raise NotImplementedError" in body["starter_code"]
    assert body["test_path"] == "tests/test_math_utils.py"


def test_generate_problem_prompt_falls_back_when_llm_fails(client, practice_root, monkeypatch):
    def fail_problem_prompt(prompt: str) -> str:
        raise AppError("LLM_API_KEY_MISSING", "missing key")

    monkeypatch.setattr(problem_generator, "_call_problem_description_llm", fail_problem_prompt)
    project_id = _create_project_with_setup(client, practice_root)
    problem_id = _generate_problem(client, project_id, "src/math_utils.py").json()["problem_id"]

    response = client.get(f"/api/problems/{problem_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["prompt"].splitlines()[0] == "아래 함수를 구현하세요."
    assert body["context"] is not None


@requires_gemini
def test_rich_prompt_contains_markdown(client, practice_root):
    """LLM-generated prompt contains markdown headers."""
    problem_id = _create_math_problem(client, practice_root)

    body = client.get(f"/api/problems/{problem_id}").json()

    assert "###" in body["prompt"]


@requires_gemini
def test_rich_prompt_contains_sections(client, practice_root):
    """LLM-generated prompt contains input/output/behavior sections."""
    problem_id = _create_math_problem(client, practice_root)

    prompt = client.get(f"/api/problems/{problem_id}").json()["prompt"]

    assert "입력" in prompt
    assert "출력" in prompt
    assert "동작" in prompt


def test_prompt_fallback_without_api_key(client, practice_root, monkeypatch):
    """Problem generation falls back to the legacy prompt when Gemini is unavailable."""
    monkeypatch.setattr(
        problem_generator,
        "_call_problem_description_llm",
        lambda prompt: (_ for _ in ()).throw(AppError("LLM_API_KEY_MISSING", "missing key")),
    )
    problem_id = _create_math_problem(client, practice_root)

    prompt = client.get(f"/api/problems/{problem_id}").json()["prompt"]

    assert prompt.startswith("아래 함수를 구현하세요.")


@requires_gemini
def test_context_stored_in_problem(client, practice_root):
    """Problem detail includes serialized context."""
    problem_id = _create_math_problem(client, practice_root)

    body = client.get(f"/api/problems/{problem_id}").json()

    assert body["context"]
    assert "readme_snippet" in json.loads(body["context"])


@requires_gemini
def test_context_contains_imports(client, practice_root):
    """Collected context stores top-level imports."""
    source_file = SAMPLE_REPO / "src" / "imported_tmp.py"
    source_file.write_text(
        "import math\n"
        "from pathlib import Path\n\n"
        "def circle_area(radius: int) -> float:\n"
        "    result = math.pi * radius * radius\n"
        "    return result\n",
        encoding="utf-8",
    )
    project_id = _create_project_with_setup(client, practice_root)
    response = client.post(
        f"/api/projects/{project_id}/problems/generate",
        json={"source_path": "src/imported_tmp.py", "target_symbols": [{"symbol": "circle_area"}]},
    )

    assert response.status_code == 200
    body = client.get(f"/api/problems/{response.json()['problem_id']}").json()
    context = json.loads(body["context"])
    assert "import math" in context["imports"]
    assert "from pathlib import Path" in context["imports"]


def test_context_collector_callers():
    """context_collector collects files referencing the target function."""
    caller_file = SAMPLE_REPO / "src" / "caller_tmp.py"
    caller_file.write_text(
        "from src.math_utils import add_numbers\n\n"
        "def use_add() -> int:\n"
        "    return add_numbers(1, 2)\n",
        encoding="utf-8",
    )

    context = context_collector.collect_context(str(SAMPLE_REPO), "src/math_utils.py", "add_numbers")

    assert "src/caller_tmp.py" in context["callers"]


def test_submit_correct(client, practice_root):
    problem_id = _create_math_problem(client, practice_root)

    response = client.post(
        f"/api/problems/{problem_id}/submit",
        json={"code": _math_solution(), "overwrite": False},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["passed"] is True
    assert body["saved_path"] == "src/math_utils.py"
    assert (practice_root / "src" / "math_utils.py").exists()


def test_submit_wrong(client, practice_root):
    problem_id = _create_math_problem(client, practice_root)

    response = client.post(
        f"/api/problems/{problem_id}/submit",
        json={"code": "def add_numbers(a, b):\n    return None\n", "overwrite": False},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["passed"] is False
    assert body["saved_path"] is None
    assert "Error" in f"{body['stdout']}\n{body['stderr']}" or "failed" in f"{body['stdout']}\n{body['stderr']}"


def test_submit_syntax_error(client, practice_root):
    problem_id = _create_math_problem(client, practice_root)

    response = client.post(
        f"/api/problems/{problem_id}/submit",
        json={"code": "def add_numbers(:\n    return 1\n", "overwrite": False},
    )

    assert response.status_code == 200
    assert response.json()["passed"] is False


def _mock_llm_test_case_grading(monkeypatch, test_cases: list[dict], feedback: str = "피드백입니다."):
    responses = iter([json.dumps({"test_cases": test_cases}), feedback])
    monkeypatch.setattr(llm_grader.llm_client, "call_gemini", lambda *args, **kwargs: next(responses))


def test_submit_llm_correct(client, practice_root, monkeypatch):
    problem_id = _create_llm_problem(client, practice_root)
    monkeypatch.setattr(
        llm_grader.llm_client,
        "call_gemini",
        lambda prompt, system_instruction=None: json.dumps(
            {
                "passed": True,
                "feedback": "기능이 원본과 동일하게 구현되었습니다.",
                "score": 95,
                "details": {
                    "functionality": "동일",
                    "edge_cases": "적절",
                    "code_quality": "좋음",
                },
            }
        ),
    )

    response = client.post(
        f"/api/problems/{problem_id}/submit",
        json={
            "code": (
                "def normalize_score(value: int) -> int:\n"
                "    if value < 0:\n"
                "        return 0\n"
                "    if value > 100:\n"
                "        return 100\n"
                "    return value\n"
            ),
            "overwrite": False,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["passed"] is True
    assert body["feedback"] == "기능이 원본과 동일하게 구현되었습니다."
    assert body["stdout"] is None
    assert body["stderr"] is None
    assert body["grading_method"] == "llm"
    assert body["saved_path"] == "src/llm_submit_tmp.py"


def test_submit_llm_wrong(client, practice_root, monkeypatch):
    """LLM problem wrong submission returns passed=false with feedback."""
    problem_id = _create_llm_problem(client, practice_root)
    monkeypatch.setattr(
        llm_grader.llm_client,
        "call_gemini",
        lambda prompt, system_instruction=None: json.dumps(
            {
                "passed": False,
                "feedback": "빈 함수라 원본과 같은 동작을 하지 않습니다.",
                "score": 10,
                "details": {
                    "functionality": "다름",
                    "edge_cases": "처리 안 됨",
                    "code_quality": "부족",
                },
            }
        ),
    )

    response = client.post(
        f"/api/problems/{problem_id}/submit",
        json={"code": "def normalize_score(value: int) -> int:\n    pass\n", "overwrite": False},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["passed"] is False
    assert body["feedback"]
    assert body["saved_path"] is None
    assert body["grading_method"] == "llm"


def test_submit_llm_test_case_based_correct(client, practice_root, monkeypatch):
    problem_id = _create_llm_problem(client, practice_root)
    responses = iter(
        [
            json.dumps(
                {
                    "test_cases": [
                        {
                            "description": "낮은 값 보정",
                            "call_expression": "normalize_score(-1)",
                            "expected_output": "0",
                        },
                        {
                            "description": "높은 값 보정",
                            "call_expression": "normalize_score(101)",
                            "expected_output": "100",
                        },
                        {
                            "description": "중간 값 유지",
                            "call_expression": "normalize_score(50)",
                            "expected_output": "50",
                        },
                    ]
                }
            ),
            "3개 테스트를 모두 통과했습니다.",
        ]
    )
    monkeypatch.setattr(llm_grader.llm_client, "call_gemini", lambda *args, **kwargs: next(responses))

    response = client.post(
        f"/api/problems/{problem_id}/submit",
        json={
            "code": (
                "def normalize_score(value: int) -> int:\n"
                "    if value < 0:\n"
                "        return 0\n"
                "    if value > 100:\n"
                "        return 100\n"
                "    return value\n"
            ),
            "overwrite": False,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["passed"] is True
    assert body["score"] == 100
    assert len(body["test_cases"]) == 3
    assert all(test_case["passed"] for test_case in body["test_cases"])
    assert body["feedback"] == "3개 테스트를 모두 통과했습니다."


def test_submit_llm_test_case_based_wrong(client, practice_root, monkeypatch):
    problem_id = _create_llm_problem(client, practice_root)
    responses = iter(
        [
            json.dumps(
                {
                    "test_cases": [
                        {
                            "description": "낮은 값 보정",
                            "call_expression": "normalize_score(-1)",
                            "expected_output": "0",
                        },
                        {
                            "description": "높은 값 보정",
                            "call_expression": "normalize_score(101)",
                            "expected_output": "100",
                        },
                    ]
                }
            ),
            "2개 중 1개 테스트를 통과했습니다. 높은 값 보정에서 결과가 다릅니다.",
        ]
    )
    monkeypatch.setattr(llm_grader.llm_client, "call_gemini", lambda *args, **kwargs: next(responses))

    response = client.post(
        f"/api/problems/{problem_id}/submit",
        json={"code": "def normalize_score(value: int) -> int:\n    return max(value, 0)\n", "overwrite": False},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["passed"] is False
    assert body["score"] == 50
    assert body["test_cases"][0]["passed"] is True
    assert body["test_cases"][1]["passed"] is False
    assert body["test_cases"][1]["expected"] == "100"
    assert body["test_cases"][1]["actual"] == "101"
    assert body["saved_path"] is None


@requires_gemini
def test_llm_grading_with_test_cases(client, practice_root, monkeypatch):
    """LLM grading response includes test_cases."""
    problem_id = _create_llm_problem(client, practice_root)
    _mock_llm_test_case_grading(
        monkeypatch,
        [{"description": "중간 값", "call_expression": "normalize_score(50)", "expected_output": "50"}],
    )

    response = client.post(
        f"/api/problems/{problem_id}/submit",
        json={"code": "def normalize_score(value: int) -> int:\n    return value\n", "overwrite": False},
    )

    assert response.status_code == 200
    assert isinstance(response.json()["test_cases"], list)


@requires_gemini
def test_llm_grading_correct_has_all_passed(client, practice_root, monkeypatch):
    """Correct submission marks all generated test cases as passed."""
    problem_id = _create_llm_problem(client, practice_root)
    _mock_llm_test_case_grading(
        monkeypatch,
        [
            {"description": "낮은 값", "call_expression": "normalize_score(-1)", "expected_output": "0"},
            {"description": "높은 값", "call_expression": "normalize_score(101)", "expected_output": "100"},
        ],
    )
    code = (
        "def normalize_score(value: int) -> int:\n"
        "    if value < 0:\n"
        "        return 0\n"
        "    if value > 100:\n"
        "        return 100\n"
        "    return value\n"
    )

    body = client.post(f"/api/problems/{problem_id}/submit", json={"code": code, "overwrite": False}).json()

    assert body["passed"] is True
    assert all(test_case["passed"] for test_case in body["test_cases"])


@requires_gemini
def test_llm_grading_wrong_has_failures(client, practice_root, monkeypatch):
    """Wrong submission includes failed cases with expected and actual values."""
    problem_id = _create_llm_problem(client, practice_root)
    _mock_llm_test_case_grading(
        monkeypatch,
        [
            {"description": "높은 값", "call_expression": "normalize_score(101)", "expected_output": "100"},
        ],
    )

    body = client.post(
        f"/api/problems/{problem_id}/submit",
        json={"code": "def normalize_score(value: int) -> int:\n    return value\n", "overwrite": False},
    ).json()

    assert body["passed"] is False
    assert body["test_cases"][0]["passed"] is False
    assert body["test_cases"][0]["expected"] == "100"
    assert body["test_cases"][0]["actual"] == "101"


@requires_gemini
def test_llm_grading_score(client, practice_root, monkeypatch):
    """LLM grading score stays within 0~100."""
    problem_id = _create_llm_problem(client, practice_root)
    _mock_llm_test_case_grading(
        monkeypatch,
        [
            {"description": "통과", "call_expression": "normalize_score(5)", "expected_output": "5"},
            {"description": "실패", "call_expression": "normalize_score(101)", "expected_output": "100"},
        ],
    )

    body = client.post(
        f"/api/problems/{problem_id}/submit",
        json={"code": "def normalize_score(value: int) -> int:\n    return value\n", "overwrite": False},
    ).json()

    assert 0 <= body["score"] <= 100


@requires_gemini
def test_llm_grading_feedback_exists(client, practice_root, monkeypatch):
    """LLM grading feedback is present."""
    problem_id = _create_llm_problem(client, practice_root)
    _mock_llm_test_case_grading(
        monkeypatch,
        [{"description": "중간 값", "call_expression": "normalize_score(50)", "expected_output": "50"}],
        feedback="모든 테스트를 통과했습니다.",
    )

    body = client.post(
        f"/api/problems/{problem_id}/submit",
        json={"code": "def normalize_score(value: int) -> int:\n    return value\n", "overwrite": False},
    ).json()

    assert body["feedback"]


def test_submit_llm_syntax_error(client, practice_root):
    problem_id = _create_llm_problem(client, practice_root)

    response = client.post(
        f"/api/problems/{problem_id}/submit",
        json={"code": "def normalize_score(:\n    return 1\n", "overwrite": False},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["passed"] is False
    assert body["feedback"].startswith("구문 오류:")
    assert body["grading_method"] == "llm"
    assert body["saved_path"] is None


def test_submit_pytest_still_works(client, practice_root):
    """pytest-graded problems still use the original runner flow."""
    problem_id = _create_math_problem(client, practice_root)

    response = client.post(
        f"/api/problems/{problem_id}/submit",
        json={"code": _math_solution(), "overwrite": False},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["passed"] is True
    assert body["grading_method"] == "pytest"
    assert body["feedback"] is None
    assert body["test_cases"] is None


def test_pytest_grading_no_test_cases(client, practice_root):
    """pytest grading does not include LLM test_cases."""
    problem_id = _create_math_problem(client, practice_root)

    response = client.post(
        f"/api/problems/{problem_id}/submit",
        json={"code": _math_solution(), "overwrite": False},
    )

    assert response.status_code == 200
    assert response.json()["test_cases"] is None


def test_submit_llm_without_api_key(client, practice_root, monkeypatch):
    problem_id = _create_llm_problem(client, practice_root)

    def missing_key(prompt, system_instruction=None, **kwargs):
        raise AppError("LLM_API_KEY_MISSING", "GEMINI_API_KEY 환경변수가 설정되지 않았습니다")

    monkeypatch.setattr(llm_grader.llm_client, "call_gemini", missing_key)

    response = client.post(
        f"/api/problems/{problem_id}/submit",
        json={
            "code": (
                "def normalize_score(value: int) -> int:\n"
                "    if value < 0:\n"
                "        return 0\n"
                "    if value > 100:\n"
                "        return 100\n"
                "    return value\n"
            ),
            "overwrite": False,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["passed"] is False
    assert body["feedback"] == "LLM 채점을 위해 GEMINI_API_KEY가 필요합니다"
    assert body["grading_method"] == "llm"


def test_generate_hint_for_pytest_problem(client, practice_root, monkeypatch):
    problem_id = _create_math_problem(client, practice_root)
    captured = {}

    def fake_hint(prompt, system_instruction=None, **kwargs):
        captured["prompt"] = prompt
        captured["system_instruction"] = system_instruction
        return "두 숫자를 받아 계산 결과를 반환하는 함수입니다."

    monkeypatch.setattr(hint_generator.llm_client, "call_gemini", fake_hint)

    response = client.post(f"/api/problems/{problem_id}/hint", json={"level": 1})

    assert response.status_code == 200
    assert response.json() == {"level": 1, "hint": "두 숫자를 받아 계산 결과를 반환하는 함수입니다.", "format": "markdown"}
    assert "add_numbers" in captured["prompt"]
    assert "정답 코드는 절대 포함하지 마세요" in captured["prompt"]
    assert "코딩 연습 도우미" in captured["system_instruction"]


def test_hint_level_1(client, practice_root, monkeypatch):
    """level 1 returns a conceptual hint."""
    problem_id = _create_math_problem(client, practice_root)
    monkeypatch.setattr(hint_generator.llm_client, "call_gemini", lambda prompt, system_instruction=None, **kwargs: "개념 힌트입니다.")

    response = client.post(f"/api/problems/{problem_id}/hint", json={"level": 1})

    assert response.status_code == 200
    assert response.json() == {"level": 1, "hint": "개념 힌트입니다.", "format": "markdown"}


def test_hint_level_2(client, practice_root, monkeypatch):
    """level 2 returns an input/output hint."""
    problem_id = _create_math_problem(client, practice_root)
    monkeypatch.setattr(hint_generator.llm_client, "call_gemini", lambda prompt, system_instruction=None, **kwargs: "입출력 힌트입니다.")

    response = client.post(f"/api/problems/{problem_id}/hint", json={"level": 2})

    assert response.status_code == 200
    assert response.json() == {"level": 2, "hint": "입출력 힌트입니다.", "format": "markdown"}


def test_hint_level_3(client, practice_root, monkeypatch):
    """level 3 returns an algorithm-direction hint."""
    problem_id = _create_math_problem(client, practice_root)
    monkeypatch.setattr(hint_generator.llm_client, "call_gemini", lambda prompt, system_instruction=None, **kwargs: "알고리즘 방향 힌트입니다.")

    response = client.post(f"/api/problems/{problem_id}/hint", json={"level": 3})

    assert response.status_code == 200
    assert response.json() == {"level": 3, "hint": "알고리즘 방향 힌트입니다.", "format": "markdown"}


@requires_gemini
def test_hint_format_markdown(client, practice_root, monkeypatch):
    """Hint response includes format=markdown."""
    problem_id = _create_math_problem(client, practice_root)
    monkeypatch.setattr(hint_generator.llm_client, "call_gemini", lambda prompt, system_instruction=None, **kwargs: "### 힌트\n내용")

    response = client.post(f"/api/problems/{problem_id}/hint", json={"level": 1})

    assert response.status_code == 200
    assert response.json()["format"] == "markdown"


@requires_gemini
def test_hint_level1_has_concept(client, practice_root, monkeypatch):
    """Level 1 hint contains conceptual markdown content."""
    problem_id = _create_math_problem(client, practice_root)
    monkeypatch.setattr(
        hint_generator.llm_client,
        "call_gemini",
        lambda prompt, system_instruction=None, **kwargs: "### 💡 개념 힌트\n이 함수의 개념을 설명합니다.",
    )

    hint = client.post(f"/api/problems/{problem_id}/hint", json={"level": 1}).json()["hint"]

    assert "개념" in hint


@requires_gemini
def test_hint_level2_has_io(client, practice_root, monkeypatch):
    """Level 2 hint contains input/output markdown content."""
    problem_id = _create_math_problem(client, practice_root)
    monkeypatch.setattr(
        hint_generator.llm_client,
        "call_gemini",
        lambda prompt, system_instruction=None, **kwargs: "### 📋 입출력 힌트\n**입력**\n- a\n\n**출력**\n- result",
    )

    hint = client.post(f"/api/problems/{problem_id}/hint", json={"level": 2}).json()["hint"]

    assert "입력" in hint
    assert "출력" in hint


@requires_gemini
def test_hint_level3_has_direction(client, practice_root, monkeypatch):
    """Level 3 hint contains implementation direction content."""
    problem_id = _create_math_problem(client, practice_root)
    monkeypatch.setattr(
        hint_generator.llm_client,
        "call_gemini",
        lambda prompt, system_instruction=None, **kwargs: "### 🔧 구현 방향\n1. 첫 번째 단계\n2. 두 번째 단계",
    )

    hint = client.post(f"/api/problems/{problem_id}/hint", json={"level": 3}).json()["hint"]

    assert "구현 방향" in hint


def test_generate_hint_invalid_level(client, practice_root):
    problem_id = _create_math_problem(client, practice_root)

    response = client.post(f"/api/problems/{problem_id}/hint", json={"level": 4})

    assert response.status_code == 400
    assert response.json() == {"error_code": "INVALID_HINT_LEVEL", "message": "level은 1~3 사이여야 합니다"}


def test_hint_invalid_level(client, practice_root):
    """level outside 1~3 returns 400."""
    problem_id = _create_math_problem(client, practice_root)

    response = client.post(f"/api/problems/{problem_id}/hint", json={"level": 0})

    assert response.status_code == 400
    assert response.json()["error_code"] == "INVALID_HINT_LEVEL"


def test_hint_contains_no_code(client, practice_root, monkeypatch):
    """Basic guard: generated hint text should not contain obvious code keywords."""
    problem_id = _create_math_problem(client, practice_root)
    monkeypatch.setattr(
        hint_generator.llm_client,
        "call_gemini",
        lambda prompt, system_instruction=None, **kwargs: "더하기 연산의 역할을 떠올리고 입력 두 값을 어떻게 결합할지 생각하세요.",
    )

    response = client.post(f"/api/problems/{problem_id}/hint", json={"level": 1})

    assert response.status_code == 200
    hint = response.json()["hint"]
    assert "def " not in hint
    assert "return " not in hint


def test_generate_hint_without_api_key(client, practice_root, monkeypatch):
    problem_id = _create_math_problem(client, practice_root)

    def missing_key(prompt, system_instruction=None, **kwargs):
        raise AppError("LLM_API_KEY_MISSING", "GEMINI_API_KEY 환경변수가 설정되지 않았습니다")

    monkeypatch.setattr(hint_generator.llm_client, "call_gemini", missing_key)

    response = client.post(f"/api/problems/{problem_id}/hint", json={"level": 1})

    assert response.status_code == 500
    assert response.json() == {
        "error_code": "LLM_API_KEY_MISSING",
        "message": "힌트 생성을 위해 GEMINI_API_KEY가 필요합니다",
    }


def test_submit_overwrite_false(client, practice_root):
    problem_id = _create_math_problem(client, practice_root)
    first = client.post(
        f"/api/problems/{problem_id}/submit",
        json={"code": _math_solution(), "overwrite": False},
    )
    second = client.post(
        f"/api/problems/{problem_id}/submit",
        json={"code": _math_solution(), "overwrite": False},
    )

    assert first.json()["saved_path"] == "src/math_utils.py"
    assert second.status_code == 200
    assert second.json()["passed"] is True
    assert second.json()["saved_path"] is None


def test_submit_overwrite_true(client, practice_root):
    problem_id = _create_math_problem(client, practice_root)
    client.post(
        f"/api/problems/{problem_id}/submit",
        json={"code": _math_solution(), "overwrite": False},
    )

    response = client.post(
        f"/api/problems/{problem_id}/submit",
        json={"code": _math_solution(), "overwrite": True},
    )

    assert response.status_code == 200
    assert response.json()["passed"] is True
    assert response.json()["saved_path"] == "src/math_utils.py"


def test_full_flow(client, practice_root):
    analyze = client.post("/api/repos/analyze", json={"repo_path": str(SAMPLE_REPO)})
    assert analyze.status_code == 200

    project_id = _create_project(client, practice_root)
    setup = client.post(f"/api/projects/{project_id}/setup")
    assert setup.status_code == 200

    math_problem = _generate_problem(client, project_id, "src/math_utils.py")
    string_problem = _generate_problem(client, project_id, "src/string_utils.py")
    assert math_problem.status_code == 200
    assert string_problem.status_code == 200

    problems = client.get(f"/api/projects/{project_id}/problems")
    assert problems.status_code == 200
    assert len(problems.json()["problems"]) == 2

    detail = client.get(f"/api/problems/{math_problem.json()['problem_id']}")
    assert detail.status_code == 200
    assert "raise NotImplementedError" in detail.json()["starter_code"]

    submit = client.post(
        f"/api/problems/{math_problem.json()['problem_id']}/submit",
        json={"code": _math_solution(), "overwrite": False},
    )
    assert submit.status_code == 200
    assert submit.json()["passed"] is True

    project = client.get(f"/api/projects/{project_id}")
    assert project.status_code == 200
    assert project.json()["file_counts"]["passed"] >= 1


def test_full_flow_mvp1(client, practice_root, monkeypatch):
    """MVP-1 end-to-end flow with mocked LLM calls."""
    analyze = client.post("/api/repos/analyze", json={"repo_path": str(SAMPLE_REPO)})
    assert analyze.status_code == 200

    create = client.post(
        "/api/projects",
        json={
            "repo_path": str(SAMPLE_REPO),
            "practice_root_path": str(practice_root),
            "target_extensions": [],
        },
    )
    assert create.status_code == 200
    project_id = create.json()["project_id"]

    update = client.put(f"/api/projects/{project_id}", json={"target_extensions": [".py"]})
    assert update.status_code == 200

    setup = client.post(f"/api/projects/{project_id}/setup")
    assert setup.status_code == 200

    monkeypatch.setattr(file_assessor, "ASSESS_DELAY_SECONDS", 0)
    monkeypatch.setattr(
        file_assessor.llm_client,
        "call_gemini",
        lambda prompt, system_instruction=None, **kwargs: json.dumps(
            [
                {
                    "source_path": "src/math_utils.py",
                    "suitable": True,
                    "reason": "연습 문제로 적합한 함수가 있습니다.",
                    "recommended_symbols": [
                        {
                            "symbol": "add_numbers",
                            "difficulty": "easy",
                            "reason": "명확한 입출력",
                        }
                    ],
                }
            ]
        ),
    )
    assess = client.post(f"/api/projects/{project_id}/assess")
    assert assess.status_code == 200
    recommended = assess.json()["assessments"][0]["recommended_symbols"]
    assert recommended

    generate = client.post(
        f"/api/projects/{project_id}/problems/generate",
        json={"source_path": "src/math_utils.py", "target_symbols": recommended},
    )
    assert generate.status_code == 200
    problem_id = generate.json()["problems"][0]["problem_id"]
    assert generate.json()["problems"][0]["grading_method"] == "pytest"

    problems = client.get(f"/api/projects/{project_id}/problems")
    assert problems.status_code == 200
    assert len(problems.json()["problems"]) >= 1

    detail = client.get(f"/api/problems/{problem_id}")
    assert detail.status_code == 200
    assert "raise NotImplementedError" in detail.json()["starter_code"]

    monkeypatch.setattr(hint_generator.llm_client, "call_gemini", lambda prompt, system_instruction=None, **kwargs: "개념 힌트입니다.")
    hint = client.post(f"/api/problems/{problem_id}/hint", json={"level": 1})
    assert hint.status_code == 200
    assert hint.json()["hint"] == "개념 힌트입니다."

    submit = client.post(
        f"/api/problems/{problem_id}/submit",
        json={"code": _math_solution(), "overwrite": False},
    )
    assert submit.status_code == 200
    assert submit.json()["passed"] is True

    project = client.get(f"/api/projects/{project_id}")
    assert project.status_code == 200
    assert project.json()["file_counts"]["passed"] >= 1


@requires_gemini
def test_full_flow_mvp2(client, practice_root, monkeypatch):
    """MVP-2 end-to-end flow with rich prompt, context, partial problems, hints, and test-case grading."""
    partial_file = SAMPLE_REPO / "src" / "partial_tmp.py"
    partial_file.write_text(
        "def bounded(value: int) -> int:\n"
        "    lower = 0\n"
        "    upper = 10\n"
        "    if value < lower:\n"
        "        return lower\n"
        "    if value > upper:\n"
        "        return upper\n"
        "    return value\n",
        encoding="utf-8",
    )

    analyze = client.post("/api/repos/analyze", json={"repo_path": str(SAMPLE_REPO)})
    assert analyze.status_code == 200

    create = client.post(
        "/api/projects",
        json={
            "repo_path": str(SAMPLE_REPO),
            "practice_root_path": str(practice_root),
            "target_extensions": [],
        },
    )
    assert create.status_code == 200
    project_id = create.json()["project_id"]

    update = client.put(f"/api/projects/{project_id}", json={"target_extensions": [".py"]})
    assert update.status_code == 200
    setup = client.post(f"/api/projects/{project_id}/setup")
    assert setup.status_code == 200

    def fake_assess(prompt, system_instruction=None, **kwargs):
        results = []
        if "partial_tmp.py" in prompt:
            results.append(
                {
                    "source_path": "src/partial_tmp.py",
                    "suitable": True,
                    "reason": "여러 단계의 경계값 로직",
                    "recommended_symbols": [
                        {
                            "symbol": "bounded",
                            "difficulty": "medium",
                            "reason": "핵심 조건 분기",
                            "problem_type": "function_partial",
                        }
                    ],
                }
            )
        if "src/math_utils.py" in prompt:
            results.append(
                {
                    "source_path": "src/math_utils.py",
                    "suitable": True,
                    "reason": "명확한 산술 로직",
                    "recommended_symbols": [
                        {
                            "symbol": "add_numbers",
                            "difficulty": "easy",
                            "reason": "기본 함수 구현",
                            "problem_type": "function_blank",
                        }
                    ],
                }
            )
        return json.dumps(results)

    monkeypatch.setattr(file_assessor.llm_client, "call_gemini", fake_assess)
    assess = client.post(f"/api/projects/{project_id}/assess")
    assert assess.status_code == 200
    assert any(assessment["suitable"] for assessment in assess.json()["assessments"])

    monkeypatch.setattr(
        problem_generator,
        "_call_partial_starter_llm",
        lambda prompt: (
            "def bounded(value: int) -> int:\n"
            "    lower = 0\n"
            "    upper = 10\n"
            "    # TODO: lower와 upper 범위로 value를 제한하세요\n"
            "    pass\n"
            "    return value\n"
        ),
    )
    blank = client.post(
        f"/api/projects/{project_id}/problems/generate",
        json={
            "source_path": "src/math_utils.py",
            "target_symbols": [{"symbol": "add_numbers", "difficulty": "easy", "problem_type": "function_blank"}],
        },
    )
    partial = client.post(
        f"/api/projects/{project_id}/problems/generate",
        json={
            "source_path": "src/partial_tmp.py",
            "target_symbols": [{"symbol": "bounded", "difficulty": "medium", "problem_type": "function_partial"}],
        },
    )
    assert blank.status_code == 200
    assert partial.status_code == 200
    blank_problem_id = blank.json()["problem_id"]
    partial_problem_id = partial.json()["problem_id"]

    detail = client.get(f"/api/problems/{partial_problem_id}")
    assert detail.status_code == 200
    detail_body = detail.json()
    assert "###" in detail_body["prompt"]
    assert detail_body["context"]
    assert detail_body["problem_type"] == "function_partial"

    problems = client.get(f"/api/projects/{project_id}/problems")
    assert problems.status_code == 200
    problem_types = {problem["target_symbol"]: problem["problem_type"] for problem in problems.json()["problems"]}
    assert problem_types["add_numbers"] == "function_blank"
    assert problem_types["bounded"] == "function_partial"

    monkeypatch.setattr(
        hint_generator.llm_client,
        "call_gemini",
        lambda prompt, system_instruction=None, **kwargs: "### 💡 개념 힌트\n경계값을 기준으로 값을 제한합니다.",
    )
    hint = client.post(f"/api/problems/{partial_problem_id}/hint", json={"level": 1})
    assert hint.status_code == 200
    assert hint.json()["format"] == "markdown"

    _mock_llm_test_case_grading(
        monkeypatch,
        [
            {"description": "중간 값", "call_expression": "bounded(5)", "expected_output": "5"},
            {"description": "낮은 값", "call_expression": "bounded(-1)", "expected_output": "0"},
        ],
        feedback="모든 테스트를 통과했습니다.",
    )
    correct_submit = client.post(
        f"/api/problems/{partial_problem_id}/submit",
        json={"code": partial_file.read_text(encoding="utf-8"), "overwrite": False},
    )
    assert correct_submit.status_code == 200
    assert correct_submit.json()["passed"] is True
    assert all(test_case["passed"] for test_case in correct_submit.json()["test_cases"])

    _mock_llm_test_case_grading(
        monkeypatch,
        [{"description": "낮은 값", "call_expression": "bounded(-1)", "expected_output": "0"}],
        feedback="낮은 값 경계 처리가 다릅니다.",
    )
    wrong_submit = client.post(
        f"/api/problems/{partial_problem_id}/submit",
        json={"code": "def bounded(value: int) -> int:\n    return value\n", "overwrite": False},
    )
    assert wrong_submit.status_code == 200
    assert wrong_submit.json()["passed"] is False
    assert any(not test_case["passed"] for test_case in wrong_submit.json()["test_cases"])

    assert blank_problem_id


def _create_project(client: TestClient, practice_root: Path) -> str:
    response = client.post(
        "/api/projects",
        json={
            "repo_path": str(SAMPLE_REPO),
            "practice_root_path": str(practice_root),
            "target_extensions": [".py"],
        },
    )
    assert response.status_code == 200
    return response.json()["project_id"]


def _create_project_with_setup(client: TestClient, practice_root: Path) -> str:
    project_id = _create_project(client, practice_root)
    response = client.post(f"/api/projects/{project_id}/setup")
    assert response.status_code == 200
    return project_id


def _create_planned_paper_project(client: TestClient, tmp_path: Path, monkeypatch) -> str:
    monkeypatch.setattr(
        project_service,
        "get_default_paper_practice_root",
        lambda paper_title: str(tmp_path / "paper_practice"),
    )
    response = client.post(
        "/api/projects",
        json={
            "paper_source": "arxiv",
            "paper_url": "https://arxiv.org/abs/2204.12484",
            "paper_title": "ViTPose Paper",
            "paper_abstract": "A pose estimation paper.",
            "paper_content": "Full paper content",
            "paper_metadata": {"authors": ["Yufei Xu"], "year": "2022"},
        },
    )
    assert response.status_code == 200
    project_id = response.json()["project_id"]
    with database.engine.begin() as connection:
        connection.execute(
            database.projects.update()
            .where(database.projects.c.id == project_id)
            .values(
                project_summary=json.dumps(
                    {
                        "summary": "ViTPose 핵심 구현 계획",
                        "key_algorithms": ["Patch embedding"],
                        "framework": "pytorch",
                    },
                    ensure_ascii=False,
                ),
                architecture=json.dumps(
                    {
                        "files": [
                            {
                                "path": "config.py",
                                "description": "설정",
                                "classes": [],
                                "functions": [],
                                "depends_on": [],
                            },
                            {
                                "path": "models/embedding.py",
                                "description": "Patch Embedding",
                                "classes": ["PatchEmbedding"],
                                "functions": [],
                                "depends_on": ["config.py"],
                            },
                        ]
                    },
                    ensure_ascii=False,
                ),
                dependency_graph=json.dumps(
                    {
                        "implementation_order": ["config.py", "models/embedding.py"],
                        "specifications": [
                            {
                                "file": "models/embedding.py",
                                "class": "PatchEmbedding",
                                "methods": {
                                    "forward": {
                                        "params": {"x": "Tensor"},
                                        "returns": "Tensor",
                                        "algorithm": "return input",
                                    }
                                },
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                analysis_status="planned",
            )
        )
    return project_id


def _create_math_problem(client: TestClient, practice_root: Path) -> str:
    project_id = _create_project_with_setup(client, practice_root)
    response = _generate_problem(client, project_id, "src/math_utils.py")
    assert response.status_code == 200
    return response.json()["problem_id"]


def _create_llm_problem(client: TestClient, practice_root: Path) -> str:
    source_file = SAMPLE_REPO / "src" / "llm_submit_tmp.py"
    source_file.write_text(
        "def normalize_score(value: int) -> int:\n"
        "    if value < 0:\n"
        "        return 0\n"
        "    if value > 100:\n"
        "        return 100\n"
        "    return value\n",
        encoding="utf-8",
    )
    project_id = _create_project_with_setup(client, practice_root)
    response = client.post(
        f"/api/projects/{project_id}/problems/generate",
        json={
            "source_path": "src/llm_submit_tmp.py",
            "target_symbols": [{"symbol": "normalize_score", "difficulty": "easy"}],
        },
    )
    assert response.status_code == 200
    return response.json()["problem_id"]


def _generate_problem(client: TestClient, project_id: str, source_path: str):
    return client.post(
        f"/api/projects/{project_id}/problems/generate",
        json={"source_path": source_path},
    )


def _math_solution() -> str:
    return (SAMPLE_REPO / "src" / "math_utils.py").read_text(encoding="utf-8")


def _tree_paths(nodes: list[dict]) -> set[str]:
    paths = set()
    for node in nodes:
        paths.add(node["path"])
        paths.update(_tree_paths(node.get("children", [])))
    return paths
