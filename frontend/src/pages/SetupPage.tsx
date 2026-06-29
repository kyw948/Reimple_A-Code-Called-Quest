import axios from "axios";
import { FormEvent, useState } from "react";
import { useNavigate } from "react-router-dom";

import { apiClient } from "../api/client";
import { FileTreeNode, useProjectStore } from "../stores/useProjectStore";

type AnalyzeResponse = {
  repo_path: string;
  file_tree: FileTreeNode[];
  extension_stats: Record<string, number>;
};

type CloneResponse = {
  repo_path: string;
  already_exists: boolean;
};

type ProjectCreateResponse = {
  project_id: string;
};

type ProjectDetailResponse = {
  id: string;
  repo_path: string;
  practice_root_path: string;
  target_extensions: string[];
  paper_source?: "arxiv" | "pdf" | null;
  paper_url?: string | null;
  paper_title?: string | null;
  paper_abstract?: string | null;
  generated_repo_path?: string | null;
};

type ProjectSetupResponse = {
  copied_files: number;
  skipped_files: number;
};

type ProjectAnalyzeResponse = {
  status: "pending" | "analyzing" | "completed";
};

type PaperParseResponse = {
  title: string;
  abstract: string;
  authors: string[];
  year?: string | null;
  content: string;
  source: "arxiv" | "pdf";
  url?: string | null;
};

