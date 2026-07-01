import axios from "axios";
import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { apiClient } from "../api/client";
import { CurrentProject, FileTreeNode, useProjectStore } from "../stores/useProjectStore";

type ProjectDetailResponse = CurrentProject & {
  created_at: string;
  updated_at: string;
  analysis_status: "pending" | "analyzing" | "planning" | "planned" | "completed" | string;
  assess_status: "pending" | "running" | "completed" | string;
  project_summary: ProjectAnalyzeResponse["project_summary"];
  architecture: ProjectAnalyzeResponse["architecture"];
  dependency_graph: ProjectAnalyzeResponse["dependency_graph"];
  paper_content?: string | null;
  paper_metadata?: {
    authors?: string[];
    year?: string | null;
  };
  file_counts: {
    total: number;
    target: number;
    passed: number;
    pending: number;
  };
};

type PaperPlanResponse = {
  status: "planned";
  overall_plan: {
    summary?: string;
    domain?: string;
    framework?: string;
    components?: Array<{
      name: string;
      description?: string;
      category?: string;
      importance?: string;
    }>;
    key_algorithms?: string[];
    required_libraries?: string[];
  };
  architecture: {
    files?: Array<{
      path: string;
      description?: string;
      classes?: string[];
      functions?: string[];
      depends_on?: string[];
    }>;
  };
  logic_design: {
    implementation_order?: string[];
    specifications?: Array<{
      file: string;
      class?: string;
      methods?: Record<string, unknown>;
    }>;
  };
};

type PaperCodegenStartResponse = {
  status: "started" | "completed";
};

type PaperCodegenStatusResponse = {
  status: "idle" | "running" | "completed" | "error" | string;
  total_files: number;
  generated_files: number;
  current_file?: string | null;
  progress: number;
  generated_repo_path?: string | null;
  completed_files?: string[];
  files: string[];
  errors: Array<{
    path: string;
    message: string;
  }>;
};

type AnalyzeResponse = {
  repo_path: string;
  file_tree: FileTreeNode[];
  extension_stats: Record<string, number>;
};

type ProjectSetupResponse = {
  copied_files: number;
  skipped_files: number;
};

type ProjectAnalyzeResponse = {
  status: "pending" | "analyzing" | "completed";
  project_summary: {
    project_summary?: string;
    domain?: string;
    framework?: string;
    key_components?: string[];
    datasets?: string[];
    main_contribution?: string;
  };
  architecture: {
    modules?: Record<
      string,
      {
        description: string;
        files: string[];
      }
    >;
    file_dependencies?: Record<string, string[]>;
    non_problem_files?: string[];
  };
  dependency_graph: {
    implementation_order?: Array<{
      symbol: string;
      file: string;
      depth: number;
    }>;
  };
};

type ApiErrorResponse = {
  error_code: string;
  message: string;
};

const DEFAULT_CODE_EXTENSIONS = new Set([
  ".py",
  ".c",
  ".cpp",
  ".h",
  ".hpp",
  ".java",
  ".js",
  ".ts",
  ".jsx",
  ".tsx",
  ".go",
  ".rs",
  ".rb",
  ".swift",
  ".kt",
  ".cs",
  ".scala",
  ".r",
]);

