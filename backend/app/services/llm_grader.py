import ast
import json
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import Any

from app.core.errors import AppError
from app.services import llm_client


SYSTEM_INSTRUCTION = """너는 코딩 테스트 출제자이다.
주어진 함수에 대한 테스트 케이스를 생성하라.
반드시 JSON 형식으로만 응답하라."""

FEEDBACK_SYSTEM_INSTRUCTION = """너는 코딩 연습 채점관이다.
테스트 케이스 실행 결과를 보고 학습자에게 한국어 피드백을 제공하라.
정답 코드는 포함하지 마라."""


@dataclass
class LlmTestCaseResult:
    description: str
    call_expression: str
    expected: str | None
    actual: str | None
    passed: bool


@dataclass
class LlmGradeResult:
    passed: bool
    feedback: str
    score: int | None = None
    test_cases: list[LlmTestCaseResult] | None = None


def grade_llm_submission(
    original_code: str,
    submitted_code: str,
    target_symbol: str,
    full_file_code: str = "",
    source_path: str = "",
) -> LlmGradeResult:
    if source_path and not source_path.lower().endswith(".py"):
        return _grade_non_python_submission(original_code, submitted_code, target_symbol, source_path)

    try:
        compile(submitted_code, "<string>", "exec")
    except SyntaxError as exc:
        return LlmGradeResult(passed=False, feedback=f"구문 오류: {exc.msg}")

    submitted_function = _extract_function_source(submitted_code, target_symbol)
    if submitted_function is None:
        return LlmGradeResult(passed=False, feedback=f"제출 코드에서 {target_symbol} 함수를 찾을 수 없습니다")

    try:
        parsed_response = llm_client.call_gemini_with_validation(
            _build_test_case_prompt(original_code, full_file_code),
            SYSTEM_INSTRUCTION,
            {"test_cases": list},
            max_retries=1,
        )
        if parsed_response is None:
            if _last_validation_error_code() == "LLM_API_KEY_MISSING" or not llm_client.get_gemini_api_key():
                return LlmGradeResult(passed=False, feedback="LLM 채점을 위해 GEMINI_API_KEY가 필요합니다")
            raise ValueError("Invalid test case response.")
        raw_response = json.dumps(parsed_response)
    except AppError as exc:
        if exc.detail.get("error_code") == "LLM_API_KEY_MISSING":
            return LlmGradeResult(passed=False, feedback="LLM 채점을 위해 GEMINI_API_KEY가 필요합니다")
        raise
    except ValueError:
        return _grade_with_legacy_comparison(original_code, submitted_function)

    try:
        test_cases = _parse_test_cases(raw_response)
    except (json.JSONDecodeError, ValueError):
        try:
            return _legacy_grade_from_response(raw_response)
        except (json.JSONDecodeError, ValueError):
            return _grade_with_legacy_comparison(original_code, submitted_function)

    test_results = [_run_and_compare_test_case(submitted_code, test_case) for test_case in test_cases]
    passed_count = sum(1 for result in test_results if result.passed)
    total_count = len(test_results)
    passed = total_count > 0 and passed_count == total_count
    score = int((passed_count / total_count) * 100) if total_count else 0
    feedback = _build_default_feedback(test_results, passed_count, total_count)
    try:
        generated_feedback = _call_feedback_llm(_build_feedback_prompt(test_results, passed_count, total_count)).strip()
        if generated_feedback:
            feedback = generated_feedback
    except Exception:
        pass

    return LlmGradeResult(
        passed=passed,
        feedback=feedback,
        score=score,
        test_cases=test_results,
    )


def _grade_non_python_submission(
    original_code: str,
    submitted_code: str,
    target_symbol: str,
    source_path: str,
) -> LlmGradeResult:
    try:
        parsed_response = llm_client.call_gemini_with_validation(
            _build_non_python_grade_prompt(original_code, submitted_code, target_symbol, source_path),
            SYSTEM_INSTRUCTION,
            {"passed": bool},
            max_retries=1,
        )
        if parsed_response is None or not isinstance(parsed_response, dict):
            if _last_validation_error_code() == "LLM_API_KEY_MISSING" or not llm_client.get_gemini_api_key():
                return LlmGradeResult(passed=False, feedback="LLM 채점을 위해 GEMINI_API_KEY가 필요합니다")
            raise ValueError("Invalid non-Python grade response.")
        return _legacy_grade_from_response(parsed_response)
    except AppError as exc:
        if exc.detail.get("error_code") == "LLM_API_KEY_MISSING":
            return LlmGradeResult(passed=False, feedback="LLM 채점을 위해 GEMINI_API_KEY가 필요합니다")
        raise
    except (json.JSONDecodeError, ValueError):
        return LlmGradeResult(passed=False, feedback="채점 중 오류가 발생했습니다")