type ApiErrorResponse = {
  error_code: string;
  message: string;
  repo_path?: string;
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

export function SetupPage() {
  const navigate = useNavigate();
  const setCurrentProject = useProjectStore((state) => state.setCurrentProject);
  const setFileTree = useProjectStore((state) => state.setFileTree);
  const setExtensionStats = useProjectStore((state) => state.setExtensionStats);

  const [mode, setMode] = useState<"local" | "github" | "paper">("local");
  const [repoPath, setRepoPath] = useState("");
  const [githubUrl, setGithubUrl] = useState("");
  const [paperArxivUrl, setPaperArxivUrl] = useState("");
  const [paperFile, setPaperFile] = useState<File | null>(null);
  const [parsedPaper, setParsedPaper] = useState<PaperParseResponse | null>(null);
  const [practiceRootPath, setPracticeRootPath] = useState("");
  const [errorMessage, setErrorMessage] = useState("");
  const [statusMessage, setStatusMessage] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);

  async function handleLocalSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setErrorMessage("");
    setStatusMessage("");

    const trimmedRepoPath = repoPath.trim();
    if (!trimmedRepoPath) {
      setErrorMessage("로컬 경로를 입력하세요");
      return;
    }

    setIsSubmitting(true);
    try {
      await analyzeAndCreateProject(trimmedRepoPath);
    } catch (error) {
      setErrorMessage(getErrorMessage(error));
    } finally {
      setIsSubmitting(false);
      setStatusMessage("");
    }
  }

  async function handleGithubSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setErrorMessage("");
    setStatusMessage("");

    const trimmedGithubUrl = githubUrl.trim();
    if (!trimmedGithubUrl) {
      setErrorMessage("GitHub URL을 입력하세요");
      return;
    }

    setIsSubmitting(true);
    try {
      setStatusMessage("저장소 복제 중...");
      const clonedRepoPath = await cloneGithubRepo(trimmedGithubUrl);
      await analyzeAndCreateProject(clonedRepoPath);
    } catch (error) {
      setErrorMessage(getErrorMessage(error));
    } finally {
      setIsSubmitting(false);
      setStatusMessage("");
    }
  }

  async function handlePaperSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setErrorMessage("");
    setStatusMessage("");
    setParsedPaper(null);

    const trimmedArxivUrl = paperArxivUrl.trim();
    if (!trimmedArxivUrl && !paperFile) {
      setErrorMessage("arXiv URL을 입력하거나 PDF 파일을 선택하세요");
      return;
    }

    setIsSubmitting(true);
    try {
      const paper = await parsePaper(trimmedArxivUrl, paperFile);
      setParsedPaper(paper);
      setStatusMessage("논문 프로젝트 생성 중...");
      const projectResponse = await apiClient.post<ProjectCreateResponse>("/projects", {
        paper_source: paper.source,
        paper_url: paper.url,
        paper_title: paper.title,
        paper_abstract: paper.abstract,
        paper_content: paper.content,
        paper_metadata: {
          authors: paper.authors,
          year: paper.year,
        },
        practice_root_path: practiceRootPath.trim(),
      });
      const projectDetailResponse = await apiClient.get<ProjectDetailResponse>(
        `/projects/${projectResponse.data.project_id}`,
      );

      setCurrentProject({
        id: projectDetailResponse.data.id,
        repo_path: projectDetailResponse.data.repo_path,
        practice_root_path: projectDetailResponse.data.practice_root_path,
        target_extensions: projectDetailResponse.data.target_extensions,
        paper_source: projectDetailResponse.data.paper_source,
        paper_url: projectDetailResponse.data.paper_url,
        paper_title: projectDetailResponse.data.paper_title,
        paper_abstract: projectDetailResponse.data.paper_abstract,
        generated_repo_path: projectDetailResponse.data.generated_repo_path,
      });
      setFileTree([]);
      setExtensionStats({});
      navigate(`/projects/${projectResponse.data.project_id}/analyze`);
    } catch (error) {
      setErrorMessage(getErrorMessage(error));
    } finally {
      setIsSubmitting(false);
      setStatusMessage("");
    }
  }

  async function cloneGithubRepo(trimmedGithubUrl: string) {
    try {
      const cloneResponse = await apiClient.post<CloneResponse>("/repos/clone", {
        github_url: trimmedGithubUrl,
      });
      return cloneResponse.data.repo_path;
    } catch (error) {
      if (
        axios.isAxiosError<ApiErrorResponse>(error) &&
        error.response?.status === 409 &&
        error.response.data.error_code === "REPO_ALREADY_EXISTS" &&
        error.response.data.repo_path
      ) {
        return error.response.data.repo_path;
      }

      throw error;
    }
  }

  async function parsePaper(trimmedArxivUrl: string, selectedFile: File | null) {
    if (selectedFile) {
      setStatusMessage("논문 파싱 중...");
      const formData = new FormData();
      formData.append("file", selectedFile);
      const response = await apiClient.post<PaperParseResponse>("/papers/parse", formData);
      return response.data;
    }

    setStatusMessage("논문 다운로드 중...");
    const response = await apiClient.post<PaperParseResponse>("/papers/parse", {
      arxiv_url: trimmedArxivUrl,
    });
    return response.data;
  }

  async function analyzeAndCreateProject(repoPathToAnalyze: string) {
    const trimmedPracticeRootPath = practiceRootPath.trim();

    setStatusMessage("파일 구조 분석 중...");
    const analyzeResponse = await apiClient.post<AnalyzeResponse>("/repos/analyze", {
      repo_path: repoPathToAnalyze,
    });

    setFileTree(analyzeResponse.data.file_tree);
    setExtensionStats(analyzeResponse.data.extension_stats);
    const targetExtensions = detectCodeExtensions(analyzeResponse.data.extension_stats);

    setStatusMessage("프로젝트 생성 중...");
    const projectResponse = await apiClient.post<ProjectCreateResponse>("/projects", {
      repo_path: analyzeResponse.data.repo_path,
      practice_root_path: trimmedPracticeRootPath,
      target_extensions: targetExtensions,
    });
    const projectDetailResponse = await apiClient.get<ProjectDetailResponse>(
      `/projects/${projectResponse.data.project_id}`,
    );

    setCurrentProject({
      id: projectDetailResponse.data.id,
      repo_path: projectDetailResponse.data.repo_path,
      practice_root_path: projectDetailResponse.data.practice_root_path,
      target_extensions: projectDetailResponse.data.target_extensions,
    });

    setStatusMessage("파일 등록 중...");
    await apiClient.post<ProjectSetupResponse>(`/projects/${projectResponse.data.project_id}/setup`);

    setStatusMessage("프로젝트 구조 분석 중... (1/3)");
    await apiClient.post<ProjectAnalyzeResponse>(`/projects/${projectResponse.data.project_id}/analyze`);
    setStatusMessage("프로젝트 구조 분석 중... (2/3)");
    setStatusMessage("프로젝트 구조 분석 중... (3/3)");

    navigate(`/projects/${projectResponse.data.project_id}/analyze`);
  }

  const isGithubMode = mode === "github";
  const isPaperMode = mode === "paper";

  return (
    <main className="setup-page">
      <section className="setup-panel" aria-labelledby="setup-title">
        <div className="setup-heading">
          <h1 id="setup-title">CodePractice</h1>
        </div>

        <div className="setup-tabs" role="tablist" aria-label="Repo input mode">
          <button
            type="button"
            className={mode === "local" ? "active" : ""}
            onClick={() => {
              setMode("local");
              setErrorMessage("");
              setStatusMessage("");
            }}
          >
            로컬 경로
          </button>
          <button
            type="button"
            className={isGithubMode ? "active" : ""}
            onClick={() => {
              setMode("github");
              setErrorMessage("");
              setStatusMessage("");
            }}
          >
            GitHub URL
          </button>
          <button
            type="button"
            className={isPaperMode ? "active" : ""}
            onClick={() => {
              setMode("paper");
              setErrorMessage("");
              setStatusMessage("");
            }}
          >
            논문
          </button>
        </div>

        <form className="setup-form" onSubmit={isPaperMode ? handlePaperSubmit : isGithubMode ? handleGithubSubmit : handleLocalSubmit}>
          {isPaperMode ? (
            <div className="paper-inputs">
              <p className="setup-description">논문에서 코드 구현 연습을 시작합니다.</p>
              <label className="field">
                <span>arXiv URL</span>
                <input
                  type="text"
                  value={paperArxivUrl}
                  onChange={(event) => setPaperArxivUrl(event.target.value)}
                  placeholder="https://arxiv.org/abs/2204.12484"
                  autoComplete="off"
                />
              </label>
              <label className="field">
                <span>PDF 업로드</span>
                <input
                  type="file"
                  accept="application/pdf,.pdf"
                  onChange={(event) => setPaperFile(event.target.files?.[0] ?? null)}
                />
              </label>
            </div>
          ) : isGithubMode ? (
            <label className="field">
              <span>GitHub URL</span>
              <input
                type="text"
                value={githubUrl}
                onChange={(event) => setGithubUrl(event.target.value)}
                placeholder="https://github.com/user/repo"
                autoComplete="off"
              />
            </label>
          ) : (
            <label className="field">
              <span>로컬 경로</span>
              <input
                type="text"
                value={repoPath}
                onChange={(event) => setRepoPath(event.target.value)}
                placeholder="C:/Users/.../samples/python_basic"
                autoComplete="off"
              />
            </label>
          )}

          <label className="field">
            <span>연습 폴더 경로</span>
            <input
              type="text"
              value={practiceRootPath}
              onChange={(event) => setPracticeRootPath(event.target.value)}
              placeholder={getPracticeRootPlaceholder(mode, repoPath, githubUrl)}
              autoComplete="off"
            />
          </label>

          {statusMessage ? <p className="status-message">{statusMessage}</p> : null}

          {errorMessage ? (
            <p className="error-message" role="alert">
              {errorMessage}
            </p>
          ) : null}

          {parsedPaper ? (
            <div className="paper-preview">
              <strong>{parsedPaper.title}</strong>
              <p>{parsedPaper.abstract || "Abstract를 찾지 못했습니다."}</p>
            </div>
          ) : null}

          <button type="submit" disabled={isSubmitting}>
            {isSubmitting
              ? statusMessage || "처리 중..."
              : isPaperMode
                ? "논문 분석"
                : isGithubMode
                  ? "Clone & 분석"
                  : "분석"}
          </button>
        </form>
      </section>
    </main>
  );
}