export function AnalyzePage() {
  const { id } = useParams();
  const navigate = useNavigate();

  const currentProject = useProjectStore((state) => state.currentProject);
  const fileTree = useProjectStore((state) => state.fileTree);
  const extensionStats = useProjectStore((state) => state.extensionStats);
  const setCurrentProject = useProjectStore((state) => state.setCurrentProject);
  const setFileTree = useProjectStore((state) => state.setFileTree);
  const setExtensionStats = useProjectStore((state) => state.setExtensionStats);

  const [isLoading, setIsLoading] = useState(true);
  const [isPreparingPractice, setIsPreparingPractice] = useState(false);
  const [statusText, setStatusText] = useState("");
  const [errorMessage, setErrorMessage] = useState("");
  const [selectedExtensions, setSelectedExtensions] = useState<string[]>([]);
  const [hasInitializedExtensions, setHasInitializedExtensions] = useState(false);
  const [projectAnalysis, setProjectAnalysis] = useState<ProjectAnalyzeResponse | null>(null);
  const [paperPlan, setPaperPlan] = useState<PaperPlanResponse | null>(null);
  const [codegenStatus, setCodegenStatus] = useState<PaperCodegenStatusResponse | null>(null);
  const [paperMetadata, setPaperMetadata] = useState<ProjectDetailResponse["paper_metadata"] | null>(null);
  const [paperWorkflowStatus, setPaperWorkflowStatus] = useState("pending");
  const selectedExtensionSet = useMemo(() => new Set(selectedExtensions), [selectedExtensions]);

  useEffect(() => {
    let ignore = false;

    async function loadProject() {
      if (!id) {
        setErrorMessage("프로젝트를 찾을 수 없습니다");
        setIsLoading(false);
        return;
      }

      setIsLoading(true);
      setErrorMessage("");

      try {
        const projectResponse = await apiClient.get<ProjectDetailResponse>(`/projects/${id}`);
        if (ignore) {
          return;
        }

        const project = {
          id: projectResponse.data.id,
          repo_path: projectResponse.data.repo_path,
          practice_root_path: projectResponse.data.practice_root_path,
          target_extensions: projectResponse.data.target_extensions,
          paper_source: projectResponse.data.paper_source,
          paper_url: projectResponse.data.paper_url,
          paper_title: projectResponse.data.paper_title,
          paper_abstract: projectResponse.data.paper_abstract,
          generated_repo_path: projectResponse.data.generated_repo_path,
        };
        setCurrentProject(project);
        setPaperMetadata(projectResponse.data.paper_metadata ?? null);
        setPaperWorkflowStatus(projectResponse.data.analysis_status ?? "pending");

        if (!project.paper_source && fileTree.length === 0) {
          const analyzeResponse = await apiClient.post<AnalyzeResponse>("/repos/analyze", {
            repo_path: project.repo_path,
          });
          if (ignore) {
            return;
          }

          setFileTree(analyzeResponse.data.file_tree);
          setExtensionStats(analyzeResponse.data.extension_stats);
        }

        if (project.paper_source) {
          setProjectAnalysis(null);
          if (projectResponse.data.analysis_status === "code_generated") {
            const statusResponse = await apiClient.get<PaperCodegenStatusResponse>(`/papers/${id}/codegen-status`);
            if (!ignore) {
              setCodegenStatus(statusResponse.data);
            }
          }
          if (
            ["planned", "code_generated", "completed"].includes(projectResponse.data.analysis_status) &&
            Object.keys(projectResponse.data.project_summary ?? {}).length > 0 &&
            Object.keys(projectResponse.data.architecture ?? {}).length > 0 &&
            Object.keys(projectResponse.data.dependency_graph ?? {}).length > 0
          ) {
            setPaperPlan({
              status: "planned",
              overall_plan: projectResponse.data.project_summary as PaperPlanResponse["overall_plan"],
              architecture: projectResponse.data.architecture as PaperPlanResponse["architecture"],
              logic_design: projectResponse.data.dependency_graph as PaperPlanResponse["logic_design"],
            });
          }
        } else if (projectResponse.data.analysis_status === "completed") {
          setProjectAnalysis({
            status: "completed",
            project_summary: projectResponse.data.project_summary ?? {},
            architecture: projectResponse.data.architecture ?? {},
            dependency_graph: projectResponse.data.dependency_graph ?? {},
          });
        } else {
          const projectAnalyzeResponse = await apiClient.post<ProjectAnalyzeResponse>(`/projects/${id}/analyze`);
          if (ignore) {
            return;
          }
          setProjectAnalysis(projectAnalyzeResponse.data);
        }
      } catch (error) {
        if (!ignore) {
          setErrorMessage(getErrorMessage(error, "프로젝트를 찾을 수 없습니다"));
        }
      } finally {
        if (!ignore) {
          setIsLoading(false);
        }
      }
    }

    loadProject();

    return () => {
      ignore = true;
    };
  }, [id, fileTree.length, setCurrentProject, setExtensionStats, setFileTree]);

  useEffect(() => {
    const extensions = Object.keys(extensionStats).sort((left, right) => left.localeCompare(right));
    if (hasInitializedExtensions || extensions.length === 0) {
      return;
    }

    const savedExtensions = currentProject?.target_extensions ?? [];
    setSelectedExtensions(
      savedExtensions.length > 0
        ? savedExtensions.filter((extension) => extension in extensionStats)
        : extensions.filter((extension) => DEFAULT_CODE_EXTENSIONS.has(extension.toLowerCase())),
    );
    setHasInitializedExtensions(true);
  }, [currentProject?.target_extensions, extensionStats, hasInitializedExtensions]);

  async function handleStartPractice() {
    if (!id) {
      setErrorMessage("프로젝트를 찾을 수 없습니다");
      return;
    }

    setIsPreparingPractice(true);
    setErrorMessage("");
    setStatusText("연습 준비 중...");

    try {
      setStatusText("연습 화면으로 이동합니다...");
      navigate(`/projects/${id}/practice`);
    } catch (error) {
      setErrorMessage(getErrorMessage(error));
      setStatusText("");
    } finally {
      setIsPreparingPractice(false);
    }
  }

  async function handleStartPaperPlan() {
    if (!id) {
      setErrorMessage("프로젝트를 찾을 수 없습니다");
      return;
    }

    setErrorMessage("");
    setStatusText("논문 분석 중... (1/3 핵심 컴포넌트 추출)");
    try {
      window.setTimeout(() => setStatusText("구조 설계 중... (2/3 파일 구조 설계)"), 600);
      window.setTimeout(() => setStatusText("상세 명세 작성 중... (3/3 구현 순서 결정)"), 1200);
      const response = await apiClient.post<PaperPlanResponse>(`/papers/${id}/plan`);
      setPaperPlan(response.data);
      setPaperWorkflowStatus("planned");
      setStatusText("구조 설계가 완료되었습니다.");
    } catch (error) {
      setErrorMessage(getErrorMessage(error, "논문 구조 설계에 실패했습니다"));
      setStatusText("");
    }
  }

  async function handleStartCodeGeneration() {
    if (!id) {
      setErrorMessage("프로젝트를 찾을 수 없습니다");
      return;
    }

    setErrorMessage("");
    setStatusText("코드 생성 시작 중...");
    try {
      await apiClient.post<PaperCodegenStartResponse>(`/papers/${id}/generate-code`);
      const firstStatus = await apiClient.get<PaperCodegenStatusResponse>(`/papers/${id}/codegen-status`);
      setCodegenStatus(firstStatus.data);
      if (firstStatus.data.status === "completed") {
        setPaperWorkflowStatus("code_generated");
      }

      const intervalId = window.setInterval(async () => {
        try {
          const statusResponse = await apiClient.get<PaperCodegenStatusResponse>(`/papers/${id}/codegen-status`);
          setCodegenStatus(statusResponse.data);
          if (["completed", "error"].includes(statusResponse.data.status)) {
            window.clearInterval(intervalId);
            if (statusResponse.data.status === "completed") {
              setPaperWorkflowStatus("code_generated");
            }
            setStatusText(statusResponse.data.status === "completed" ? "코드 생성이 완료되었습니다." : "코드 생성에 실패했습니다.");
          }
        } catch (error) {
          window.clearInterval(intervalId);
          setErrorMessage(getErrorMessage(error, "코드 생성 상태를 확인하지 못했습니다"));
        }
      }, 5000);
    } catch (error) {
      setErrorMessage(getErrorMessage(error, "코드 생성 시작에 실패했습니다"));
      setStatusText("");
    }
  }

  async function handleStartGeneratedPractice() {
    if (!id) {
      setErrorMessage("프로젝트를 찾을 수 없습니다");
      return;
    }

    setIsPreparingPractice(true);
    setErrorMessage("");
    try {
      setStatusText("생성된 코드 파일 등록 중...");
      const projectResponse = await apiClient.get<ProjectDetailResponse>(`/projects/${id}`);
      console.log(`[연습시작] setup 호출, repo_path=${projectResponse.data.repo_path}, generated_repo_path=${projectResponse.data.generated_repo_path ?? "-"}`);
      await apiClient.post<ProjectSetupResponse>(`/projects/${id}/setup`);
      console.log("[연습시작] setup 완료");
      setStatusText("생성된 코드 구조 분석 중...");
      console.log("[연습시작] analyze 호출");
      await apiClient.post<ProjectAnalyzeResponse>(`/projects/${id}/analyze`, { force: true });
      console.log("[연습시작] analyze 완료");
      navigate(`/projects/${id}/practice`);
    } catch (error) {
      setErrorMessage(getErrorMessage(error, "연습 시작에 실패했습니다"));
      setStatusText("");
    } finally {
      setIsPreparingPractice(false);
    }
  }

  if (isLoading) {
    return <main className="analyze-page">프로젝트 정보를 불러오는 중...</main>;
  }

  const isPaperProject = Boolean(currentProject?.paper_source);
  const displayName = projectDisplayName(currentProject, id);

  return (
    <main className="analyze-page">
      <header className="project-header">
        <div>
          <h1>{displayName}</h1>
          <dl className="project-meta">
            <div>
              <dt>Repo</dt>
              <dd>{currentProject?.repo_path ?? "-"}</dd>
            </div>
            <div>
              <dt>연습 폴더</dt>
              <dd>{currentProject?.practice_root_path ?? "-"}</dd>
            </div>
          </dl>
        </div>
      </header>

      {errorMessage ? (
        <p className="error-message" role="alert">
          {errorMessage}
        </p>
      ) : null}

      {isPaperProject ? (
        <PaperAnalysisSummary
          project={currentProject}
          paperMetadata={paperMetadata}
          plan={paperPlan}
          codegenStatus={codegenStatus}
          workflowStatus={paperWorkflowStatus}
          statusText={statusText}
          onStartPlan={handleStartPaperPlan}
          onStartCodeGeneration={handleStartCodeGeneration}
          onStartGeneratedPractice={handleStartGeneratedPractice}
          isPreparingPractice={isPreparingPractice}
        />
      ) : (
        <>
      <section className="analyze-layout">
        <section className="analyze-panel">
          <h2>파일 트리</h2>
          <div className="file-tree" aria-label="파일 트리">
            {fileTree.length > 0 ? (
              fileTree.map((node) => <FileTreeItem key={node.path} node={node} selectedExtensions={selectedExtensionSet} />)
            ) : (
              "파일이 없습니다"
            )}
          </div>
        </section>

        <section className="analyze-panel">
          <h2>연습 대상 확장자 선택</h2>
          <ExtensionSelector
            stats={extensionStats}
            selectedExtensions={selectedExtensions}
            onChange={setSelectedExtensions}
          />
        </section>
      </section>

      <section className="setup-actions">
        <button type="button" onClick={handleStartPractice} disabled={isPreparingPractice}>
          연습 시작
        </button>

        {statusText ? <p className="status-text">{statusText}</p> : null}
      </section>
        </>
      )}

      {!isPaperProject && projectAnalysis ? (
        <ProjectAnalysisSummary
          analysis={projectAnalysis}
        />
      ) : null}
    </main>
  );
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
  return fallback ? `Project ${fallback.slice(0, 8)}` : "????";
}

