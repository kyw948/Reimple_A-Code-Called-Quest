# MVP-1.md

## 목표
실제 GitHub repo를 대상으로 LLM이 연습 가능한 파일과 함수를 자동 판별하고,
테스트 파일이 없는 repo도 지원한다.
Python 이외의 언어도 대상 확장자로 선택할 수 있다.

## MVP-0 대비 변경점

### 1. GitHub URL clone
- SetupPage에서 repo 경로 대신 GitHub URL 입력 가능
- URL 입력 시 로컬에 자동 clone
- clone 위치: 사용자 지정 또는 기본 경로 (예: ~/.codepractice/repos/{repo_name})
- 로컬 경로 입력도 기존처럼 유지
- private repo는 MVP-1에서 미지원 (public만)

### 2. 대상 확장자 선택
MVP-0은 .py 고정이었다.
MVP-1은 사용자가 연습할 확장자를 직접 선택한다.

- AnalyzePage에서 확장자 통계를 보고 대상 확장자 체크박스로 선택
- 예: .py, .c, .cpp, .java, .js, .ts 등
- 선택한 확장자 파일만 LLM 분석 및 문제 생성 대상
- 비대상 파일은 MVP-0처럼 practice_root에 그대로 복사

### 3. LLM 기반 파일 분석 및 문제 후보 선정
MVP-0은 AST 조건만으로 후보를 골랐고, 파일당 문제 1개만 생성했다.
MVP-1은 LLM이 각 대상 파일을 읽고 문제로 적합한 함수를 여러 개 찾는다.

#### LLM 파일 분석 기준
LLM이 파일을 보고 아래를 판단한다:
- 이 파일에 연습 문제로 만들 수 있는 함수가 있는가?
- 적합한 함수가 여러 개면 모두 추천한다
- 각 함수의 학습 가치, 난이도, 독립성을 평가한다
- 부적합 사유는 무엇인가?

#### 적합 판정
- 명확한 입출력이 있는 함수
- 알고리즘/로직이 포함된 함수
- 다른 파일 의존성이 적은 함수
- docstring이나 type hint로 의도가 파악 가능한 함수

#### 부적합 판정
- 설정/config만 있는 파일 (__init__.py, config.py, setup.py 등)
- 외부 서비스 의존이 강한 파일 (API 호출, DB 접속 등)
- body가 너무 단순한 함수 (단순 위임, alias)
- 클래스 초기화만 있는 파일
- 파일 전체가 import와 상수 정의뿐인 경우

#### 분석 결과
각 파일에 대해 아래를 반환한다:
- suitable: true/false
- reason: 판정 사유 (한 줄)
- recommended_symbols: 문제로 만들 함수명 배열 (suitable=true일 때)
- 각 함수별 difficulty: easy/medium/hard

#### 한 파일 여러 문제
- LLM이 추천한 함수가 여러 개면 함수마다 문제를 1개씩 생성
- 각 문제의 starter_code는 해당 함수만 TODO로 치환, 나머지는 원본 유지
- 같은 파일에서 동시에 여러 함수를 비우지 않음 (문제마다 독립)
- PracticePage에서 같은 파일의 여러 문제를 구분하여 표시

#### AnalyzePage 표시
- 파일 트리에 적합/부적합 아이콘 표시 (✓ 적합 / ✗ 부적합)
- 적합 파일은 추천 함수 개수 표시 (예: "✓ 3개 함수")
- 부적합 파일은 사유 표시 (예: "설정 파일", "외부 의존성 과다")
- 사용자가 부적합 판정을 override 가능 (체크박스로 강제 포함)
- "분석 결과대로 문제 생성" 버튼

### 4. 테스트 파일 없이 문제 생성
MVP-0은 매칭되는 pytest 파일이 있어야만 문제를 생성했다.
MVP-1은 테스트 파일이 없어도 문제를 생성한다.

채점 방식을 2가지로 분리한다:

