# MVP-2.md

## 목표
문제 출제, 채점, 힌트의 LLM 품질을 개선하고,
프로그램 전체 맥락을 활용한 문제 생성을 지원한다.

## MVP-1 대비 변경점

### 1. 문제 출제 개선

#### 현재 문제점
- 한 파일 안의 함수를 하나 가리고 전체를 구현하게 하는 패턴만 반복됨
- 가린 함수에 대한 설명이 부족함 (시그니처 + docstring 수준)
- 파일 단위로만 보기 때문에 프로그램 전체 맥락이 없음

#### 개선 1: LLM 기반 문제 설명 생성
MVP-1까지의 prompt는 "아래 함수를 구현하세요" + 시그니처 + docstring이었다.
MVP-2에서는 Gemini가 함수를 분석하여 풍부한 문제 설명을 생성한다.

문제 설명에 포함할 내용:
- 이 함수가 프로그램에서 하는 역할
- 입력과 출력의 의미 (단순 타입이 아니라 "무엇을 나타내는" 값인지)
- 기대 동작 설명 (구체적 시나리오 1~2개)
- 주의할 엣지 케이스
- 난이도 표시

예시 (기존):
```
아래 함수를 구현하세요.
forward(x: Tensor) -> Tensor
입력 텐서를 변환하여 반환합니다.
```

예시 (개선 후):
```
## forward 함수 구현

이 함수는 ViTPose 모델의 백본 네트워크에서 입력 이미지 텐서를
특징 맵으로 변환하는 핵심 추론 함수입니다.

### 입력
- x (Tensor): 배치 이미지 텐서, shape [B, 3, H, W]

### 출력
- Tensor: 특징 맵, shape [B, C, H/16, W/16]

### 동작
1. 입력 이미지를 패치 임베딩으로 변환
2. 트랜스포머 블록을 순차 통과
3. 최종 특징 맵 반환

### 참고
- 이 함수는 models/backbone.py의 ViTBackbone 클래스 메서드입니다
- __init__에서 정의된 self.patch_embed, self.blocks를 사용합니다
- 난이도: ★★★ (Hard)
```

#### 개선 2: 프로그램 맥락 제공
현재는 파일 1개만 보고 문제를 만든다.
MVP-2에서는 관련 파일의 맥락도 함께 제공한다.

맥락 수집 방법:
- 해당 파일이 import하는 모듈 목록
- 해당 함수를 호출하는 다른 파일 (간단한 grep 또는 AST 분석)
- 같은 클래스의 __init__ 내용 (멤버 변수 파악)
- 프로젝트의 README 또는 설명 파일

맥락 제공 수준 (난이도에 따라 조절):
- easy: 맥락 전부 제공 + 구현 방향 힌트
- medium: import와 클래스 구조만 제공
- hard: 함수 시그니처와 docstring만 제공

#### 개선 3: 문제 유형 다양화
MVP-1까지는 "함수 전체 구현"만 있었다.
MVP-2에서는 문제 유형을 추가한다:

- function_blank (기존): 함수 전체를 구현
- function_partial: 함수의 일부만 빈칸 (TODO 부분 표시)
- bug_fix: 의도적 버그가 있는 코드를 고치기
- refactor: 동작하는 코드를 더 효율적으로 개선

MVP-2에서는 function_blank + function_partial만 구현한다.
bug_fix, refactor는 MVP-3 이후.

function_partial 동작:
- LLM이 함수에서 핵심 로직 부분만 골라서 빈칸 처리
- 나머지 코드(변수 선언, 입력 처리 등)는 그대로 둠
- 힌트 수준의 주석을 빈칸 위치에 추가

예시:
```python
def forward(self, x):
    # 패치 임베딩
    x = self.patch_embed(x)
    
    # TODO: 트랜스포머 블록을 순차적으로 통과시키세요
    # hint: self.blocks를 사용합니다
    _______________
    
    # 최종 정규화
    x = self.norm(x)
    return x
```

### 2. 채점 결과 개선

#### 현재 문제점
- LLM이 문장형으로만 피드백을 줌
- 구체적으로 어떤 입력에서 틀렸는지 알 수 없음

#### 개선: 테스트 케이스 기반 채점
LLM 채점 시 원본 함수를 기반으로 테스트 케이스를 생성하고,
제출 코드를 실제로 실행하여 결과를 비교한다.

채점 흐름:
1. LLM이 원본 함수를 보고 테스트 케이스 5~10개 생성
   - 일반 케이스 3~5개
   - 엣지 케이스 2~3개
   - 각 케이스: input, expected_output
2. 제출된 함수를 실제로 실행하여 각 테스트 케이스의 actual_output 수집
3. expected vs actual 비교
4. LLM에 결과를 보내서 종합 피드백 생성