def _build_non_python_grade_prompt(
    original_code: str,
    submitted_code: str,
    target_symbol: str,
    source_path: str,
) -> str:
    extension = Path(source_path).suffix or "non-Python"
    return f"""아래 {extension} 함수의 원본 코드와 제출 코드를 비교하여 채점해주세요.

파일 경로: {source_path}
대상 함수/메서드: {target_symbol}

## 원본 코드
```
{original_code}
```

## 제출 코드
```
{submitted_code}
```

채점 기준:
1. 원본과 같은 입력에 대해 같은 결과를 내는가?
2. 경계값과 예외 상황을 비슷하게 처리하는가?
3. 코드가 해당 언어 문법에 맞고 읽기 쉬운가?

아래 JSON 형식으로만 응답하세요:
{{
  "passed": true 또는 false,
  "feedback": "채점 결과에 대한 한국어 피드백",
  "score": 0~100
}}"""


def _call_test_case_llm(prompt: str) -> str:
    return llm_client.call_gemini(prompt, SYSTEM_INSTRUCTION)


def _call_feedback_llm(prompt: str) -> str:
    return llm_client.call_gemini(prompt, FEEDBACK_SYSTEM_INSTRUCTION, response_mime_type=None)


def _build_test_case_prompt(original_code: str, full_file_code: str) -> str:
    return f"""아래 함수에 대한 테스트 케이스를 생성해주세요.

함수 코드:
```
{original_code}
```

파일 전체 코드 (import 등 참고용):
```
{full_file_code}
```

규칙:
- 일반 케이스 3~5개
- 엣지 케이스 2~3개
- 각 케이스는 함수를 직접 호출하는 Python 표현식으로 작성
- 실행 가능한 코드여야 함

아래 JSON 형식으로 응답하세요:
{{
  "test_cases": [
    {{
      "description": "양수 더하기",
      "call_expression": "add_numbers(1, 2)",
      "expected_output": "3"
    }}
  ]
}}

call_expression은 함수를 직접 호출하는 코드여야 합니다.
expected_output은 repr() 결과 문자열이어야 합니다.
클래스 메서드인 경우 인스턴스 생성 코드를 setup에 포함하세요."""


def _build_feedback_prompt(test_results: list[LlmTestCaseResult], passed_count: int, total_count: int) -> str:
    return f"""아래 테스트 결과를 보고 종합 피드백을 작성해주세요.

테스트 결과:
{json.dumps([result.__dict__ for result in test_results], ensure_ascii=False, indent=2)}

통과: {passed_count}/{total_count}

피드백은 한국어로, 3~5문장으로 작성하세요.
틀린 부분이 있으면 어디가 다른지 구체적으로 알려주세요.
정답 코드는 포함하지 마세요."""


def _extract_function_source(source: str, target_symbol: str) -> str | None:
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == target_symbol:
            lines = source.splitlines(keepends=True)
            return "".join(lines[node.lineno - 1 : node.end_lineno])
    return None


def _parse_grade_json(raw_response: str) -> dict:
    text = raw_response.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise ValueError("LLM grade response must be an object.")
    return parsed


def _parse_test_cases(raw_response: str) -> list[dict[str, Any]]:
    parsed = _parse_grade_json(raw_response)
    value = parsed.get("test_cases")
    if not isinstance(value, list) or not value:
        raise ValueError("LLM test case response must contain test_cases.")

    test_cases: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        call_expression = str(item.get("call_expression") or "").strip()
        if not call_expression:
            continue
        test_cases.append(item)
    if not test_cases:
        raise ValueError("No executable test cases.")
    return test_cases


def _legacy_grade_from_response(raw_response: str | dict) -> LlmGradeResult:
    parsed = raw_response if isinstance(raw_response, dict) else _parse_grade_json(raw_response)
    if "passed" not in parsed:
        raise ValueError("Not a legacy grade response.")
    return LlmGradeResult(
        passed=bool(parsed.get("passed", False)),
        feedback=str(parsed.get("feedback") or "피드백이 없습니다"),
        score=_normalize_score(parsed.get("score")),
        test_cases=None,
    )


def _grade_with_legacy_comparison(original_code: str, submitted_function: str) -> LlmGradeResult:
    try:
        parsed_response = llm_client.call_gemini_with_validation(
            _build_legacy_prompt(original_code, submitted_function),
            SYSTEM_INSTRUCTION,
            {"passed": bool},
            max_retries=1,
        )
        if parsed_response is None or not isinstance(parsed_response, dict):
            if _last_validation_error_code() == "LLM_API_KEY_MISSING" or not llm_client.get_gemini_api_key():
                return LlmGradeResult(passed=False, feedback="LLM 채점을 위해 GEMINI_API_KEY가 필요합니다")
            raise ValueError("Invalid legacy grade response.")
        return _legacy_grade_from_response(parsed_response)
    except AppError as exc:
        if exc.detail.get("error_code") == "LLM_API_KEY_MISSING":
            return LlmGradeResult(passed=False, feedback="LLM 채점을 위해 GEMINI_API_KEY가 필요합니다")
        raise
    except (json.JSONDecodeError, ValueError):
        return LlmGradeResult(passed=False, feedback="채점 중 오류가 발생했습니다")


