import Editor from "@monaco-editor/react";
import axios from "axios";
import type React from "react";
import { useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import { useParams } from "react-router-dom";

import { apiClient } from "../api/client";
import {
  Candidate,
  CurrentProject,
  CandidateApiItem,
  Module,
  Problem,
  ProblemDetail,
  normalizeCandidate,
  SubmitResult,
  normalizeProblemDetail,
  useProjectStore,
} from "../stores/useProjectStore";

type HintResponse = {
  level: number;
  hint: string;
  format: "markdown" | "text";
};

type ApiErrorResponse = {
  error_code: string;
  message: string;
};

type HintItem = {
  level: number;
  hint: string;
  format: "markdown" | "text";
};

type ProblemGenerateResponse = {
  problems: Array<{
    problem_id: string;
    file_id: string;
    source_path: string;
    target_symbol: string;
    problem_type: "function_blank" | "function_partial";
    test_path: string | null;
    grading_method: "pytest" | "llm";
    difficulty: "easy" | "medium" | "hard" | null;
  }>;
};

type WarmupQuestion = {
  id: number;
  question: string;
  options: string[];
  answer: number;
  explanation: string;
};

type WarmupResponse = {
  questions: WarmupQuestion[];
};

type AssessStatus = {
  status: "pending" | "running" | "completed" | string;
  total: number;
  assessed: number;
  suitable: number;
  progress: number;
  deferred?: Array<{
    source_path: string;
    reason: string;
  }>;
  candidates?: CandidateApiItem[];
};

type ArchitectureFile = {
  path: string;
  description?: string;
  classes?: string[];
  functions?: string[];
  depends_on?: string[];
};

type ProjectArchitectureResponse = {
  id: string;
  repo_path: string;
  practice_root_path: string;
  target_extensions: string[];
  paper_source?: "arxiv" | "pdf" | null;
  paper_title?: string | null;
  paper_abstract?: string | null;
  paper_metadata?: {
    figure_count?: number;
  };
  project_summary?: {
    project_summary?: string;
    summary?: string;
    paper_summary?: string;
    architecture_flow?: string[] | string;
    architecture_figure?: number | string | null;
    components?: Array<{
      name?: string;
      related_figure?: number | string | null;
    }>;
  };
  architecture?: {
    architecture_flow?: string;
    files?: ArchitectureFile[];
  };
};

type ArchitectureFlowItem = {
  label: string;
  path: string | null;
  description: string | null;
};

const STATUS_ICON: Record<Problem["status"], string> = {
  active: "⏳",
  locked: "🔒",
  unlocked: "🔓",
  passed: "✅",
  failed: "!",
};

const CANDIDATE_STATUS_ICON: Record<Candidate["status"], string> = {
  ...STATUS_ICON,
  skipped: "!",
  error: "!",
};

const GRADING_ICON: Record<Problem["grading_method"], string> = {
  pytest: "🧪",
  llm: "🤖",
};

export function PracticePage() {
  const { id } = useParams();

  const currentProject = useProjectStore((state) => state.currentProject);
  const problems = useProjectStore((state) => state.problems);
  const candidates = useProjectStore((state) => state.candidates);
  const modules = useProjectStore((state) => state.modules);
  const overallProgress = useProjectStore((state) => state.overallProgress);
  const currentProblem = useProjectStore((state) => state.currentProblem);
  const editorCode = useProjectStore((state) => state.editorCode);
  const submitResult = useProjectStore((state) => state.submitResult);
  const refreshProblems = useProjectStore((state) => state.refreshProblems);
  const preparePractice = useProjectStore((state) => state.preparePractice);
  const setCandidates = useProjectStore((state) => state.setCandidates);
  const setCurrentProject = useProjectStore((state) => state.setCurrentProject);
  const setCurrentProblem = useProjectStore((state) => state.setCurrentProblem);
  const setEditorCode = useProjectStore((state) => state.setEditorCode);
  const setSubmitResult = useProjectStore((state) => state.setSubmitResult);

  const [selectedProblemId, setSelectedProblemId] = useState<string | null>(null);
  const [selectedCandidateKey, setSelectedCandidateKey] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isGeneratingProblem, setIsGeneratingProblem] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [isHintLoading, setIsHintLoading] = useState(false);
  const [errorMessage, setErrorMessage] = useState("");
  const [hints, setHints] = useState<HintItem[]>([]);
  const [isHintModalOpen, setIsHintModalOpen] = useState(false);
  const successfulCodeByProblemId = useRef<Record<string, string>>({});
  const [isFileMenuOpen, setIsFileMenuOpen] = useState(false);
  const [toastMessage, setToastMessage] = useState("");
  const [invalidCandidateKeys, setInvalidCandidateKeys] = useState<Set<string>>(new Set());
  const [warmupQuestions, setWarmupQuestions] = useState<WarmupQuestion[]>([]);
  const [warmupSelections, setWarmupSelections] = useState<Record<number, number>>({});
  const [revealedWarmups, setRevealedWarmups] = useState<Set<number>>(new Set());
  const [assessProgress, setAssessProgress] = useState<AssessStatus | null>(null);
  const [architectureFlow, setArchitectureFlow] = useState<ArchitectureFlowItem[]>([]);
  const [paperFigureCount, setPaperFigureCount] = useState(0);
  const [paperSummaryText, setPaperSummaryText] = useState("");

  const projectTitle = useMemo(() => projectDisplayName(currentProject, id), [currentProject, id]);
  const selectedCandidate = useMemo(
    () => candidates.find((candidate) => candidateKey(candidate) === selectedCandidateKey) ?? null,
    [candidates, selectedCandidateKey],
  );
  const activeArchitectureTarget = useMemo(
    () => ({
      sourcePath: currentProblem?.source_path ?? selectedCandidate?.sourcePath ?? null,
      symbol: currentProblem?.target_symbol ?? selectedCandidate?.symbol ?? null,
    }),
    [currentProblem?.source_path, currentProblem?.target_symbol, selectedCandidate?.sourcePath, selectedCandidate?.symbol],
  );
  const dependencyProblems = useMemo(
    () =>
      currentProblem?.unlockDependencies.map((dependencyId) => problems.find((problem) => problem.id === dependencyId)).filter(Boolean) ??
      [],
    [currentProblem?.unlockDependencies, problems],
  );

  useEffect(() => {
    let ignore = false;

    let intervalId: number | undefined;

    async function loadWarmup() {
      try {
        const response = await apiClient.post<WarmupResponse>(`/projects/${id}/warmup`);
        if (!ignore) {
          setWarmupQuestions(response.data.questions);
        }
      } catch {
        if (!ignore) {
          setWarmupQuestions([]);
        }
      }
    }

    async function refreshAssessStatus(showToast: boolean) {
      if (!id) {
        return;
      }
      const projectId = id;
      const statusResponse = await apiClient.get<AssessStatus>(`/projects/${projectId}/assess/status`);
      if (ignore) {
        return;
      }
      console.log("[PracticePage] assess/status 폴링 결과", statusResponse.data);
      setAssessProgress(statusResponse.data);
      const nextCandidates = (statusResponse.data.candidates ?? []).map(normalizeCandidate);
      const previousCount = useProjectStore.getState().candidates.length;
      console.log(`[poll] received ${nextCandidates.length} candidates, prev was ${previousCount}`);
      setCandidates(nextCandidates);

      if (statusResponse.data.status === "completed") {
        if (intervalId !== undefined) {
          window.clearInterval(intervalId);
        }
        const loadedCandidates =
          statusResponse.data.candidates?.map(normalizeCandidate) ?? (await preparePractice(projectId));
        await refreshProblems(projectId);
        if (showToast && loadedCandidates.length > 0 && !ignore) {
          setToastMessage(`프로젝트 분석 완료! ${loadedCandidates.length}개 문제가 준비되었습니다.`);
          window.setTimeout(() => setToastMessage(""), 4000);
        }
      }
    }

    async function loadPracticeShell() {
      if (!id) {
        setErrorMessage("프로젝트를 찾을 수 없습니다");
        setIsLoading(false);
        return;
      }

      setIsLoading(true);
      setErrorMessage("");
      setCurrentProblem(null);
      setEditorCode("");
      setSubmitResult(null);

      try {
        void loadWarmup();
        const projectDetail = await apiClient.get<ProjectArchitectureResponse>(`/projects/${id}`);
        if (!ignore) {
          setCurrentProject({
            id: projectDetail.data.id,
            repo_path: projectDetail.data.repo_path,
            practice_root_path: projectDetail.data.practice_root_path,
            target_extensions: projectDetail.data.target_extensions,
            paper_source: projectDetail.data.paper_source,
            paper_title: projectDetail.data.paper_title,
            paper_abstract: projectDetail.data.paper_abstract,
          });
        }
        if (!ignore && projectDetail.data.paper_source) {
          setArchitectureFlow(
            buildArchitectureFlow(projectDetail.data.project_summary ?? {}, projectDetail.data.architecture ?? {}, projectDetail.data.paper_title ?? projectTitle),
          );
          setPaperFigureCount(projectDetail.data.paper_metadata?.figure_count ?? 0);
          setPaperSummaryText(projectDetail.data.project_summary?.paper_summary ?? projectDetail.data.project_summary?.summary ?? projectDetail.data.project_summary?.project_summary ?? projectDetail.data.paper_abstract ?? "");
        }
        const loadedCandidates = await preparePractice(id);
        const loadedProblems = await refreshProblems(id);
        if (ignore) {
          return;
        }

        const firstProblem = loadedProblems.find((problem) => problem.status !== "locked") ?? loadedProblems[0];
        const firstCandidate = loadedCandidates.find((candidate) => candidate.status !== "locked") ?? loadedCandidates[0];
        setSelectedProblemId(firstProblem?.id ?? null);
        setSelectedCandidateKey(firstProblem ? null : firstCandidate ? candidateKey(firstCandidate) : null);

        if (!firstProblem) {
          setCurrentProblem(null);
          setEditorCode("");
          setSubmitResult(null);
        }

        const initialStatus = await apiClient.get<AssessStatus>(`/projects/${id}/assess/status`);
        if (ignore) {
          return;
        }
        setAssessProgress(initialStatus.data);
        console.log("[PracticePage] assess/status 폴링 결과", initialStatus.data);
        const initialCandidates = (initialStatus.data.candidates ?? []).map(normalizeCandidate);
        console.log(`[poll] received ${initialCandidates.length} candidates, prev was ${useProjectStore.getState().candidates.length}`);
        setCandidates(initialCandidates);

        if (initialStatus.data.status === "completed") {
          if (!initialStatus.data.candidates) {
            await preparePractice(id);
          }
        } else {
          if (initialStatus.data.status === "pending" || initialStatus.data.status.startsWith("error")) {
            console.log("[PracticePage] assess/start 호출");
            await apiClient.post(`/projects/${id}/assess/start`);
          }
          intervalId = window.setInterval(() => {
            void refreshAssessStatus(true);
          }, 5000);
          void refreshAssessStatus(false);
        }
      } catch (error) {
        if (!ignore) {
          setErrorMessage(getErrorMessage(error));
        }
      } finally {
        if (!ignore) {
          setIsLoading(false);
        }
      }
    }

    loadPracticeShell();

    return () => {
      ignore = true;
      if (intervalId !== undefined) {
        window.clearInterval(intervalId);
      }
    };
  }, [id, preparePractice, refreshProblems, setCandidates, setCurrentProject, setCurrentProblem, setEditorCode, setSubmitResult]);

  useEffect(() => {
    let ignore = false;

    async function loadProblemDetail() {
      setHints([]);
      setIsHintModalOpen(false);

      if (!selectedProblemId) {
        return;
      }

      setErrorMessage("");
      setSubmitResult(null);

      try {
        const response = await apiClient.get<ProblemDetail>(`/problems/${selectedProblemId}`);
        if (ignore) {
          return;
        }

        const problem = normalizeProblemDetail(response.data);
        setCurrentProblem(problem);
        setEditorCode(successfulCodeByProblemId.current[selectedProblemId] ?? problem.starter_code);
      } catch (error) {
        if (!ignore) {
          setErrorMessage(getErrorMessage(error));
        }
      }
    }

    loadProblemDetail();

    return () => {
      ignore = true;
    };
  }, [selectedProblemId, setCurrentProblem, setEditorCode, setSubmitResult]);

  async function handleSubmit(overwrite: boolean) {
    if (!currentProblem || currentProblem.status === "locked" || !id) {
      return;
    }

    setIsSubmitting(true);
    setErrorMessage("");
    const beforeProblems = problems;

    try {
      const response = await apiClient.post<SubmitResult>(`/problems/${currentProblem.id}/submit`, {
        code: editorCode,
        overwrite,
      });

      setSubmitResult(response.data);

      if (response.data.passed) {
        successfulCodeByProblemId.current[currentProblem.id] = editorCode;
        setCurrentProblem({ ...currentProblem, status: "passed" });
        const refreshed = await refreshProblems(id);
        await preparePractice(id);
        const beforeById = new Map(beforeProblems.map((problem) => [problem.id, problem]));
        const unlocked = refreshed.find(
          (problem) => beforeById.get(problem.id)?.status === "locked" && problem.status === "unlocked",
        );
        if (unlocked) {
          setToastMessage(`🔓 ${unlocked.target_symbol}가 해금되었습니다!`);
          window.setTimeout(() => setToastMessage(""), 4000);
        }
      } else {
        setCurrentProblem({ ...currentProblem, status: "failed" });
        await refreshProblems(id);
        await preparePractice(id);
      }
    } catch (error) {
      setErrorMessage(getErrorMessage(error));
    } finally {
      setIsSubmitting(false);
    }
  }

  async function handleHint(level: number) {
    if (!currentProblem || hints.some((hint) => hint.level === level)) {
      return;
    }

    setIsHintLoading(true);
    setErrorMessage("");

    try {
      const response = await apiClient.post<HintResponse>(`/problems/${currentProblem.id}/hint`, { level });
      setHints((currentHints) => [...currentHints, response.data].sort((left, right) => left.level - right.level));
    } catch (error) {
      setErrorMessage(getErrorMessage(error));
    } finally {
      setIsHintLoading(false);
    }
  }

  async function handleRetryDeferred(sourcePath: string) {
    if (!id) {
      return;
    }
    setErrorMessage("");
    try {
      await apiClient.post(`/projects/${id}/assess`, {
        source_paths: [sourcePath],
        force: true,
      });
      const statusResponse = await apiClient.get<AssessStatus>(`/projects/${id}/assess/status`);
      setAssessProgress(statusResponse.data);
      if (statusResponse.data.status === "completed") {
        await preparePractice(id);
        await refreshProblems(id);
      }
    } catch (error) {
      setErrorMessage(getErrorMessage(error, "재분석에 실패했습니다"));
    }
  }

  function handleSelectProblem(problemId: string) {
    setSelectedProblemId(problemId);
    setSelectedCandidateKey(null);
    setIsFileMenuOpen(false);
  }

  async function handleSelectCandidate(candidate: Candidate) {
    setSelectedCandidateKey(candidateKey(candidate));
    setSelectedProblemId(null);
    setSubmitResult(null);
    setHints([]);
    setIsHintModalOpen(false);
    setIsFileMenuOpen(false);

    if (candidate.status === "locked") {
      setCurrentProblem(null);
      setEditorCode("");
      return;
    }

    if (candidate.status === "skipped" || candidate.status === "error" || invalidCandidateKeys.has(candidateKey(candidate))) {
      setErrorMessage("이 함수를 찾을 수 없습니다");
      return;
    }

    if (candidate.generated && candidate.problemId) {
      handleSelectProblem(candidate.problemId);
      return;
    }

    if (!id) {
      return;
    }

    setIsGeneratingProblem(true);
    setErrorMessage("");
    try {
      const generate = await apiClient.post<ProblemGenerateResponse>(`/projects/${id}/problems/generate`, {
        source_path: candidate.sourcePath,
        target_symbols: [
          {
            symbol: candidate.symbol,
            difficulty: candidate.difficulty,
            problem_type: candidate.problemType,
            role_in_project: candidate.roleInProject,
            depends_on: candidate.dependsOn,
            used_by: candidate.usedBy,
          },
        ],
      });
      const generated = generate.data.problems[0];
      await preparePractice(id);
      await refreshProblems(id);
      setSelectedProblemId(generated.problem_id);
      setSelectedCandidateKey(null);
    } catch (error) {
      if (axios.isAxiosError<ApiErrorResponse>(error) && error.response?.data?.error_code === "SYMBOL_NOT_FOUND") {
        setInvalidCandidateKeys((current) => new Set([...current, candidateKey(candidate)]));
        await preparePractice(id);
        await refreshProblems(id);
      }
      setErrorMessage(getErrorMessage(error, "문제 생성에 실패했습니다"));
    } finally {
      setIsGeneratingProblem(false);
    }
  }

  function handleReset() {
    if (currentProblem) {
      setEditorCode(currentProblem.starter_code);
      setSubmitResult(null);
    }
  }

  function handleOpenHintModal() {
    setIsHintModalOpen(true);
  }

  if (isLoading) {
    return <main className="practice-page">문제 목록을 불러오는 중...</main>;
  }

  return (
    <main className="practice-page">
      <button className="file-menu-toggle" type="button" onClick={() => setIsFileMenuOpen((open) => !open)}>
        문제 트리
      </button>

      <aside className={isFileMenuOpen ? "problem-sidebar open" : "problem-sidebar"}>
        <ProgressBar title={projectTitle} progress={overallProgress} />
        {assessProgress ? <AssessProgressPanel progress={assessProgress} onRetry={handleRetryDeferred} /> : null}
        {paperSummaryText || (paperFigureCount > 0 && id) ? (
          <PaperExplanationPanel summary={paperSummaryText} projectId={id ?? ""} figureCount={paperFigureCount} />
        ) : null}
        {problems.length === 0 && candidates.length === 0 ? (
          <p className="empty-state">분석이 진행되는 동안 몸풀기 문제를 먼저 풀어보세요</p>
        ) : (
          <ProblemTree
            modules={modules}
            problems={problems}
            candidates={candidates}
            isAssessRunning={assessProgress?.status === "running"}
            invalidCandidateKeys={invalidCandidateKeys}
            selectedProblemId={selectedProblemId}
            selectedCandidateKey={selectedCandidateKey}
            onSelectProblem={handleSelectProblem}
            onSelectCandidate={handleSelectCandidate}
          />
        )}
      </aside>

      <section className="practice-main">
        {errorMessage ? (
          <p className="error-message" role="alert">
            {errorMessage}
          </p>
        ) : null}

        {isGeneratingProblem ? (
          <p className="status-text">문제 생성 중...</p>
        ) : selectedCandidate?.status === "locked" ? (
          <LockedCandidatePanel candidate={selectedCandidate} candidates={candidates} onSelectCandidate={handleSelectCandidate} />
        ) : currentProblem ? (
          <>
            <section className="problem-prompt">
              <div className="problem-labels">
                <span>{currentProblem.source_path}</span>
                <strong>{currentProblem.target_symbol}</strong>
                {currentProblem.status === "passed" ? <em className="completed-badge">✅ 완료됨</em> : null}
                <em>{currentProblem.problem_type === "function_partial" ? "부분 구현" : "전체 구현"}</em>
                <em>{currentProblem.grading_method === "pytest" ? "pytest 채점" : "LLM 채점"}</em>
                {currentProblem.difficulty ? <em>{currentProblem.difficulty}</em> : null}
              </div>
              {currentProblem.roleInProject ? (
                <p className="role-callout">📍 역할: {currentProblem.roleInProject}</p>
              ) : null}
              {currentProblem.status === "locked" ? (
                <LockedProblemNotice dependencies={dependencyProblems as Problem[]} onSelectProblem={handleSelectProblem} />
              ) : null}
              <div className="markdown-content">
                <ReactMarkdown>{currentProblem.prompt}</ReactMarkdown>
              </div>
            </section>

            <section className="editor-panel">
              <Editor
                height="52vh"
                language="python"
                value={editorCode}
                onChange={(value) => {
                  if (currentProblem.status !== "locked") {
                    setEditorCode(value ?? "");
                  }
                }}
                options={{
                  minimap: { enabled: false },
                  fontSize: 14,
                  tabSize: 4,
                  wordWrap: "on",
                  scrollBeyondLastLine: false,
                  readOnly: currentProblem.status === "locked",
                }}
              />
            </section>

            <div className="editor-actions">
              <button type="button" onClick={() => handleSubmit(false)} disabled={isSubmitting || currentProblem.status === "locked"}>
                {isSubmitting ? "채점 중..." : "제출"}
              </button>
              <button type="button" className="secondary" onClick={handleReset} disabled={isSubmitting || currentProblem.status === "locked"}>
                초기화
              </button>
              <button type="button" className="secondary" onClick={handleOpenHintModal}>
                💡 힌트
              </button>
            </div>

            <div className="mobile-result">
              <ResultPanel
                result={submitResult}
                isSubmitting={isSubmitting}
                onOverwrite={() => handleSubmit(true)}
                canOverwrite={Boolean(currentProblem)}
              />
            </div>
          </>
        ) : (
          <>
            {architectureFlow.length > 0 ? (
              <ArchitectureFlowPanel title={projectTitle} items={architectureFlow} activeTarget={activeArchitectureTarget} />
            ) : null}
            <WarmupPanel
              title={projectTitle}
              questions={warmupQuestions}
              selections={warmupSelections}
              revealed={revealedWarmups}
              onSelect={(questionId, optionIndex) =>
                setWarmupSelections((current) => ({ ...current, [questionId]: optionIndex }))
              }
              onReveal={(questionId) => setRevealedWarmups((current) => new Set([...current, questionId]))}
            />
          </>
        )}
      </section>

      <aside className="result-sidebar">
        <ResultPanel
          result={submitResult}
          isSubmitting={isSubmitting}
          onOverwrite={() => handleSubmit(true)}
          canOverwrite={Boolean(currentProblem)}
        />
      </aside>

      {currentProblem && isHintModalOpen ? (
        <HintModal
          hints={hints}
          isLoading={isHintLoading}
          onHint={handleHint}
          onClose={() => setIsHintModalOpen(false)}
        />
      ) : null}
      {toastMessage ? <div className="unlock-toast">{toastMessage}</div> : null}
    </main>
  );
}