#### 방식 A: pytest 채점 (기존, Python만)
- 매칭되는 테스트 파일이 있는 경우
- MVP-0과 동일하게 동작
- Python 파일 전용

#### 방식 B: LLM 채점 (신규, 모든 언어)
- 매칭되는 테스트 파일이 없는 경우
- Python 이외 언어인 경우
- 원본 함수 코드를 참조하여 LLM이 제출 코드를 채점
- 채점 기준: 기능 동일성, 엣지 케이스 처리, 코드 품질
- LLM 채점 결과: passed/failed + 피드백 메시지

#### 채점 방식 자동 선택
- Python + 테스트 파일 있음 → pytest 채점
- Python + 테스트 파일 없음 → LLM 채점
- Python 이외 언어 → LLM 채점
- 문제 상세에 grading_method 필드 추가: "pytest" 또는 "llm"

### 5. 힌트 기능 (기본)
- PracticePage 오른쪽 패널에 "힌트 보기" 버튼 추가
- 클릭 시 단계별 힌트 제공 (AGENT_POLICY.md 1~3단계)
  - 1단계: 개념 힌트
  - 2단계: 입출력 힌트
  - 3단계: 알고리즘 방향
- LLM으로 생성 (원본 코드 참조, 정답 직접 노출 금지)

## LLM 설정
MVP-1은 Gemini를 사용한다.

- 모델: gemini-2.5-flash (또는 사용 가능한 최신 모델)
- API 키: 환경변수 GEMINI_API_KEY
- 용도: 파일 분석, 채점, 힌트 생성
- 호출 라이브러리: google-genai 또는 REST API

## 제외 (MVP-2 이후)
- Docker sandbox
- Tauri/Electron 데스크톱 앱
- 논문/모델 figure 문제
- private repo 지원
- 실패 로그 자동 해석
- 언어별 네이티브 테스트 프레임워크 (JUnit, Jest 등)

## DB 변경

### files 테이블 추가 컬럼
```sql
suitable INTEGER  -- LLM 판정: 1=적합, 0=부적합, NULL=미분석
suitable_reason TEXT  -- 판정 사유
```

### problems 테이블 추가 컬럼
```sql
grading_method TEXT NOT NULL DEFAULT 'pytest'  -- 'pytest' 또는 'llm'
original_code TEXT  -- LLM 채점 시 비교용 원본 코드 (pytest 채점이면 NULL)
difficulty TEXT  -- easy/medium/hard
```

## API 변경

### POST /api/repos/clone (신규)
```json
Request:
{
  "github_url": "https://github.com/user/repo",
  "clone_path": "/optional/local/path"
}

Response:
{
  "repo_path": "/home/user/.codepractice/repos/repo",
  "already_exists": false
}
```

에러:
- CLONE_FAILED: 422 (URL 잘못됨, 네트워크 오류 등)
- REPO_ALREADY_EXISTS: 409 (이미 clone됨, 기존 경로 반환)

### POST /api/projects/{project_id}/assess (신규)
LLM이 대상 파일들을 분석하여 문제 적합성을 판단한다.

```json
Request:
{
  "source_paths": ["src/model.py", "src/config.py", "src/utils.py"]
}

Response:
{
  "assessments": [
    {
      "source_path": "src/model.py",
      "suitable": true,
      "reason": "명확한 입출력을 가진 함수 2개 발견",
      "recommended_symbols": [
        {"symbol": "forward", "difficulty": "hard"},
        {"symbol": "init_weights", "difficulty": "medium"}
      ]
    },
    {
      "source_path": "src/config.py",
      "suitable": false,
      "reason": "설정 상수만 포함된 파일",
      "recommended_symbols": []
    },
    {
      "source_path": "src/utils.py",
      "suitable": true,
      "reason": "독립적인 유틸리티 함수 다수 포함",
      "recommended_symbols": [
        {"symbol": "resize_image", "difficulty": "medium"},
        {"symbol": "normalize", "difficulty": "easy"},
        {"symbol": "augment_data", "difficulty": "hard"}
      ]
    }
  ]
}
```