채점 결과 형식:
```json
{
  "passed": false,
  "score": 70,
  "test_cases": [
    {
      "input": "forward(torch.randn(1, 3, 256, 256))",
      "expected": "shape: [1, 768, 16, 16]",
      "actual": "shape: [1, 768, 8, 8]",
      "passed": false
    },
    {
      "input": "forward(torch.randn(2, 3, 128, 128))",
      "expected": "shape: [2, 768, 8, 8]",
      "actual": "shape: [2, 768, 8, 8]",
      "passed": true
    }
  ],
  "feedback": "대부분의 로직이 정확하지만, 256x256 입력에서 출력 크기가 맞지 않습니다. stride 설정을 확인해보세요.",
  "grading_method": "llm"
}
```

#### 채점 결과 UI
```
채점 결과: 실패 (7/10 통과)

테스트 케이스:
✓ forward(randn(2,3,128,128)) → [2,768,8,8]
✓ forward(randn(1,3,64,64))   → [1,768,4,4]
✗ forward(randn(1,3,256,256)) → 예상: [1,768,16,16], 실제: [1,768,8,8]
✗ forward(empty tensor)       → 예상: ValueError, 실제: RuntimeError
...

피드백:
대부분의 로직이 정확하지만, 256x256 입력에서 출력 크기가
맞지 않습니다. stride 설정을 확인해보세요.
```

#### pytest 채점은 변경 없음
기존 pytest 채점 문제는 그대로 유지.

### 3. 힌트 UI 개선

#### 현재 문제점
- 힌트가 줄글로 나와서 보기 불편
- 오른쪽 패널에 채점 결과와 힌트가 섞여서 복잡

#### 개선: 힌트 팝업
힌트를 오른쪽 패널이 아닌 모달 팝업으로 표시한다.

- 에디터 하단의 "힌트" 버튼 클릭 → 모달 팝업 열림
- 팝업 안에서 레벨 1/2/3 탭으로 전환
- 팝업은 드래그로 위치 이동 가능 (에디터와 동시에 볼 수 있도록)
- 팝업 닫기 버튼

#### 힌트 포맷 개선
LLM에게 마크다운 형식으로 힌트를 생성하게 하고, 렌더링한다.

레벨 1 (개념):
```markdown
### 💡 개념 힌트
이 함수는 **입력 이미지를 특징 맵으로 변환**하는 역할입니다.

- 컴퓨터 비전에서 백본 네트워크의 핵심 부분
- Vision Transformer 구조를 따름
```

레벨 2 (입출력):
```markdown
### 📋 입출력 힌트

**입력**
- `x`: 이미지 텐서 `[B, 3, H, W]`

**출력**
- 특징 맵 텐서 `[B, C, H/16, W/16]`

**예시**
- 입력 `[1, 3, 256, 256]` → 출력 `[1, 768, 16, 16]`
```

레벨 3 (알고리즘):
```markdown
### 🔧 구현 방향

1. `self.patch_embed(x)`로 패치 임베딩
2. 각 `self.blocks`를 순서대로 통과
3. `self.norm(x)`로 정규화
4. 결과 반환

> 💡 for 루프로 blocks를 순회하면 됩니다
```

## API 변경

### POST /api/projects/{project_id}/problems/generate 변경
문제 생성 시 LLM이 풍부한 prompt를 생성한다.

- 파일 내용 + 프로젝트 맥락(import, 클래스 구조)을 LLM에 전달
- LLM이 마크다운 형식의 문제 설명 생성
- problem_type 필드 추가: "function_blank" 또는 "function_partial"

### POST /api/problems/{problem_id}/submit 변경 (LLM 채점)
- LLM이 테스트 케이스 생성
- 제출 코드 실행하여 결과 수집
- 테스트 케이스별 passed/failed + 종합 피드백

Response에 test_cases 필드 추가.

### POST /api/problems/{problem_id}/hint 변경
- 마크다운 형식으로 힌트 생성
- 힌트 응답에 format: "markdown" 필드 추가

## DB 변경

### problems 테이블 변경
```sql
problem_type TEXT NOT NULL DEFAULT 'function_blank'  -- 'function_blank' 또는 'function_partial'
context TEXT  -- 프로그램 맥락 정보 (import, 클래스 구조 등)
```

## Frontend 변경

### PracticePage
- 문제 설명: 마크다운 렌더링 (react-markdown 사용)
- 채점 결과: 테스트 케이스 목록 + 피드백 표시
- 힌트: 모달 팝업 + 마크다운 렌더링 + 레벨 탭

## 제외 (MVP-3 이후)
- bug_fix, refactor 문제 유형
- Docker sandbox
- Tauri/Electron 데스크톱 앱
- private repo 지원
- 여러 언어 네이티브 테스트 (JUnit, Jest)

## 완료 기준
- 문제 설명이 함수 역할, 입출력, 맥락을 포함하여 풍부하게 생성됨
- function_partial 문제 유형이 동작
- LLM 채점 시 테스트 케이스별 결과가 표시됨
- 힌트가 마크다운 형식 팝업으로 표시됨
- 기존 pytest 채점 문제는 변경 없이 동작
