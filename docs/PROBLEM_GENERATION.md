# PROBLEM_GENERATION.md

## 방향
AI 연구 논문의 Python repository를 대상으로,
논문의 모델 구조를 단계별로 구현하면서 학습하는 문제를 생성한다.

단순히 "함수를 구현하세요"가 아니라:
- 이 함수가 프로그램에서 무슨 역할인지 설명
- 의존하는 모듈을 먼저 풀도록 순서 강제
- 전체 프로젝트에서의 위치를 알려줌

## 참고 논문
- Paper2Code (PaperCoder, ICLR 2026): 논문→코드 변환의 Planning/Analysis 방법론
- PaperBench (OpenAI): 계층적 rubric 트리 구조, 가중치 기반 채점

## 분석 파이프라인

기존 코드를 이해하는 4단계 분석 (Paper2Code의 역방향 적용):

### Step 1: Project Understanding (프로젝트 전체 이해)
프로젝트당 1회 실행. LLM 1회 호출.

입력:
- README.md (전문)
- 루트 파일 구조 (디렉토리 트리)
- 주요 파일들의 첫 30줄 (import + 클래스 정의)
- requirements.txt 또는 pyproject.toml

출력 (DB에 저장):
```json
{
  "project_summary": "ViTPose는 Vision Transformer 기반 pose estimation 모델이다.",
  "domain": "computer_vision",
  "framework": "pytorch",
  "key_components": ["backbone", "head", "loss", "training"],
  "datasets": ["COCO-Keypoints", "MPII"],
  "main_contribution": "ViT를 pose estimation에 적용"
}
```

### Step 2: Architecture Recovery (파일 관계 분석)
프로젝트당 1회 실행. LLM 1회 호출.

입력:
- 전체 파일 트리
- 각 .py 파일의 import 문 목록 (코드 분석으로 추출, LLM 불필요)
- Step 1의 project_summary

출력 (DB에 저장):
```json
{
  "modules": {
    "model": {
      "description": "모델 정의",
      "files": ["models/backbone.py", "models/head.py", "models/layers/attention.py"]
    },
    "data": {
      "description": "데이터 로딩 및 전처리",
      "files": ["datasets/coco.py", "datasets/transforms.py"]
    },
    "training": {
      "description": "학습 루프",
      "files": ["train.py"]
    },
    "config": {
      "description": "설정 파일 (문제 대상 아님)",
      "files": ["configs/default.py"]
    }
  },
  "file_dependencies": {
    "models/backbone.py": ["models/layers/attention.py", "models/layers/mlp.py"],
    "models/head.py": ["models/backbone.py"],
    "train.py": ["models/backbone.py", "models/head.py", "datasets/coco.py"]
  }
}
```

### Step 3: Dependency Graph (구현 순서 결정)
LLM 불필요. 코드 분석(AST + grep)으로 수행.

입력:
- Step 2의 file_dependencies
- 각 파일의 클래스/함수 목록 (AST)
- 함수 간 호출 관계 (AST + grep)

출력:
```json
{
  "implementation_order": [
    {"file": "models/layers/attention.py", "symbols": ["MultiHeadAttention"], "depth": 0},
    {"file": "models/layers/mlp.py", "symbols": ["MLP"], "depth": 0},
    {"file": "models/backbone.py", "symbols": ["PatchEmbed", "TransformerBlock", "ViTBackbone"], "depth": 1},
    {"file": "models/head.py", "symbols": ["SimpleHead"], "depth": 2},
    {"file": "train.py", "symbols": ["train_step"], "depth": 3}
  ],
  "symbol_dependencies": {
    "TransformerBlock": ["MultiHeadAttention", "MLP"],
    "ViTBackbone": ["PatchEmbed", "TransformerBlock"],
    "train_step": ["ViTBackbone", "SimpleHead", "JointsMSELoss"]
  }
}
```

### Step 4: Function Analysis (함수별 상세 분석)
파일별로 LLM 호출. 배치 처리.

입력:
- 파일 내용
- Step 1~3의 분석 결과 (프로젝트 맥락)

출력 (파일별, DB에 저장):
```json
{
  "source_path": "models/backbone.py",
  "symbols": [
    {
      "name": "PatchEmbed.forward",
      "role": "입력 이미지를 패치 단위로 잘라 임베딩 벡터로 변환",
      "inputs": {"x": "이미지 텐서 [B, 3, H, W]"},
      "outputs": {"return": "패치 임베딩 [B, N, D]"},
      "key_operations": ["Conv2d로 패치 추출", "Flatten + Transpose"],
      "difficulty": "medium",
      "problem_type": "function_blank",
      "depends_on": [],
      "used_by": ["ViTBackbone.forward"]
    }
  ]
}
```