function AssessProgressPanel({ progress, onRetry }: { progress: AssessStatus; onRetry: (sourcePath: string) => void }) {
  const percent = Math.round(progress.progress * 100);
  const isCompleted = progress.status === "completed";
  const label = isCompleted
    ? `분석 완료 (${progress.suitable}개 적합)`
    : `분석 중... (${progress.assessed}/${progress.total} 파일)`;
  return (
    <section className="assess-progress-panel">
      <div className="assess-progress-header">
        <strong>{label}</strong>
        <span>{percent}%</span>
      </div>
      <div className="progress-track" aria-label={`파일 분석 진행도 ${percent}%`}>
        <div className="progress-fill" style={{ width: `${Math.min(100, Math.max(0, percent))}%` }} />
      </div>
      {progress.deferred?.length ? (
        <ul className="deferred-files">
          {progress.deferred.slice(0, 5).map((item) => (
            <li key={item.source_path}>
              <span>분석 보류: {item.source_path}</span>
              <small>{item.reason}</small>
              <button type="button" onClick={() => onRetry(item.source_path)}>
                재분석
              </button>
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}

function PaperExplanationPanel({ summary, projectId, figureCount }: { summary: string; projectId: string; figureCount: number }) {
  const [isOpen, setIsOpen] = useState(true);
  return (
    <section className="paper-explanation-panel">
      <button className="paper-explanation-toggle" type="button" onClick={() => setIsOpen((open) => !open)}>
        <strong>📖 논문 설명</strong>
        <span>{isOpen ? "접기 ▲" : "펼치기 ▼"}</span>
      </button>
      {isOpen ? (
        <div className="paper-explanation-body">
          {summary ? <p>{summary}</p> : null}
          {figureCount > 0 ? <PaperFigurePanel projectId={projectId} variant="sidebar" /> : null}
        </div>
      ) : null}
    </section>
  );
}

function PaperFigurePanel({ projectId, variant = "problem" }: { projectId: string; variant?: "sidebar" | "problem" }) {
  return (
    <section className={variant === "sidebar" ? "paper-figure-panel sidebar" : "paper-figure-panel problem"}>
      <div className="paper-figure-header">
        <h2>{variant === "sidebar" ? "아키텍처 Figure" : "📄 관련 Figure"}</h2>
      </div>
      <img src={`http://localhost:8000/api/papers/${projectId}/figures/0`} alt="논문에서 추출한 figure" loading="lazy" />
    </section>
  );
}

function ArchitectureFlowPanel({
  title,
  items,
  activeTarget,
}: {
  title: string;
  items: ArchitectureFlowItem[];
  activeTarget: { sourcePath: string | null; symbol: string | null };
}) {
  const displayTitle = stripPracticeSuffix(title);
  return (
    <section className="architecture-flow-panel">
      <div className="architecture-flow-header">
        <h2>📐 {displayTitle} 아키텍처</h2>
      </div>
      <ol className="architecture-flow-list">
        {items.map((item, index) => {
          const isActive = isArchitectureNodeActive(item, activeTarget);
          return (
            <li key={`${item.label}:${item.path ?? index}`} className={isActive ? "active" : ""}>
              <div className="architecture-flow-node">
                <strong>{item.label}</strong>
                {item.path ? <span>← {item.path}</span> : null}
                {isActive ? <em>지금 여기! ★</em> : null}
              </div>
              {index < items.length - 1 ? <div className="architecture-flow-arrow">↓</div> : null}
            </li>
          );
        })}
      </ol>
    </section>
  );
}

function WarmupPanel({
  title,
  questions,
  selections,
  revealed,
  onSelect,
  onReveal,
}: {
  title: string;
  questions: WarmupQuestion[];
  selections: Record<number, number>;
  revealed: Set<number>;
  onSelect: (questionId: number, optionIndex: number) => void;
  onReveal: (questionId: number) => void;
}) {
  const solvedCount = questions.filter((question) => revealed.has(question.id)).length;
  return (
    <section className="warmup-panel">
      <div className="warmup-intro">
        <h2>{title}</h2>
        <p>프로젝트 분석이 끝나는 동안 핵심 개념을 가볍게 점검해보세요.</p>
      </div>

      <div className="warmup-header">
        <h3>몸풀기 퀴즈</h3>
        {questions.length > 0 ? (
          <span>
            {solvedCount}/{questions.length}
          </span>
        ) : null}
      </div>

      {questions.length === 0 ? (
        <p className="empty-state">몸풀기 문제를 준비하는 중...</p>
      ) : (
        <ul className="warmup-list">
          {questions.map((question) => {
            const selected = selections[question.id];
            const isRevealed = revealed.has(question.id);
            const isCorrect = selected === question.answer;
            return (
              <li key={question.id} className="warmup-card">
                <strong>
                  Q{question.id + 1}. {question.question}
                </strong>
                <div className="warmup-options">
                  {question.options.map((option, index) => (
                    <label
                      key={`${question.id}:${option}`}
                      className={
                        isRevealed && index === question.answer
                          ? "correct"
                          : isRevealed && selected === index
                            ? "incorrect"
                            : ""
                      }
                    >
                      <input
                        type="radio"
                        name={`warmup-${question.id}`}
                        checked={selected === index}
                        disabled={isRevealed}
                        onChange={() => onSelect(question.id, index)}
                      />
                      <span>{option}</span>
                    </label>
                  ))}
                </div>
                <button type="button" onClick={() => onReveal(question.id)} disabled={selected === undefined || isRevealed}>
                  정답 확인
                </button>
                {isRevealed ? (
                  <p className={isCorrect ? "warmup-feedback correct" : "warmup-feedback incorrect"}>
                    {isCorrect ? "정답입니다. " : "다시 짚어볼 부분입니다. "}
                    {question.explanation}
                  </p>
                ) : null}
              </li>
            );
          })}
        </ul>
      )}

      {questions.length > 0 && solvedCount === questions.length ? (
        <p className="warmup-complete">잘 하셨습니다! 왼쪽에 문제가 나타나면 본 문제를 선택해 시작하세요.</p>
      ) : null}
    </section>
  );
}

function ProgressBar({ title, progress }: { title: string; progress: number }) {
  const percent = Math.round(progress * 100);
  return (
    <div className="project-progress">
      <div className="project-progress-header">
        <h1>{title}</h1>
        <strong>{percent}%</strong>
      </div>
      <div className="progress-track" aria-label={`전체 진행도 ${percent}%`}>
        <div className="progress-fill" style={{ width: `${Math.min(100, Math.max(0, percent))}%` }} />
      </div>
    </div>
  );
}

function ProblemTree({
  modules,
  problems,
  candidates,
  isAssessRunning,
  invalidCandidateKeys,
  selectedProblemId,
  selectedCandidateKey,
  onSelectProblem,
  onSelectCandidate,
}: {
  modules: Module[];
  problems: Problem[];
  candidates: Candidate[];
  isAssessRunning: boolean;
  invalidCandidateKeys: Set<string>;
  selectedProblemId: string | null;
  selectedCandidateKey: string | null;
  onSelectProblem: (problemId: string) => void;
  onSelectCandidate: (candidate: Candidate) => void;
}) {
  const groups = groupTreeItemsByModule(modules, problems, candidates, isAssessRunning);
  console.log(`[tree] rendering ${candidates.length} candidates`);
  return (
    <ul className="problem-tree">
      {groups.map((group) => (
        <ModuleGroup
          key={group.id}
          group={group}
          selectedProblemId={selectedProblemId}
          selectedCandidateKey={selectedCandidateKey}
          invalidCandidateKeys={invalidCandidateKeys}
          onSelectProblem={onSelectProblem}
          onSelectCandidate={onSelectCandidate}
        />
      ))}
    </ul>
  );
}

function ModuleGroup({
  group,
  selectedProblemId,
  selectedCandidateKey,
  invalidCandidateKeys,
  onSelectProblem,
  onSelectCandidate,
}: {
  group: ProblemModuleGroup;
  selectedProblemId: string | null;
  selectedCandidateKey: string | null;
  invalidCandidateKeys: Set<string>;
  onSelectProblem: (problemId: string) => void;
  onSelectCandidate: (candidate: Candidate) => void;
}) {
  const [isOpen, setIsOpen] = useState(true);
  return (
    <li className="module-group">
      <button className="module-group-toggle" type="button" onClick={() => setIsOpen((open) => !open)}>
        <span>{isOpen ? "▾" : "▸"}</span>
        <strong>📁 {group.title}</strong>
        <small>
          ({group.passedCount}/{group.problemCount})
        </small>
      </button>
      {isOpen ? (
        <ul className="module-problems">
          {group.items.length > 0 ? (
            group.items.map((item) => (
              <TreeNode
                key={item.key}
                item={item}
                isSelected={item.kind === "problem" ? item.problem.id === selectedProblemId : item.key === selectedCandidateKey}
                isInvalid={item.kind === "candidate" && invalidCandidateKeys.has(item.key)}
                onSelectProblem={onSelectProblem}
                onSelectCandidate={onSelectCandidate}
              />
            ))
          ) : group.isAnalyzing ? (
            <li className="module-empty">분석 중...</li>
          ) : null}
        </ul>
      ) : null}
    </li>
  );
}

function TreeNode({
  item,
  isSelected,
  isInvalid,
  onSelectProblem,
  onSelectCandidate,
}: {
  item: TreeItem;
  isSelected: boolean;
  isInvalid: boolean;
  onSelectProblem: (problemId: string) => void;
  onSelectCandidate: (candidate: Candidate) => void;
}) {
  const status = item.kind === "problem" ? item.problem.status : item.candidate.status;
  const symbol = item.kind === "problem" ? item.problem.target_symbol : item.candidate.symbol;
  const difficulty = item.kind === "problem" ? item.problem.difficulty : item.candidate.difficulty;
  const gradingMethod = item.kind === "problem" ? item.problem.grading_method : "llm";
  const icon = item.kind === "problem" ? STATUS_ICON[item.problem.status] : CANDIDATE_STATUS_ICON[item.candidate.status];
  return (
    <li>
      <button
        className={isSelected ? `problem-node selected ${status}` : `problem-node ${status}${item.kind === "candidate" ? " not-generated" : ""}`}
        type="button"
        disabled={isInvalid || status === "skipped" || status === "error"}
        onClick={() => (item.kind === "problem" ? onSelectProblem(item.problem.id) : onSelectCandidate(item.candidate))}
      >
        <span className={`status-icon ${status}`}>{icon}</span>
        <span className="problem-symbol">
          {isInvalid || status === "skipped" || status === "error" ? `${shortSymbol(symbol)} (찾을 수 없음)` : shortSymbol(symbol)}
        </span>
        <span className="difficulty-stars">{difficultyStars(difficulty)}</span>
        <span className="grading-icon" title={gradingMethod}>
          {GRADING_ICON[gradingMethod]}
        </span>
      </button>
    </li>
  );
}

function LockedCandidatePanel({
  candidate,
  candidates,
  onSelectCandidate,
}: {
  candidate: Candidate;
  candidates: Candidate[];
  onSelectCandidate: (candidate: Candidate) => void;
}) {
  const dependencies = candidates.filter((item) => candidate.dependsOn.some((dependency) => dependency === item.symbol || dependency.endsWith(item.symbol)));
  return (
    <section className="problem-prompt">
      <div className="problem-labels">
        <span>{candidate.sourcePath}</span>
        <strong>{candidate.symbol}</strong>
        <em>{candidate.problemType === "function_partial" ? "부분 구현" : "전체 구현"}</em>
        <em>{candidate.difficulty}</em>
      </div>
      {candidate.roleInProject ? <p className="role-callout">📍 역할: {candidate.roleInProject}</p> : null}
      <div className="locked-notice">
        <strong>🔒 이 문제는 아직 잠겨있습니다.</strong>
        <p>먼저 다음 문제를 완료하세요:</p>
        {dependencies.length > 0 ? (
          <ul>
            {dependencies.map((dependency) => (
              <li key={candidateKey(dependency)}>
                <button type="button" onClick={() => onSelectCandidate(dependency)}>
                  {dependency.symbol}
                </button>
              </li>
            ))}
          </ul>
        ) : (
          <p>{candidate.dependsOn.join(", ") || "의존 문제 정보가 없습니다."}</p>
        )}
      </div>
    </section>
  );
}

function LockedProblemNotice({
  dependencies,
  onSelectProblem,
}: {
  dependencies: Problem[];
  onSelectProblem: (problemId: string) => void;
}) {
  return (
    <div className="locked-notice">
      <strong>🔒 이 문제는 아직 잠겨있습니다.</strong>
      <p>먼저 다음 문제를 완료하세요:</p>
      {dependencies.length > 0 ? (
        <ul>
          {dependencies.map((problem) => (
            <li key={problem.id}>
              <button type="button" onClick={() => onSelectProblem(problem.id)}>
                {problem.target_symbol}
              </button>
            </li>
          ))}
        </ul>
      ) : (
        <p>의존 문제 정보가 없습니다.</p>
      )}
    </div>
  );
}

type ProblemModuleGroup = {
  id: string;
  title: string;
  problemCount: number;
  passedCount: number;
  items: TreeItem[];
  isAnalyzing: boolean;
};

type TreeItem =
  | { kind: "problem"; key: string; parentId: string | null; problem: Problem }
  | { kind: "candidate"; key: string; parentId: string | null; candidate: Candidate };

function groupTreeItemsByModule(
  modules: Module[],
  problems: Problem[],
  candidates: Candidate[],
  isAssessRunning: boolean,
): ProblemModuleGroup[] {
  const generatedKeys = new Set(problems.map((problem) => `${problem.source_path}:${problem.target_symbol}`));
  const problemItems: TreeItem[] = problems.map((problem) => ({
    kind: "problem",
    key: problem.id,
    parentId: problem.parentId,
    problem,
  }));
  const candidateItems: TreeItem[] = candidates
    .filter((candidate) => !generatedKeys.has(`${candidate.sourcePath}:${candidate.symbol}`))
    .map((candidate) => ({
      kind: "candidate",
      key: candidateKey(candidate),
      parentId: candidate.parentId,
      candidate,
    }));
  const items = [...problemItems, ...candidateItems].sort((left, right) => itemDepth(left) - itemDepth(right) || itemSymbol(left).localeCompare(itemSymbol(right)));

  if (modules.length === 0) {
    return groupItemsBySourcePath(items);
  }
  const uniqueModules = deduplicateModulesByTitle(modules);
  const knownModuleIds = new Set(modules.map((module) => module.id));
  const groups = uniqueModules
    .map((module) => {
      const groupedItems = items.filter((item) => item.parentId !== null && module.ids.has(item.parentId));
      return {
        id: module.id,
        title: module.title,
        problemCount: groupedItems.length,
        passedCount: groupedItems.filter((item) => itemStatus(item) === "passed").length,
        items: groupedItems,
        isAnalyzing: isAssessRunning && groupedItems.length === 0,
      };
    })
    .filter((group) => group.items.length > 0 || group.isAnalyzing);
  const orphanItems = items.filter((item) => !item.parentId || !knownModuleIds.has(item.parentId));
  if (orphanItems.length > 0) {
    groups.push({
      id: "misc",
      title: "기타",
      problemCount: orphanItems.length,
      passedCount: orphanItems.filter((item) => itemStatus(item) === "passed").length,
      items: orphanItems,
      isAnalyzing: false,
    });
  }
  return groups;
}

function groupItemsBySourcePath(items: TreeItem[]): ProblemModuleGroup[] {
  const groups = new Map<string, TreeItem[]>();
  for (const item of items) {
    const path = item.kind === "problem" ? item.problem.source_path : item.candidate.sourcePath;
    groups.set(path, [...(groups.get(path) ?? []), item]);
  }
  return [...groups.entries()].map(([sourcePath, grouped]) => ({
    id: sourcePath,
    title: sourcePath,
    problemCount: grouped.length,
    passedCount: grouped.filter((item) => itemStatus(item) === "passed").length,
    items: grouped,
    isAnalyzing: false,
  }));
}

function deduplicateModulesByTitle(modules: Module[]) {
  const grouped = new Map<string, { id: string; title: string; ids: Set<string> }>();
  for (const module of modules) {
    const key = normalizeModuleTitle(module.title);
    const existing = grouped.get(key);
    if (existing) {
      existing.ids.add(module.id);
      continue;
    }
    grouped.set(key, {
      id: module.id,
      title: module.title,
      ids: new Set([module.id]),
    });
  }
  return [...grouped.values()];
}

function normalizeModuleTitle(title: string) {
  return title.trim().toLowerCase();
}

function candidateKey(candidate: Candidate) {
  return `${candidate.sourcePath}:${candidate.symbol}`;
}

function itemStatus(item: TreeItem) {
  return item.kind === "problem" ? item.problem.status : item.candidate.status;
}

function itemDepth(item: TreeItem) {
  return item.kind === "problem" ? item.problem.depth : item.candidate.depth;
}

function itemSymbol(item: TreeItem) {
  return item.kind === "problem" ? item.problem.target_symbol : item.candidate.symbol;
}

function difficultyStars(difficulty: Problem["difficulty"]) {
  if (difficulty === "hard") {
    return "★★★";
  }
  if (difficulty === "medium") {
    return "★★☆";
  }
  return "★☆☆";
}

function shortSymbol(symbol: string) {
  const parts = symbol.split(".");
  return parts.length > 2 ? parts.slice(-2).join(".") : symbol;
}

function stripPracticeSuffix(title: string) {
  const suffix = " 구현하기";
  return title.endsWith(suffix) ? title.slice(0, -suffix.length) : title;
}

function splitArchitectureFlow(flow: string) {
  return flow
    .replaceAll(String.fromCharCode(8594), "|")
    .replaceAll("->", "|")
    .replaceAll("=>", "|")
    .split("|");
}

function buildArchitectureFlow(
  summary: ProjectArchitectureResponse["project_summary"],
  architecture: ProjectArchitectureResponse["architecture"],
  title: string,
): ArchitectureFlowItem[] {
  const files = architecture?.files ?? [];
  const summaryFlow = summary?.architecture_flow;
  if (Array.isArray(summaryFlow) && summaryFlow.length > 0) {
    return summaryFlow.map((entry) => parseFlowEntry(String(entry), files));
  }
  if (typeof summaryFlow === "string" && summaryFlow.trim()) {
    return splitArchitectureFlow(summaryFlow)
      .map((part) => part.trim())
      .filter(Boolean)
      .map((entry) => parseFlowEntry(entry, files));
  }

  const explicitFlow = architecture?.architecture_flow;
  if (explicitFlow) {
    return splitArchitectureFlow(explicitFlow)
      .map((part) => part.trim())
      .filter(Boolean)
      .map((entry) => parseFlowEntry(entry, files));
  }

  if (files.length === 0) {
    return [];
  }

  const ordered = orderArchitectureFiles(files);
  return [
    { label: inferInputLabel(title), path: null, description: null },
    ...ordered.map((file) => ({
      label: file.classes?.[0] ?? file.functions?.[0] ?? titleFromPath(file.path),
      path: file.path,
      description: file.description ?? null,
    })),
    { label: "Output", path: null, description: null },
  ];
}

function parseFlowEntry(entry: string, files: ArchitectureFile[]): ArchitectureFlowItem {
  const [rawLabel, rawPath] = entry.split("|").map((part) => part.trim());
  const matched = rawPath ? files.find((file) => normalizePath(file.path) === normalizePath(rawPath)) : findFileForFlowLabel(rawLabel, files);
  return {
    label: rawLabel,
    path: rawPath || matched?.path || null,
    description: matched?.description ?? null,
  };
}

function orderArchitectureFiles(files: ArchitectureFile[]) {
  const byPath = new Map(files.map((file) => [file.path, file]));
  const visited = new Set<string>();
  const ordered: ArchitectureFile[] = [];

  function visit(file: ArchitectureFile) {
    if (visited.has(file.path)) {
      return;
    }
    visited.add(file.path);
    for (const dependency of file.depends_on ?? []) {
      const dependencyPath = resolveDependencyPath(dependency, files);
      const dependencyFile = dependencyPath ? byPath.get(dependencyPath) : null;
      if (dependencyFile) {
        visit(dependencyFile);
      }
    }
    ordered.push(file);
  }

  for (const file of files) {
    visit(file);
  }
  return ordered;
}

function resolveDependencyPath(dependency: string, files: ArchitectureFile[]) {
  const normalized = dependency.replaceAll("\\", "/").toLowerCase();
  return files.find((file) => {
    const path = file.path.toLowerCase();
    return path === normalized || path.endsWith(`/${normalized}`) || titleFromPath(file.path).toLowerCase() === normalized;
  })?.path;
}

function findFileForFlowLabel(label: string, files: ArchitectureFile[]) {
  const normalizedLabel = normalizeFlowText(label);
  return files.find((file) => {
    const haystack = normalizeFlowText([
      file.path,
      file.description ?? "",
      ...(file.classes ?? []),
      ...(file.functions ?? []),
    ].join(" "));
    return haystack.includes(normalizedLabel) || normalizedLabel.includes(normalizeFlowText(titleFromPath(file.path)));
  });
}

function isArchitectureNodeActive(item: ArchitectureFlowItem, activeTarget: { sourcePath: string | null; symbol: string | null }) {
  if (!activeTarget.sourcePath && !activeTarget.symbol) {
    return false;
  }
  if (item.path && activeTarget.sourcePath && normalizePath(item.path) === normalizePath(activeTarget.sourcePath)) {
    return true;
  }
  if (activeTarget.symbol) {
    const symbolPart = activeTarget.symbol.split(".")[0] ?? activeTarget.symbol;
    return normalizeFlowText(item.label).includes(normalizeFlowText(symbolPart));
  }
  return false;
}

function normalizePath(path: string) {
  return path.replaceAll("\\", "/").toLowerCase();
}

function normalizeFlowText(value: string) {
  return value.toLowerCase().replace(/[^a-z0-9?-?]+/g, "");
}

function inferInputLabel(title: string) {
  return /image|vision|mixer|vit|cnn/i.test(title) ? "Input Image" : "Input";
}

function titleFromPath(path: string) {
  const filename = path.split(/[\\/]/).at(-1) ?? path;
  return filename
    .replace(/\.[^.]+$/, "")
    .split(/[_-]+/)
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

function projectDisplayName(project: CurrentProject | null, fallback: string | undefined) {
  if (project?.paper_source && project.paper_title) {
    return truncateProjectName(project.paper_title);
  }
  if (project?.repo_path) {
    const normalized = project.repo_path.replaceAll("\\", "/");
    const name = normalized.split("/").filter(Boolean).at(-1);
    if (name) {
      return truncateProjectName(name);
    }
  }
  return fallback ? `Project ${fallback.slice(0, 8)}` : "프로젝트";
}

function truncateProjectName(name: string) {
  return name.length > 50 ? `${name.slice(0, 50)}...` : name;
}

function ResultPanel({
  result,
  isSubmitting,
  onOverwrite,
  canOverwrite,
}: {
  result: SubmitResult | null;
  isSubmitting: boolean;
  onOverwrite: () => void;
  canOverwrite: boolean;
}) {
  if (isSubmitting) {
    return (
      <section className="result-panel">
        <h2>채점 결과</h2>
        <p className="status-text">채점 중...</p>
      </section>
    );
  }

  if (!result) {
    return (
      <section className="result-panel">
        <h2>채점 결과</h2>
        <p className="empty-state">코드를 작성하고 제출하세요</p>
      </section>
    );
  }

  const shouldOfferOverwrite = result.passed && result.saved_path === null;
  const passedCaseCount = result.test_cases?.filter((testCase) => testCase.passed).length ?? 0;
  const totalCaseCount = result.test_cases?.length ?? 0;

  return (
    <section className="result-panel">
      <h2>채점 결과</h2>
      <p className={result.passed ? "result-status passed" : "result-status failed"}>
        {result.passed ? "통과!" : "실패"}
        {result.grading_method === "llm" ? " (LLM 채점)" : ""}
      </p>

      {result.grading_method === "llm" && totalCaseCount > 0 ? (
        <p className="case-summary">
          테스트 케이스 {passedCaseCount}/{totalCaseCount} 통과
          {result.score !== null ? ` · ${result.score}점` : ""}
        </p>
      ) : null}

      {result.grading_method === "llm" && result.test_cases?.length ? (
        <div className="test-case-results">
          <h3>테스트 케이스</h3>
          <ul>
            {result.test_cases.map((testCase, index) => (
              <li key={`${testCase.call_expression}:${index}`} className={testCase.passed ? "passed" : "failed"}>
                <strong>
                  {testCase.passed ? "✓" : "✗"} {testCase.description}
                </strong>
                <code>{testCase.call_expression}</code>
                {testCase.passed ? (
                  <span>{testCase.actual}</span>
                ) : (
                  <dl>
                    <div>
                      <dt>예상</dt>
                      <dd>{testCase.expected ?? "-"}</dd>
                    </div>
                    <div>
                      <dt>실제</dt>
                      <dd>{testCase.actual ?? "-"}</dd>
                    </div>
                  </dl>
                )}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {result.grading_method === "llm" && result.feedback ? (
        <div className="feedback-block">
          <h3>피드백</h3>
          <p>{result.feedback}</p>
        </div>
      ) : null}

      <dl className="result-meta">
        <div>
          <dt>소요 시간</dt>
          <dd>{result.duration_ms}ms</dd>
        </div>
        <div>
          <dt>저장 경로</dt>
          <dd>{result.saved_path ?? "-"}</dd>
        </div>
      </dl>

      {shouldOfferOverwrite ? (
        <div className="overwrite-prompt">
          <p>이미 저장된 파일이 있습니다. 덮어쓰시겠습니까?</p>
          <button type="button" onClick={onOverwrite} disabled={!canOverwrite}>
            덮어쓰기
          </button>
        </div>
      ) : null}

      {result.stdout ? (
        <div className="output-block">
          <h3>stdout</h3>
          <pre>{result.stdout}</pre>
        </div>
      ) : null}

      {result.stderr ? (
        <div className="output-block">
          <h3>stderr</h3>
          <pre>{result.stderr}</pre>
        </div>
      ) : null}
    </section>
  );
}

function HintModal({
  hints,
  isLoading,
  onHint,
  onClose,
}: {
  hints: HintItem[];
  isLoading: boolean;
  onHint: (level: number) => void;
  onClose: () => void;
}) {
  const viewedLevels = new Set(hints.map((hint) => hint.level));
  const [position, setPosition] = useState({ x: 120, y: 110 });
  const [isDragging, setIsDragging] = useState(false);
  const [dragOffset, setDragOffset] = useState({ x: 0, y: 0 });

  useEffect(() => {
    function handleMouseMove(event: MouseEvent) {
      if (!isDragging) {
        return;
      }
      setPosition({
        x: Math.max(12, event.clientX - dragOffset.x),
        y: Math.max(12, event.clientY - dragOffset.y),
      });
    }

    function handleMouseUp() {
      setIsDragging(false);
    }

    window.addEventListener("mousemove", handleMouseMove);
    window.addEventListener("mouseup", handleMouseUp);
    return () => {
      window.removeEventListener("mousemove", handleMouseMove);
      window.removeEventListener("mouseup", handleMouseUp);
    };
  }, [dragOffset.x, dragOffset.y, isDragging]);

  useEffect(() => {
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        onClose();
      }
    }

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [onClose]);

  function handleMouseDown(event: React.MouseEvent<HTMLDivElement>) {
    setIsDragging(true);
    setDragOffset({
      x: event.clientX - position.x,
      y: event.clientY - position.y,
    });
  }

  return (
    <section className="hint-modal" style={{ left: position.x, top: position.y }}>
      <div className="hint-modal-titlebar" onMouseDown={handleMouseDown}>
        <h2>💡 힌트</h2>
        <button type="button" onClick={onClose} onMouseDown={(event) => event.stopPropagation()}>
          닫기
        </button>
      </div>

      <div className="hint-actions">
        {[1, 2, 3].map((level) => (
          <button key={level} type="button" onClick={() => onHint(level)} disabled={isLoading || viewedLevels.has(level)}>
            레벨 {level}
          </button>
        ))}
      </div>

      {isLoading ? <p className="status-text">힌트 생성 중...</p> : null}

      {hints.length > 0 ? (
        <div className="hint-modal-content">
          {hints.map((hint) => (
            <article key={hint.level} className="hint-markdown">
              {hint.format === "markdown" ? <ReactMarkdown>{hint.hint}</ReactMarkdown> : <p>{hint.hint}</p>}
            </article>
          ))}
        </div>
      ) : (
        <p className="empty-state">필요한 힌트 레벨을 선택하세요</p>
      )}
    </section>
  );
}

function getErrorMessage(error: unknown, fallback = "요청을 처리하지 못했습니다") {
  if (axios.isAxiosError<ApiErrorResponse>(error)) {
    return error.response?.data?.message ?? fallback;
  }

  return fallback;
}