function getErrorMessage(error: unknown) {
  if (axios.isAxiosError<ApiErrorResponse>(error)) {
    const errorCode = error.response?.data?.error_code;
    const message = error.response?.data?.message;

    if (errorCode === "CLONE_FAILED") {
      return message?.toLowerCase().includes("git")
        ? "git이 설치되어 있지 않거나 실행할 수 없습니다"
        : "저장소 복제에 실패했습니다";
    }

    return message ?? "요청을 처리하지 못했습니다";
  }

  return "요청을 처리하지 못했습니다";
}

function detectCodeExtensions(extensionStats: Record<string, number>) {
  return Object.keys(extensionStats)
    .filter((extension) => DEFAULT_CODE_EXTENSIONS.has(extension.toLowerCase()))
    .sort((left, right) => left.localeCompare(right));
}

function getPracticeRootPlaceholder(mode: "local" | "github" | "paper", repoPath: string, githubUrl: string) {
  if (mode === "paper") {
    return "미입력 시 ~/.codepractice/practice/{논문 제목}";
  }
  const repoName = getRepoName(mode === "github" ? githubUrl : repoPath);
  return `미입력 시 ~/.codepractice/practice/${repoName}`;
}

function getRepoName(value: string) {
  const trimmed = value.trim().replace(/[\\/]+$/, "");
  if (!trimmed) {
    return "{repo이름}";
  }

  const parts = trimmed.split(/[\\/]/);
  return (parts[parts.length - 1] || "{repo이름}").replace(/\.git$/i, "") || "{repo이름}";
}