function truncateProjectName(name: string) {
  return name.length > 50 ? `${name.slice(0, 50)}...` : name;
}

function PaperAnalysisSummary({
  project,
  paperMetadata,
  plan,
  codegenStatus,
  workflowStatus,
  statusText,
  onStartPlan,
  onStartCodeGeneration,
  onStartGeneratedPractice,
  isPreparingPractice,
}: {
  project: CurrentProject | null;
  paperMetadata: ProjectDetailResponse["paper_metadata"] | null;
  plan: PaperPlanResponse | null;
  codegenStatus: PaperCodegenStatusResponse | null;
  workflowStatus: string;
  statusText: string;
  onStartPlan: () => void;
  onStartCodeGeneration: () => void;
  onStartGeneratedPractice: () => void;
  isPreparingPractice: boolean;
}) {
  const components = plan?.overall_plan.components ?? [];
  const files = plan?.architecture.files ?? [];
  const implementationOrder = plan?.logic_design.implementation_order ?? [];
  const completedFiles = codegenStatus?.completed_files ?? codegenStatus?.files ?? [];
  const plannedFiles = files.map((file) => file.path);
  const displayFiles = mergeFileLists(plannedFiles, completedFiles, codegenStatus?.current_file ?? null);
  const codegenPercent = Math.round((codegenStatus?.progress ?? 0) * 100);
  const normalizedWorkflowStatus = workflowStatus === "completed" ? "code_generated" : workflowStatus;
  const isCodegenComplete = codegenStatus?.status === "completed" || normalizedWorkflowStatus === "code_generated";
  const isCodegenRunning = codegenStatus?.status === "running";
  const canPlan = normalizedWorkflowStatus === "pending" && !plan;
  const canGenerateCode = Boolean(plan) && normalizedWorkflowStatus === "planned" && !isCodegenRunning;
  const canStartPractice = normalizedWorkflowStatus === "code_generated";
  const authors = paperMetadata?.authors ?? [];

  return (
    <section className="project-analysis-summary paper-analysis-summary">
      <div className="assessment-header">
        <h2>논문 정보</h2>
      </div>
      <dl className="project-meta">
        <div>
          <dt>입력 방식</dt>
          <dd>{project?.paper_source === "arxiv" ? "arXiv" : "PDF"}</dd>
        </div>
        <div>
          <dt>논문 URL</dt>
          <dd>{project?.paper_url || "-"}</dd>
        </div>
      </dl>
      <h3>{project?.paper_title || "제목 없음"}</h3>
      {authors.length > 0 ? <p className="paper-authors">{authors.join(", ")}</p> : null}
      <p>{project?.paper_abstract || "Abstract를 찾지 못했습니다."}</p>

      <div className="paper-stepper">
        <div className={paperStepClass(canPlan, normalizedWorkflowStatus !== "pending")}>
          <div className="paper-step-card-header">
            <div className="paper-step-title">
              <span className="paper-step-index">{normalizedWorkflowStatus !== "pending" ? "✓" : "1"}</span>
              <h3>구조 설계</h3>
            </div>
            <StepStatusText isCompleted={normalizedWorkflowStatus !== "pending"} isActive={canPlan} />
          </div>
          {canPlan ? (
            <button className="paper-step-action" type="button" onClick={onStartPlan} disabled={isCodegenRunning}>
              구조 설계 시작
            </button>
          ) : plan ? (
            <details className="paper-step-details">
              <summary>설계 결과 보기</summary>
              <div className="paper-plan-results">
                <section className="analysis-modules">
                  <h3>핵심 컴포넌트</h3>
                  <p>{plan.overall_plan.summary || "요약 없음"}</p>
                  <ul>
                    {components.map((component) => (
                      <li key={component.name}>
                        <strong>{component.name}</strong>
                        <span>{component.description || "-"}</span>
                        <small>
                          {component.category || "other"} ? {component.importance || "supporting"}
                        </small>
                      </li>
                    ))}
                  </ul>
                </section>

                <section className="analysis-modules">
                  <h3>파일 구조</h3>
                  <ul>
                    {files.map((file) => (
                      <li key={file.path}>
                        <strong>{file.path}</strong>
                        <span>{file.description || "-"}</span>
                        <small>{[...(file.classes ?? []), ...(file.functions ?? [])].join(", ") || "구성 요소 없음"}</small>
                      </li>
                    ))}
                  </ul>
                </section>

                <section className="analysis-modules">
                  <h3>구현 순서</h3>
                  <ol>
                    {implementationOrder.map((path) => (
                      <li key={path}>
                        <span>{path}</span>
                      </li>
                    ))}
                  </ol>
                </section>
              </div>
            </details>
          ) : null}
        </div>

        <div className={paperStepClass(canGenerateCode || isCodegenRunning, normalizedWorkflowStatus === "code_generated")}>
          <div className="paper-step-card-header">
            <div className="paper-step-title">
              <span className="paper-step-index">{normalizedWorkflowStatus === "code_generated" ? "✓" : "2"}</span>
              <h3>코드 생성</h3>
            </div>
            <StepStatusText
              isCompleted={normalizedWorkflowStatus === "code_generated"}
              isActive={canGenerateCode || isCodegenRunning}
              pendingText="이전 단계 완료 후 가능"
            />
          </div>
          {canGenerateCode ? (
            <button className="paper-step-action" type="button" onClick={onStartCodeGeneration}>
              코드 생성 시작
            </button>
          ) : normalizedWorkflowStatus === "pending" ? null : codegenStatus ? (
            <section className="analysis-modules codegen-status">
              <p>
                {isCodegenComplete
                  ? `코드 생성 완료! ${completedFiles.length}개 파일이 생성되었습니다.`
                  : codegenStatus.status === "error"
                    ? "코드 생성 중 오류가 발생했습니다."
                    : `코드 생성 중... ${codegenStatus.generated_files}/${codegenStatus.total_files} 파일 (${codegenPercent}%)`}
              </p>
              {codegenStatus.current_file ? <small>현재: {codegenStatus.current_file}</small> : null}
              <div className="progress-bar" aria-label="코드 생성 진행률">
                <span style={{ width: `${codegenPercent}%` }} />
              </div>
              <ul className="codegen-file-list">
                {displayFiles.map((path) => (
                  <li key={path} className={fileCodegenClass(path, completedFiles, codegenStatus.current_file ?? null)}>
                    <span>{fileCodegenIcon(path, completedFiles, codegenStatus.current_file ?? null)}</span>
                    <span>{path}</span>
                    {path === codegenStatus.current_file ? <small>생성 중...</small> : null}
                  </li>
                ))}
              </ul>
              {codegenStatus.errors.length > 0 ? (
                <details>
                  <summary>오류 파일 보기</summary>
                  <ul>
                    {codegenStatus.errors.map((error) => (
                      <li key={`${error.path}:${error.message}`}>
                        <strong>{error.path || "전체"}</strong>
                        <span>{error.message}</span>
                      </li>
                    ))}
                  </ul>
                </details>
              ) : null}
            </section>
          ) : (
            <p className="paper-step-muted">이전 단계 완료 후 가능</p>
          )}
        </div>

        <div className={paperStepClass(canStartPractice, false)}>
          <div className="paper-step-card-header">
            <div className="paper-step-title">
              <span className="paper-step-index">3</span>
              <h3>연습 시작</h3>
            </div>
            <StepStatusText isCompleted={false} isActive={canStartPractice} pendingText="코드 생성 완료 후 가능" />
          </div>
          {canStartPractice ? (
            <button className="paper-step-action practice" type="button" onClick={onStartGeneratedPractice} disabled={isPreparingPractice}>
              연습 시작
            </button>
          ) : null}
        </div>
      </div>
      {statusText ? <p className="status-text">{statusText}</p> : null}
    </section>
  );
}

