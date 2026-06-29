import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware

from app.core.errors import AppError, app_error_handler, http_error_handler
from app.db.database import create_tables
from app.services.project_service import (
    ProjectCreateRequest,
    ProjectCreateResponse,
    ProjectDetail,
    ProjectListResponse,
    ProjectSetupResponse,
    ProjectUpdateRequest,
    ProjectUpdateResponse,
    create_project,
    get_project_detail,
    list_projects,
    setup_project,
    update_project,
)
from app.services.problem_generator import (
    ProblemDetail,
    ProblemGenerateRequest,
    ProblemGenerateResponse,
    ProblemListResponse,
    generate_problem,
    get_problem_detail,
    list_project_problems,
)
from app.services.file_assessor import (
    ProjectAssessRequest,
    ProjectAssessResponse,
    ProjectAssessStartResponse,
    ProjectAssessStatusResponse,
    assess_project_files,
    get_assess_status,
    start_background_assess,
)
from app.services.hint_generator import ProblemHintRequest, ProblemHintResponse, generate_hint
from app.services.project_analyzer import ProjectAnalyzeRequest, ProjectAnalyzeResponse, analyze_project
from app.services.paper_parser import PaperParseResponse, parse_arxiv, parse_multipart_pdf, parse_pdf_upload
from app.services.paper_planner import PaperPlanRequest, PaperPlanResponse, plan_paper_project
from app.services.paper_codegen import (
    PaperCodegenStartResponse,
    PaperCodegenStatusResponse,
    get_codegen_status,
    start_code_generation,
)
from app.services.problem_tree import PracticePrepareResponse, prepare_practice
from app.services.repo_cloner import RepoCloneRequest, RepoCloneResponse, clone_repo
from app.services.repo_analyzer import RepoAnalyzeRequest, RepoAnalyzeResponse, analyze_repo
from app.services.runner import ProblemSubmitRequest, ProblemSubmitResponse, submit_problem
from app.services.warmup_generator import WarmupResponse, generate_warmup


