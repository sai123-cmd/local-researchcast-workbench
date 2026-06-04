export type RawEvent = {
  id: number;
  source: string;
  source_type: string;
  title: string;
  body: string;
  author?: string;
  conversation?: string;
  happened_at?: string;
  received_at: string;
  processed_at?: string;
  provenance?: Record<string, unknown>;
};

export type Task = {
  id: number;
  title: string;
  summary: string;
  status: string;
  priority_score: number;
  priority_label: string;
  project: string;
  owner: string;
  next_action: string;
  due_at?: string;
  source_event_ids?: number[];
  provenance?: Record<string, unknown>;
  created_at: string;
  updated_at?: string;
};

export type TaskPatch = {
  status?: string;
  priority_score?: number;
  next_action?: string;
  due_at?: string;
};

export type KnowledgeItem = {
  id: number;
  title: string;
  domain: string;
  summary: string;
  novelty_score: number;
  urgency: string;
  work_relevance: string;
  key_questions?: string[];
  followups?: string[];
  source_event_ids?: number[];
  provenance?: Record<string, unknown>;
  created_at: string;
};

export type Reminder = {
  id: number;
  title: string;
  body: string;
  status: string;
  remind_at: string;
  channel: string;
};

export type LearningPack = {
  id: number;
  title: string;
  status: string;
  script_text: string;
  engine?: string;
  research_status?: string;
  blog_path?: string;
  blog_url?: string;
  source_manifest_url?: string;
  audio_status?: string;
  audio_error?: string;
  tts_task_id?: string;
  local_audio_path?: string;
  local_audio_url?: string;
  script_url?: string;
  notebooklm_audio_path?: string;
  notebooklm_audio_url?: string;
  notebooklm_status: string;
  created_at: string;
};

export type SearchResult = {
  type: "event" | "document" | "task" | "knowledge" | "podcast";
  id: number;
  title: string;
  snippet: string;
  source: string;
  conversation?: string;
  happened_at?: string;
  score: number;
};

export type TaskContext = {
  task: Task;
  source_events: RawEvent[];
  related_knowledge: KnowledgeItem[];
  next_questions: string[];
  suggested_reply: string;
  confidence: "source_linked" | "fallback_search";
};

export type GlossaryTerm = {
  term: string;
  meaning: string;
};

export type KnowledgeContext = {
  item: KnowledgeItem;
  source_events: RawEvent[];
  related_tasks: Task[];
  glossary: GlossaryTerm[];
  learning_goals: string[];
  supplier_questions: string[];
  podcast_outline: string[];
  study_note: string;
  confidence: "source_linked" | "fallback_search";
};

export type Run = {
  id: number;
  kind: string;
  status: string;
  started_at: string;
  finished_at?: string;
  message?: string;
};

export type ExternalCall = {
  id: number;
  provider: string;
  purpose: string;
  model: string;
  base_url: string;
  status: string;
  prompt_chars: number;
  content_summary: string;
  response_summary: string;
  error: string;
  started_at: string;
  finished_at?: string;
};

export type BriefingItem = {
  title: string;
  body: string;
  action: string;
  priority: string;
};

export type Briefing = {
  id: number;
  day: string;
  title: string;
  summary: string;
  focus?: BriefingItem[];
  decisions?: BriefingItem[];
  followups?: BriefingItem[];
  waiting?: BriefingItem[];
  learning?: BriefingItem[];
  risks?: BriefingItem[];
  source_task_ids?: number[];
  source_knowledge_ids?: number[];
  source_reminder_ids?: number[];
  provenance?: Record<string, unknown>;
  updated_at: string;
};

export type LLMSettingsPayload = {
  base_url?: string;
  api_key?: string;
  model?: string;
  embedding_model?: string;
};

export type ApiResponse<T = unknown> = {
  ok: boolean;
  data?: T;
  message: string;
};

export type ReadinessItem = {
  id: string;
  label: string;
  status: "done" | "todo" | "warning";
  required: boolean;
  detail: string;
  action: string;
  command?: string;
};

export type Readiness = {
  complete: boolean;
  status: "ready" | "needs_setup";
  score: number;
  summary: string;
  items: ReadinessItem[];
  blockers: ReadinessItem[];
};

export type Health = {
  paths: Record<string, string>;
  llm: { configured: boolean; base_url: string; model: string; embedding_model?: string; api_key_set?: boolean };
  wx: { installed: boolean; ok: boolean; message: string; sample_count?: number; meta?: Record<string, unknown> };
  ocr: { installed: boolean; ok: boolean; provider: string; bin?: string; lang?: string; message: string };
  notebooklm: { installed: boolean; ok: boolean; status?: string; message: string };
  scheduler: {
    wx_poll_seconds: number;
    auto_startup_sync?: boolean;
    auto_daily_briefing?: boolean;
    auto_daily_learning_audio?: boolean;
  };
  startup?: { installed: boolean; ok: boolean; state: string; task_name: string; message: string };
  wechat_limits?: Record<string, number>;
  counts: Record<string, number>;
};

