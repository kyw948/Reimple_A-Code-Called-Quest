import { create } from "zustand";

import { apiClient } from "../api/client";

export type FileTreeNode = {
  name: string;
  path: string;
  type: "file" | "directory";
  children?: FileTreeNode[];
  extension?: string;
  size_bytes?: number;
};

export type CurrentProject = {
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

export type Problem = {
  id: string;
  source_path: string;
  target_symbol: string;
  problem_type: "function_blank" | "function_partial";
  grading_method: "pytest" | "llm";
  difficulty: "easy" | "medium" | "hard" | null;
  status: "locked" | "unlocked" | "active" | "passed" | "failed";
  parentId: string | null;
  depth: number;
  unlockDependencies: string[];
  roleInProject: string | null;
};

export type ProblemDetail = Problem & {
  project_id: string;
  file_id: string;
  problem_type: "function_blank" | "function_partial";
  prompt: string;
  starter_code: string;
  test_path: string | null;
  grading_method: "pytest" | "llm";
  original_code: string | null;
  difficulty: "easy" | "medium" | "hard" | null;
  context: string | null;
  parent_id?: string | null;
  weight: number;
  unlock_dependencies?: string | string[] | null;
  role_in_project?: string | null;
  created_at: string;
  updated_at: string;
};

export type Module = {
  id: string;
  title: string;
  description: string;
  weight: number;
  problemCount: number;
  passedCount: number;
  progress: number;
};

export type Candidate = {
  sourcePath: string;
  symbol: string;
  difficulty: "easy" | "medium" | "hard";
  problemType: "function_blank" | "function_partial";
  roleInProject: string | null;
  dependsOn: string[];
  usedBy: string[];
  status: "locked" | "unlocked" | "active" | "passed" | "failed" | "skipped" | "error";
  depth: number;
  generated: boolean;
  problemId: string | null;
  parentId: string | null;
};

export type SubmitResult = {
  passed: boolean;
  feedback: string | null;
  score: number | null;
  test_cases: Array<{
    description: string;
    call_expression: string;
    expected: string | null;
    actual: string | null;
    passed: boolean;
  }> | null;
  stdout: string | null;
  stderr: string | null;
  duration_ms: number;
  saved_path: string | null;
  grading_method: "pytest" | "llm";
};

type ProjectState = {
  currentProject: CurrentProject | null;
  fileTree: FileTreeNode[];
  extensionStats: Record<string, number>;
  problems: Problem[];
  candidates: Candidate[];
  modules: Module[];
  overallProgress: number;
  currentProblem: ProblemDetail | null;
  editorCode: string;
  submitResult: SubmitResult | null;
  setCurrentProject: (project: CurrentProject | null) => void;
  setFileTree: (fileTree: FileTreeNode[]) => void;
  setExtensionStats: (extensionStats: Record<string, number>) => void;
  setProblems: (problems: Problem[]) => void;
  setCandidates: (candidates: Candidate[]) => void;
  setModules: (modules: Module[]) => void;
  setOverallProgress: (progress: number) => void;
  preparePractice: (projectId: string) => Promise<Candidate[]>;
  refreshProblems: (projectId: string) => Promise<Problem[]>;
  updateProblemStatus: (problemId: string, status: Problem["status"]) => void;
  setCurrentProblem: (problem: ProblemDetail | null) => void;
  setEditorCode: (editorCode: string) => void;
  setSubmitResult: (submitResult: SubmitResult | null) => void;
  reset: () => void;
};

export const useProjectStore = create<ProjectState>((set) => ({
  currentProject: null,
  fileTree: [],
  extensionStats: {},
  problems: [],
  candidates: [],
  modules: [],
  overallProgress: 0,
  currentProblem: null,
  editorCode: "",
  submitResult: null,
  setCurrentProject: (project) => set({ currentProject: project }),
  setFileTree: (fileTree) => set({ fileTree }),
  setExtensionStats: (extensionStats) => set({ extensionStats }),
  setProblems: (problems) => set({ problems }),
  setCandidates: (candidates) => set({ candidates }),
  setModules: (modules) => set({ modules }),
  setOverallProgress: (overallProgress) => set({ overallProgress }),
  preparePractice: async (projectId) => {
    const response = await apiClient.post<PracticePrepareApiResponse>(`/projects/${projectId}/prepare`);
    const modules = (response.data.modules ?? []).map(normalizeModule);
    const candidates = (response.data.candidates ?? []).map(normalizeCandidate);
    set({ modules, candidates });
    return candidates;
  },
  refreshProblems: async (projectId) => {
    const response = await apiClient.get<ProblemListApiResponse>(`/projects/${projectId}/problems`);
    const problems = response.data.problems.map(normalizeProblem);
    const modules = (response.data.modules ?? []).map(normalizeModule);
    set({
      problems,
      modules,
      overallProgress: response.data.overall_progress ?? 0,
    });
    return problems;
  },
  updateProblemStatus: (problemId, status) =>
    set((state) => ({
      problems: state.problems.map((problem) => (problem.id === problemId ? { ...problem, status } : problem)),
      currentProblem:
        state.currentProblem?.id === problemId ? { ...state.currentProblem, status } : state.currentProblem,
    })),
  setCurrentProblem: (problem) => set({ currentProblem: problem }),
  setEditorCode: (editorCode) => set({ editorCode }),
  setSubmitResult: (submitResult) => set({ submitResult }),
  reset: () =>
    set({
      currentProject: null,
      fileTree: [],
      extensionStats: {},
      problems: [],
      candidates: [],
      modules: [],
      overallProgress: 0,
      currentProblem: null,
      editorCode: "",
      submitResult: null,
    }),
}));

type ProblemApiItem = Omit<Problem, "parentId" | "unlockDependencies" | "roleInProject" | "status"> & {
  status: Problem["status"] | "pending";
  parent_id?: string | null;
  unlock_dependencies?: string[] | null;
  role_in_project?: string | null;
};

type ModuleApiItem = {
  id: string;
  title: string;
  description: string;
  weight: number;
  problem_count: number;
  passed_count: number;
  progress: number;
};

export type CandidateApiItem = {
  source_path: string;
  symbol: string;
  difficulty?: "easy" | "medium" | "hard";
  problem_type?: "function_blank" | "function_partial";
  role_in_project?: string | null;
  depends_on?: string[];
  used_by?: string[];
  status: Candidate["status"];
  depth: number;
  generated: boolean;
  problem_id?: string | null;
  parent_id?: string | null;
};

type PracticePrepareApiResponse = {
  modules: ModuleApiItem[];
  candidates: CandidateApiItem[];
};

type ProblemListApiResponse = {
  problems: ProblemApiItem[];
  modules?: ModuleApiItem[];
  overall_progress?: number;
};

export function normalizeProblem(problem: ProblemApiItem): Problem {
  return {
    ...problem,
    status: problem.status === "pending" ? "unlocked" : problem.status,
    parentId: problem.parent_id ?? null,
    depth: problem.depth ?? 0,
    unlockDependencies: problem.unlock_dependencies ?? [],
    roleInProject: problem.role_in_project ?? null,
  };
}

export function normalizeProblemDetail(problem: ProblemDetail): ProblemDetail {
  return {
    ...problem,
    status: (problem.status as string) === "pending" ? "unlocked" : problem.status,
    parentId: problem.parent_id ?? problem.parentId ?? null,
    depth: problem.depth ?? 0,
    unlockDependencies: Array.isArray(problem.unlock_dependencies)
      ? problem.unlock_dependencies
      : parseDependencyIds(problem.unlock_dependencies),
    roleInProject: problem.role_in_project ?? problem.roleInProject ?? null,
  };
}

function normalizeModule(module: ModuleApiItem): Module {
  return {
    id: module.id,
    title: module.title,
    description: module.description,
    weight: module.weight,
    problemCount: module.problem_count,
    passedCount: module.passed_count,
    progress: module.progress,
  };
}

export function normalizeCandidate(candidate: CandidateApiItem): Candidate {
  return {
    sourcePath: candidate.source_path,
    symbol: candidate.symbol,
    difficulty: candidate.difficulty ?? "medium",
    problemType: candidate.problem_type ?? "function_blank",
    roleInProject: candidate.role_in_project ?? null,
    dependsOn: candidate.depends_on ?? [],
    usedBy: candidate.used_by ?? [],
    status: candidate.status,
    depth: candidate.depth ?? 0,
    generated: candidate.generated,
    problemId: candidate.problem_id ?? null,
    parentId: candidate.parent_id ?? null,
  };
}

function parseDependencyIds(value: string | string[] | null | undefined) {
  if (Array.isArray(value)) {
    return value;
  }
  if (!value) {
    return [];
  }
  try {
    const parsed = JSON.parse(value);
    return Array.isArray(parsed) ? parsed.map(String) : [];
  } catch {
    return [];
  }
}