### POST /api/projects/{project_id}/problems/generate 변경
```json
Request:
{
  "source_path": "src/model.py",
  "target_symbol": "forward"
}
```

- target_symbol 파라미터 추가 (assess 결과에서 선택된 함수)
- target_symbol 생략 시 해당 파일의 추천 함수 전체에 대해 문제 생성

Response (여러 문제 생성 시):
```json
{
  "problems": [
    {
      "problem_id": "uuid-1",
      "file_id": "uuid",
      "source_path": "src/model.py",
      "target_symbol": "forward",
      "test_path": null,
      "grading_method": "llm"
    },
    {
      "problem_id": "uuid-2",
      "file_id": "uuid",
      "source_path": "src/model.py",
      "target_symbol": "init_weights",
      "test_path": null,
      "grading_method": "llm"
    }
  ]
}
```

- test_path가 null이면 grading_method는 "llm"
- TEST_NOT_FOUND 에러 대신 LLM 채점 문제로 생성

### POST /api/problems/{problem_id}/submit 변경
- grading_method에 따라 분기
- pytest: 기존과 동일
- llm: Gemini API 호출하여 채점, feedback 필드 추가

```json
Response (LLM 채점):
{
  "passed": true,
  "feedback": "기능이 원본과 동일하게 구현되었습니다.",
  "stdout": null,
  "stderr": null,
  "duration_ms": 1200,
  "saved_path": "src/model.py"
}
```

### POST /api/problems/{problem_id}/hint (신규)
```json
Request:
{
  "level": 1
}

Response:
{
  "level": 1,
  "hint": "이 함수는 입력 텐서의 차원을 변환하는 역할을 합니다."
}
```

## SetupPage 변경
- 입력 모드 전환: "로컬 경로" / "GitHub URL" 탭 또는 토글
- GitHub URL 모드:
  - URL 입력 필드
  - "Clone & 분석" 버튼
  - clone 진행 상태 표시
- 로컬 경로 모드: 기존과 동일

## AnalyzePage 변경
- 확장자 통계에서 대상 확장자를 체크박스로 선택 (MVP-0은 .py 고정)
- setup 후 선택한 확장자의 대상 파일 목록을 POST /api/projects/{project_id}/assess로 LLM 분석
- 분석 중 진행률 표시 ("파일 분석 중... 3/12")
- 결과: 파일 트리에 적합(✓)/부적합(✗) 아이콘 + 사유 + 추천 함수 개수
- 부적합 파일도 체크박스로 강제 포함 가능 (override)
- "문제 생성" 버튼 → 적합 판정 + override 파일의 추천 함수 전체에 대해 문제 생성
- 테스트 파일이 없거나 Python 이외 언어는 "(LLM 채점)" 라벨 표시

## PracticePage 변경
- 왼쪽 파일 목록: 같은 파일에 여러 문제가 있으면 하위 항목으로 함수명 표시
  - 예: src/model.py > forward, src/model.py > init_weights
- 오른쪽 패널에 "힌트 보기" 버튼 추가
- LLM 채점 문제는 결과에 feedback 표시

## 환경변수 추가
```env
GEMINI_API_KEY=...
GITHUB_CLONE_BASE_PATH=~/.codepractice/repos
```

## 완료 기준
- GitHub URL로 public repo clone 가능
- 대상 확장자를 사용자가 선택 가능
- LLM이 대상 파일을 분석하여 적합/부적합 판정
- 한 파일에서 여러 함수가 추천되면 함수마다 문제 생성
- 적합 파일만 자동으로 문제 생성 (사용자 override 가능)
- Python + 테스트 있는 파일 → pytest 채점 동작
- 테스트 없거나 다른 언어 → LLM 채점 동작
- 힌트 단계별 제공