def create_app() -> FastAPI:
    app = FastAPI(title="CodePractice API")

    frontend_origin = os.getenv("FRONTEND_ORIGIN", "http://localhost:5173")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[frontend_origin],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(HTTPException, http_error_handler)

    @app.on_event("startup")
    def on_startup() -> None:
        create_tables()

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/api/repos/analyze", response_model=RepoAnalyzeResponse, response_model_exclude_none=True)
    def post_repo_analyze(payload: RepoAnalyzeRequest) -> RepoAnalyzeResponse:
        return analyze_repo(payload.repo_path)

    @app.post("/api/repos/clone", response_model=RepoCloneResponse)
    def post_repo_clone(payload: RepoCloneRequest) -> RepoCloneResponse:
        return clone_repo(payload)

    @app.post("/api/papers/parse", response_model=PaperParseResponse)
    async def post_paper_parse(request: Request) -> PaperParseResponse:
        content_type = request.headers.get("content-type", "")
        if content_type.startswith("multipart/form-data"):
            try:
                form = await request.form()
                file = form.get("file")
                if file is None or not hasattr(file, "read"):
                    raise AppError("PDF_PARSE_FAILED", "PDF 파일을 선택하세요.")
                pdf_bytes = await file.read()
                filename = getattr(file, "filename", "paper.pdf") or "paper.pdf"
            except AssertionError:
                pdf_bytes, filename = parse_multipart_pdf(content_type, await request.body())
            return parse_pdf_upload(pdf_bytes, filename)

        payload = await request.json()
        arxiv_url = str(payload.get("arxiv_url", "")).strip()
        if not arxiv_url:
            raise AppError("INVALID_ARXIV_URL", "arXiv URL을 입력하세요.")
        return parse_arxiv(arxiv_url)

    @app.post("/api/papers/{project_id}/plan", response_model=PaperPlanResponse)
    def post_paper_plan(project_id: str, payload: PaperPlanRequest | None = None) -> PaperPlanResponse:
        return plan_paper_project(project_id, payload or PaperPlanRequest())

    @app.post("/api/papers/{project_id}/generate-code", response_model=PaperCodegenStartResponse)
    def post_paper_generate_code(project_id: str) -> PaperCodegenStartResponse:
        return start_code_generation(project_id)

    @app.get("/api/papers/{project_id}/codegen-status", response_model=PaperCodegenStatusResponse)
    def get_paper_codegen_status(project_id: str) -> PaperCodegenStatusResponse:
        return get_codegen_status(project_id)

    @app.post("/api/projects", response_model=ProjectCreateResponse)
    def post_project(payload: ProjectCreateRequest) -> ProjectCreateResponse:
        return create_project(payload)

    @app.get("/api/projects", response_model=ProjectListResponse)
    def get_projects() -> ProjectListResponse:
        return list_projects()

    @app.get("/api/projects/{project_id}", response_model=ProjectDetail)
    def get_project(project_id: str) -> ProjectDetail:
        return get_project_detail(project_id)

    @app.put("/api/projects/{project_id}", response_model=ProjectUpdateResponse)
    def put_project(project_id: str, payload: ProjectUpdateRequest) -> ProjectUpdateResponse:
        return update_project(project_id, payload)

    @app.post("/api/projects/{project_id}/setup", response_model=ProjectSetupResponse)
    def post_project_setup(project_id: str) -> ProjectSetupResponse:
        return setup_project(project_id)

    @app.post("/api/projects/{project_id}/analyze", response_model=ProjectAnalyzeResponse)
    def post_project_analyze(
        project_id: str, payload: ProjectAnalyzeRequest | None = None
    ) -> ProjectAnalyzeResponse:
        payload = payload or ProjectAnalyzeRequest()
        return analyze_project(project_id, payload)

    @app.post("/api/projects/{project_id}/prepare", response_model=PracticePrepareResponse)
    def post_project_prepare(project_id: str) -> PracticePrepareResponse:
        return prepare_practice(project_id)

    @app.post("/api/projects/{project_id}/warmup", response_model=WarmupResponse)
    def post_project_warmup(project_id: str) -> WarmupResponse:
        return generate_warmup(project_id)

    @app.post("/api/projects/{project_id}/assess", response_model=ProjectAssessResponse)
    def post_project_assess(project_id: str, payload: ProjectAssessRequest | None = None) -> ProjectAssessResponse:
        payload = payload or ProjectAssessRequest()
        return assess_project_files(project_id, payload)

    @app.post("/api/projects/{project_id}/assess/start", response_model=ProjectAssessStartResponse)
    def post_project_assess_start(project_id: str) -> ProjectAssessStartResponse:
        return start_background_assess(project_id)

    @app.get("/api/projects/{project_id}/assess/status", response_model=ProjectAssessStatusResponse)
    def get_project_assess_status(project_id: str) -> ProjectAssessStatusResponse:
        return get_assess_status(project_id)

    @app.post("/api/projects/{project_id}/problems/generate", response_model=ProblemGenerateResponse)
    def post_problem_generate(project_id: str, payload: ProblemGenerateRequest) -> ProblemGenerateResponse:
        return generate_problem(project_id, payload)

    @app.get("/api/projects/{project_id}/problems", response_model=ProblemListResponse)
    def get_project_problems(project_id: str) -> ProblemListResponse:
        return list_project_problems(project_id)

    @app.get("/api/problems/{problem_id}", response_model=ProblemDetail)
    def get_problem(problem_id: str) -> ProblemDetail:
        return get_problem_detail(problem_id)

    @app.post("/api/problems/{problem_id}/submit", response_model=ProblemSubmitResponse)
    def post_problem_submit(problem_id: str, payload: ProblemSubmitRequest) -> ProblemSubmitResponse:
        return submit_problem(problem_id, payload)

    @app.post("/api/problems/{problem_id}/hint", response_model=ProblemHintResponse)
    def post_problem_hint(problem_id: str, payload: ProblemHintRequest) -> ProblemHintResponse:
        return generate_hint(problem_id, payload)

    return app


app = create_app()