## 문제 트리 구조 (PaperBench 영감)

### 트리 예시
```
프로젝트: ViTPose 구현하기
├── 1. 기본 레이어 (weight: 25%)
│   ├── 문제: MultiHeadAttention.forward (★★☆) — 해금: 없음
│   └── 문제: MLP.forward (★☆☆) — 해금: 없음
├── 2. Backbone (weight: 35%)
│   ├── 문제: PatchEmbed.forward (★★☆) — 해금: 없음
│   ├── 문제: TransformerBlock.forward (★★★) — 해금: MultiHeadAttention + MLP
│   └── 문제: ViTBackbone.forward (★★★) — 해금: PatchEmbed + TransformerBlock
├── 3. Head & Loss (weight: 20%)
│   ├── 문제: SimpleHead.forward (★★☆) — 해금: ViTBackbone
│   └── 문제: JointsMSELoss.forward (★★☆) — 해금: 없음
└── 4. Training (weight: 20%)
    └── 문제: train_step (★★★) — 해금: 모든 모듈
```

### 해금 규칙
- 의존하는 문제가 모두 completed → 자동으로 unlocked
- depth 0인 문제는 처음부터 unlocked
- locked 상태에서는 문제를 볼 수 있지만 제출은 불가

### 점수 계산
- 각 문제: passed=true이면 1, 아니면 0
- 모듈 점수: 하위 문제의 가중 평균
- 전체 점수: 모듈 점수의 가중 평균

## 문제 설명 생성

Step 1~4의 분석 결과를 활용한 마크다운 문제 설명:

```markdown
## TransformerBlock.forward 구현

### 프로젝트 맥락
ViTPose는 Vision Transformer 기반의 pose estimation 모델입니다.
TransformerBlock은 ViT backbone의 핵심 구성 요소로,
입력 특징에 self-attention과 MLP를 순차 적용합니다.

### 이 함수의 역할
models/backbone.py의 ViTBackbone 클래스에서
PatchEmbed 이후 N개의 TransformerBlock을 순차적으로 통과시킵니다.

### 입력
- x (Tensor): 패치 임베딩 [B, N, D]

### 출력
- Tensor: 변환된 특징 [B, N, D] (shape 유지)

### 동작
1. Layer Normalization 적용
2. MultiHeadAttention 수행 (이전 문제에서 구현한 모듈)
3. Residual connection
4. Layer Normalization 적용
5. MLP 수행 (이전 문제에서 구현한 모듈)
6. Residual connection

### 사용 가능한 모듈
- self.norm1, self.norm2: LayerNorm
- self.attn: MultiHeadAttention (이전 문제에서 구현)
- self.mlp: MLP (이전 문제에서 구현)

### 난이도: ★★★ (Hard)
```

## DB 변경

### projects 테이블 추가
```sql
project_summary TEXT          -- Step 1 결과 (JSON)
architecture TEXT             -- Step 2 결과 (JSON)
dependency_graph TEXT         -- Step 3 결과 (JSON)
analysis_status TEXT DEFAULT 'pending'  -- pending | analyzing | completed
```

### problems 테이블 추가
```sql
parent_id TEXT                -- 트리 부모 ID (null이면 루트 모듈)
weight REAL DEFAULT 1.0       -- 가중치
depth INTEGER DEFAULT 0       -- 트리 깊이
unlock_dependencies TEXT      -- 해금 조건 (JSON: 문제 ID 배열)
status TEXT                   -- locked | unlocked | in_progress | completed
```

## 채점 전략

| 조건 | 방식 |
|---|---|
| Python + pytest 있음 | pytest 실행 (가장 정확) |
| Python + pytest 없음 + 외부 패키지 불필요 | LLM 테스트 케이스 생성 + 실제 실행 |
| Python + 외부 패키지 필요 (torch 등) | LLM 채점 (코드 비교, 실행 안 함) |

## 향후 확장
- 데이터셋 RAG: 주요 벤치마크 데이터셋 지식 DB
- 논문 기반 모드: PDF 입력 → 코드 없이 문제 생성
- 다른 언어: JS/Java 등 (LLM 분석 + LLM 채점)
