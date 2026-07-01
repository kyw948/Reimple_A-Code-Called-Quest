from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse


ERROR_STATUS_CODES = {
    "CLONE_FAILED": 422,
    "INVALID_HINT_LEVEL": 400,
    "FIGURE_NOT_FOUND": 404,
    "INVALID_ARXIV_URL": 400,
    "INVALID_PROJECT_MODE": 400,
    "INVALID_REPO_PATH": 400,
    "LLM_API_KEY_MISSING": 500,
    "LLM_CLIENT_UNAVAILABLE": 500,
    "LLM_RATE_LIMITED": 429,
    "PAPER_NOT_FOUND": 404,
    "PAPER_CODEGEN_FILE_FAILED": 422,
    "PAPER_PLAN_FAILED": 422,
    "PAPER_PLAN_REQUIRED": 400,
    "PDF_PARSE_FAILED": 422,
    "PROJECT_NOT_FOUND": 404,
    "PROBLEM_NOT_FOUND": 404,
    "PROBLEM_LOCKED": 400,
    "SYMBOL_NOT_FOUND": 404,
    "REPO_ALREADY_EXISTS": 409,
    "TEST_NOT_FOUND": 422,
    "BASELINE_TEST_FAILED": 422,
    "RUNNER_TIMEOUT": 408,
    "INTERNAL_ERROR": 500,
}


class AppError(HTTPException):
    def __init__(self, error_code: str, message: str):
        super().__init__(
            status_code=ERROR_STATUS_CODES.get(error_code, 500),
            detail={"error_code": error_code, "message": message},
        )


async def app_error_handler(_: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content=exc.detail)


async def http_error_handler(_: Request, exc: HTTPException) -> JSONResponse:
    if isinstance(exc.detail, dict) and "error_code" in exc.detail and "message" in exc.detail:
        return JSONResponse(status_code=exc.status_code, content=exc.detail)

    return JSONResponse(
        status_code=exc.status_code,
        content={"error_code": "INTERNAL_ERROR", "message": str(exc.detail)},
    )
