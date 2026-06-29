# REPO_STRUCTURE.md

## 프로젝트 구조
```txt
app/
  backend/
    app/
      main.py
      api/
      core/
      db/
      services/
        repo_analyzer.py
        file_manager.py
        problem_generator.py
        test_matcher.py
        runner.py
  frontend/
    src/
      pages/
        SetupPage.tsx
        AnalyzePage.tsx
        PracticePage.tsx
      components/
      stores/
      api/
  samples/
    python_basic/
      README.md
      pyproject.toml
      src/
        math_utils.py
        string_utils.py
      tests/
        test_math_utils.py
        test_string_utils.py
  docs/
```

## 샘플 repo
모든 테스트 문서는 `samples/python_basic` 기준으로 작성한다.