def _last_validation_error_code() -> str | None:
    exc = llm_client.get_last_validation_error()
    if isinstance(exc, AppError):
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        return detail.get("error_code")
    return None


def _run_and_compare_test_case(submitted_code: str, test_case: dict[str, Any]) -> LlmTestCaseResult:
    result = run_test_case(submitted_code, test_case)
    expected = _expected_value(test_case)
    actual = result.get("output") if result.get("success") else result.get("error")
    passed = bool(result.get("success")) and _matches_expected(result, test_case)
    return LlmTestCaseResult(
        description=str(test_case.get("description") or "테스트 케이스"),
        call_expression=str(test_case.get("call_expression") or ""),
        expected=expected,
        actual=str(actual) if actual is not None else None,
        passed=passed,
    )


def run_test_case(submitted_code: str, test_case: dict[str, Any], timeout: int = 10) -> dict[str, Any]:
    script = _build_test_runner_script(submitted_code, test_case)
    with tempfile.TemporaryDirectory() as temp_dir:
        try:
            result = subprocess.run(
                [sys.executable, "-c", script],
                cwd=temp_dir,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return {"success": False, "error": "TimeoutError: test case timed out"}

    try:
        return json.loads(result.stdout.strip())
    except json.JSONDecodeError:
        error = result.stderr.strip() or result.stdout.strip() or "테스트 실행 결과를 파싱하지 못했습니다"
        return {"success": False, "error": error[:500]}


def _build_test_runner_script(submitted_code: str, test_case: dict[str, Any]) -> str:
    setup = str(test_case.get("setup") or "")
    call_expression = str(test_case["call_expression"])
    return "\n".join(
        [
            "import json",
            "try:",
            f"    exec({submitted_code!r})",
            f"    exec({setup!r})",
            f"    result = eval({call_expression!r})",
            "    shape = list(result.shape) if hasattr(result, 'shape') else None",
            "    print(json.dumps({'success': True, 'output': repr(result), 'type': type(result).__name__, 'shape': shape}))",
            "except Exception as e:",
            "    print(json.dumps({'success': False, 'error': type(e).__name__ + ': ' + str(e)}))",
        ]
    )


def _matches_expected(result: dict[str, Any], test_case: dict[str, Any]) -> bool:
    if "expected_output" in test_case:
        return str(result.get("output")) == str(test_case.get("expected_output"))
    if "expected_type" in test_case:
        expected_type = str(test_case.get("expected_type") or "").split(".")[-1]
        if str(result.get("type")) != expected_type:
            return False
    if "expected_shape" in test_case:
        return str(result.get("shape")) == str(test_case.get("expected_shape"))
    return bool(result.get("success"))


def _expected_value(test_case: dict[str, Any]) -> str | None:
    if "expected_output" in test_case:
        return str(test_case.get("expected_output"))
    expected_parts = []
    if "expected_type" in test_case:
        expected_parts.append(f"type={test_case.get('expected_type')}")
    if "expected_shape" in test_case:
        expected_parts.append(f"shape={test_case.get('expected_shape')}")
    return ", ".join(expected_parts) if expected_parts else None


def _build_default_feedback(test_results: list[LlmTestCaseResult], passed_count: int, total_count: int) -> str:
    if passed_count == total_count:
        return f"{total_count}개 테스트 케이스를 모두 통과했습니다."
    failed = [result for result in test_results if not result.passed]
    first = failed[0] if failed else None
    if first is None:
        return f"{total_count}개 중 {passed_count}개 테스트를 통과했습니다."
    return (
        f"{total_count}개 중 {passed_count}개 테스트를 통과했습니다. "
        f"'{first.description}' 케이스에서 예상값 {first.expected}, 실제값 {first.actual}로 차이가 있습니다."
    )


def _normalize_score(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _build_legacy_prompt(original_code: str, submitted_code: str) -> str:
    return f"""아래 원본 함수와 제출된 함수를 비교하여 채점해주세요.

## 원본 함수
```
{original_code}
```

## 제출된 함수
```
{submitted_code}
```

아래 JSON 형식으로 응답하세요:
{{
  "passed": true 또는 false,
  "feedback": "채점 결과에 대한 상세 피드백 (한국어)",
  "score": 0~100
}}"""