function paperStepClass(isActive: boolean, isCompleted: boolean) {
  if (isCompleted) {
    return "paper-step-card completed";
  }
  if (isActive) {
    return "paper-step-card active";
  }
  return "paper-step-card disabled";
}

function StepStatusText({
  isCompleted,
  isActive,
  pendingText = "이전 단계 완료 후 가능",
}: {
  isCompleted: boolean;
  isActive: boolean;
  pendingText?: string;
}) {
  if (isCompleted) {
    return <span className="paper-step-state completed">완료</span>;
  }
  if (isActive) {
    return <span className="paper-step-state active">진행 가능</span>;
  }
  return <span className="paper-step-state disabled">{pendingText}</span>;
}

function mergeFileLists(plannedFiles: string[], completedFiles: string[], currentFile: string | null) {
  const ordered = new Set<string>();
  for (const path of plannedFiles) {
    ordered.add(path);
  }
  for (const path of completedFiles) {
    ordered.add(path);
  }
  if (currentFile) {
    ordered.add(currentFile);
  }
  return [...ordered];
}

function fileCodegenClass(path: string, completedFiles: string[], currentFile: string | null) {
  if (completedFiles.includes(path)) {
    return "completed";
  }
  if (path === currentFile) {
    return "current";
  }
  return "pending";
}

