import type { ApiResponse, Briefing, ExternalCall, Health, KnowledgeContext, KnowledgeItem, LearningPack, LLMSettingsPayload, RawEvent, Readiness, Reminder, Run, SearchResult, Task, TaskContext, TaskPatch } from "../types";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
  });
  if (!response.ok) {
    throw new Error(`${response.status} ${response.statusText}`);
  }
  const payload = await response.json();
  if (payload && typeof payload === "object" && "ok" in payload && payload.ok === false) {
    throw new Error(String(payload.message || "Request failed"));
  }
  return payload as T;
}

export const api = {
  health: () => request<Health>("/api/settings"),
  readiness: () => request<Readiness>("/api/settings/readiness"),
  inbox: () => request<RawEvent[]>("/api/inbox?limit=120"),
  tasks: () => request<Task[]>("/api/tasks"),
  taskContext: (taskId: number) => request<TaskContext>(`/api/tasks/${taskId}/context`),
  patchTask: (taskId: number, patch: TaskPatch) =>
    request<ApiResponse<Task>>(`/api/tasks/${taskId}`, { method: "PATCH", body: JSON.stringify(patch) }),
  knowledge: () => request<KnowledgeItem[]>("/api/knowledge"),
  knowledgeContext: (knowledgeId: number) => request<KnowledgeContext>(`/api/knowledge/${knowledgeId}/context`),
  reminders: () => request<Reminder[]>("/api/reminders"),
  podcasts: () => request<LearningPack[]>("/api/podcasts"),
  runs: () => request<Run[]>("/api/runs"),
  externalCalls: () => request<ExternalCall[]>("/api/audit/external-calls?limit=40"),
  search: (query: string, limit = 60) => request<SearchResult[]>(`/api/search?q=${encodeURIComponent(query)}&limit=${limit}`),
  todayBriefing: () => request<Briefing>("/api/briefings/today"),
  regenerateBriefing: () => request<ApiResponse<Briefing>>("/api/briefings/today/regenerate", { method: "POST" }),
  createTodayLearningPack: (regenerate = true, useNotebookLM = false) =>
    request<ApiResponse<LearningPack>>(`/api/podcasts/today-learning-pack?regenerate=${regenerate}&use_notebooklm=${useNotebookLM}`, {
      method: "POST",
    }),
  syncNotebookLM: (force = true) =>
    request<ApiResponse<{ pending: number; attempted: number; updated: number; ready: number; auth_required: boolean }>>(
      `/api/podcasts/sync-notebooklm?force=${force}`,
      { method: "POST" },
    ),
  updateLLM: (payload: LLMSettingsPayload) =>
    request<ApiResponse<Health>>("/api/settings/llm", { method: "PUT", body: JSON.stringify(payload) }),
  testLLM: (payload: LLMSettingsPayload) =>
    request<ApiResponse<{ elapsed_ms?: number; sample?: string; status_code?: number }>>("/api/settings/llm/test", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  pollWechat: () => request<{ ok: boolean; data: unknown; message: string }>("/api/inbox/poll-wechat", { method: "POST" }),
  backfillWechatToday: () =>
    request<{ ok: boolean; data: unknown; message: string }>("/api/inbox/backfill-wechat-today", { method: "POST" }),
  scanWechatAttachments: () =>
    request<{ ok: boolean; data: unknown; message: string }>("/api/inbox/scan-wechat-attachments", { method: "POST" }),
  ocrAttachments: () =>
    request<{ ok: boolean; data: unknown; message: string }>("/api/inbox/ocr-attachments", { method: "POST" }),
  scanInbox: (scope: "inbox" | "workspace" | "wechat") =>
    request<{ ok: boolean; data: unknown }>("/api/inbox/scan-documents?scope=" + scope, { method: "POST" }),
  analyze: () => request<{ ok: boolean; data: unknown }>("/api/inbox/analyze", { method: "POST" }),
  dispatchReminders: () => request<{ ok: boolean; data: unknown }>("/api/reminders/dispatch", { method: "POST" }),
  createLearningPack: (knowledge_item_ids: number[], use_notebooklm = false) =>
    request<{ ok: boolean; data: LearningPack }>("/api/knowledge/learning-pack", {
      method: "POST",
      body: JSON.stringify({ knowledge_item_ids, use_notebooklm }),
    }),
  generateNotebookLM: (packId: number) =>
    request<{ ok: boolean; data: unknown }>(`/api/podcasts/${packId}/notebooklm`, { method: "POST" }),
  regenerateResearchCast: (packId: number) =>
    request<{ ok: boolean; data: LearningPack }>(`/api/podcasts/${packId}/regenerate-researchcast`, { method: "POST" }),
  continueResearchCastAudio: (limit = 3) =>
    request<ApiResponse<{ pending: number; attempted: number; ready: number; processing: number; failed: number }>>(
      `/api/podcasts/researchcast-audio?limit=${limit}`,
      { method: "POST" },
    ),
};