function fileCodegenIcon(path: string, completedFiles: string[], currentFile: string | null) {
  if (completedFiles.includes(path)) {
    return "✓";
  }
  if (path === currentFile) {
    return "→";
  }
  return "";
}

function ProjectAnalysisSummary({
  analysis,
}: {
  analysis: ProjectAnalyzeResponse;
}) {
  const allModules = Object.entries(analysis.architecture.modules ?? {});
  const problemModules = allModules.filter(([name]) => ["model", "training", "data"].includes(name));
  const excludedModules = allModules.filter(([name]) => !["model", "training", "data"].includes(name));
  const order = (analysis.dependency_graph.implementation_order ?? []).slice(0, 10);

  return (
    <section className="project-analysis-summary">
      <div className="assessment-header">
        <h2>프로젝트 개요</h2>
      </div>

      <p>{analysis.project_summary.project_summary || "프로젝트 요약을 생성하지 못했습니다."}</p>
      <dl className="project-meta">
        <div>
          <dt>도메인</dt>
          <dd>{analysis.project_summary.domain || "-"}</dd>
        </div>
        <div>
          <dt>프레임워크</dt>
          <dd>{analysis.project_summary.framework || "-"}</dd>
        </div>
        <div>
          <dt>핵심 기여</dt>
          <dd>{analysis.project_summary.main_contribution || "-"}</dd>
        </div>
      </dl>

      {problemModules.length > 0 ? (
        <div className="analysis-modules">
          <h3>문제 대상 모듈</h3>
          <ul>
            {problemModules.map(([name, module]) => (
              <li key={name}>
                <strong>{name}</strong>
                <span>{module.description}</span>
                <small>{module.files.length}개 파일</small>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {excludedModules.length > 0 ? (
        <details className="analysis-modules excluded-modules">
          <summary>제외된 모듈 보기</summary>
          <ul>
            {excludedModules.map(([name, module]) => (
              <li key={name}>
                <strong>{name}</strong>
                <span>{module.description}</span>
                <small>{module.files.length}개 파일</small>
              </li>
            ))}
          </ul>
        </details>
      ) : null}

      {order.length > 0 ? (
        <div className="analysis-modules">
          <h3>구현 순서 미리보기 (총 {analysis.dependency_graph.implementation_order?.length ?? 0}개 문제)</h3>
          <ol>
            {order.slice(0, 8).map((item) => (
              <li key={`${item.file}:${item.symbol}`}>
                <span>{item.symbol}</span>
                <small>
                  {item.file} · {difficultyStarsFromDepth(item.depth)}
                </small>
              </li>
            ))}
          </ol>
        </div>
      ) : null}
    </section>
  );
}

function difficultyStarsFromDepth(depth: number) {
  if (depth >= 2) {
    return "★★★";
  }
  if (depth === 1) {
    return "★★☆";
  }
  return "★☆☆";
}

function FileTreeItem({ node, selectedExtensions }: { node: FileTreeNode; selectedExtensions: Set<string> }) {
  if (node.type === "directory") {
    return (
      <details className="tree-directory" open>
        <summary>
          <span className="node-icon directory-icon">dir</span>
          {node.name}
        </summary>
        <div className="tree-children">
          {node.children?.map((child) => (
            <FileTreeItem key={child.path} node={child} selectedExtensions={selectedExtensions} />
          ))}
        </div>
      </details>
    );
  }

  const isSelectedTarget = node.extension ? selectedExtensions.has(node.extension) : false;

  return (
    <div className={isSelectedTarget ? "tree-file target-file" : "tree-file"}>
      <span className="node-icon file-icon">{node.extension || "file"}</span>
      <span>{node.name}</span>
    </div>
  );
}

function ExtensionSelector({
  stats,
  selectedExtensions,
  onChange,
}: {
  stats: Record<string, number>;
  selectedExtensions: string[];
  onChange: (extensions: string[]) => void;
}) {
  const entries = Object.entries(stats).sort(([left], [right]) => left.localeCompare(right));
  const selected = new Set(selectedExtensions);

  if (entries.length === 0) {
    return <p className="empty-state">통계가 없습니다</p>;
  }

  return (
    <ul className="extension-selector">
      {entries.map(([extension, count]) => (
        <li key={extension}>
          <label>
            <input
              type="checkbox"
              checked={selected.has(extension)}
              onChange={(event) => {
                onChange(
                  event.target.checked
                    ? [...selectedExtensions, extension].sort((left, right) => left.localeCompare(right))
                    : selectedExtensions.filter((selectedExtension) => selectedExtension !== extension),
                );
              }}
            />
            <span>{extension}</span>
            <strong>({count}개 파일)</strong>
          </label>
        </li>
      ))}
    </ul>
  );
}

export function extractSourcePathsByExtension(nodes: FileTreeNode[], selectedExtensions: Set<string>): string[] {
  const paths: string[] = [];

  for (const node of nodes) {
    if (node.type === "directory") {
      paths.push(...extractSourcePathsByExtension(node.children ?? [], selectedExtensions));
      continue;
    }

    if (node.extension && selectedExtensions.has(node.extension) && !node.name.startsWith("test_")) {
      paths.push(node.path);
    }
  }

  return paths;
}

function getErrorMessage(error: unknown, notFoundFallback = "요청을 처리하지 못했습니다") {
  if (axios.isAxiosError<ApiErrorResponse>(error)) {
    const errorCode = error.response?.data?.error_code;
    if (errorCode === "PROJECT_NOT_FOUND") {
      return "프로젝트를 찾을 수 없습니다";
    }
    return error.response?.data?.message ?? notFoundFallback;
  }

  return notFoundFallback;
}
