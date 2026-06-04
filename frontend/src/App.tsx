import {
  Activity,
  Bell,
  BookOpen,
  CalendarClock,
  CheckCircle2,
  ClipboardList,
  FileSearch,
  FlaskConical,
  History,
  Headphones,
  Inbox,
  KeyRound,
  ListChecks,
  Loader2,
  Radar,
  RefreshCw,
  Save,
  Search as SearchIcon,
  Settings,
  ShieldAlert,
  Sparkles,
  Zap,
} from "lucide-react";
import type * as React from "react";
import { useEffect, useMemo, useState } from "react";
import { api } from "./lib/api";
import type { Briefing, BriefingItem, ExternalCall, Health, KnowledgeContext, KnowledgeItem, LearningPack, LLMSettingsPayload, RawEvent, Readiness, ReadinessItem, Reminder, Run, SearchResult, Task, TaskContext, TaskPatch } from "./types";

type Tab = "today" | "inbox" | "search" | "tasks" | "knowledge" | "podcasts" | "system";

const tabs: { id: Tab; label: string; icon: React.ComponentType<{ size?: number }> }[] = [
  { id: "today", label: "今日", icon: Activity },
  { id: "inbox", label: "收件箱", icon: Inbox },
  { id: "search", label: "搜索", icon: SearchIcon },
  { id: "tasks", label: "任务", icon: ListChecks },
  { id: "knowledge", label: "新知识", icon: Radar },
  { id: "podcasts", label: "播客", icon: Headphones },
  { id: "system", label: "系统", icon: Settings },
];

export default function App() {
  const [active, setActive] = useState<Tab>("today");
  const [health, setHealth] = useState<Health | null>(null);
  const [events, setEvents] = useState<RawEvent[]>([]);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [knowledge, setKnowledge] = useState<KnowledgeItem[]>([]);
  const [reminders, setReminders] = useState<Reminder[]>([]);
  const [podcasts, setPodcasts] = useState<LearningPack[]>([]);
  const [runs, setRuns] = useState<Run[]>([]);
  const [externalCalls, setExternalCalls] = useState<ExternalCall[]>([]);
  const [briefing, setBriefing] = useState<Briefing | null>(null);
  const [readiness, setReadiness] = useState<Readiness | null>(null);
  const [busy, setBusy] = useState<string>("");
  const [error, setError] = useState<string>("");
  const [selectedKnowledge, setSelectedKnowledge] = useState<number[]>([]);

  async function loadAll() {
    const results = await Promise.allSettled([
      api.health().then(setHealth),
      api.inbox().then(setEvents),
      api.tasks().then(setTasks),
      api.knowledge().then(setKnowledge),
      api.reminders().then(setReminders),
      api.podcasts().then(setPodcasts),
      api.runs().then(setRuns),
      api.externalCalls().then(setExternalCalls),
      api.todayBriefing().then(setBriefing),
      api.readiness().then(setReadiness),
    ]);
    const failed = results.filter((result) => result.status === "rejected");
    if (failed.length) {
      throw new Error(`${failed.length} 个数据接口刷新失败`);
    }
  }

  async function action(name: string, run: () => Promise<unknown>) {
    setBusy(name);
    setError("");
    try {
      await run();
      await loadAll();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      await loadAll().catch(() => undefined);
    } finally {
      setBusy("");
    }
  }

  function patchTask(taskId: number, patch: TaskPatch) {
    return action(`task-${taskId}`, () => api.patchTask(taskId, patch));
  }

  useEffect(() => {
    loadAll().catch((err) => setError(err instanceof Error ? err.message : String(err)));
    const timer = window.setInterval(() => {
      loadAll().catch(() => undefined);
    }, 30_000);
    return () => window.clearInterval(timer);
  }, []);

  const openTasks = tasks.filter((task) => task.status === "open");
  const urgentTasks = openTasks.filter((task) => task.priority_score >= 70);
  const pendingReminders = reminders.filter((reminder) => reminder.status === "pending");
  const newestKnowledge = knowledge.slice(0, 6);

  const activeView = useMemo(() => {
    if (active === "today") {
      return (
        <TodayView
          health={health}
          events={events}
          urgentTasks={urgentTasks}
          openTasks={openTasks}
          knowledge={newestKnowledge}
          reminders={pendingReminders}
          briefing={briefing}
          onPoll={() => action("poll", api.pollWechat)}
          onBackfill={() => action("backfill", api.backfillWechatToday)}
          onAttachments={() => action("attachments", api.scanWechatAttachments)}
          onOcrAttachments={() => action("ocr-attachments", api.ocrAttachments)}
          onAnalyze={() => action("analyze", api.analyze)}
          onRegenerateBriefing={() => action("briefing", api.regenerateBriefing)}
          onTodayLearningPack={() => action("today-pack", () => api.createTodayLearningPack(true, false))}
          onPatchTask={patchTask}
          busy={busy}
        />
      );
    }
    if (active === "inbox") {
      return (
        <InboxView
          events={events}
          onPoll={() => action("poll", api.pollWechat)}
          onScan={() => action("scan", () => api.scanInbox("inbox"))}
          onScanWechatFiles={() => action("wechat-files", () => api.scanInbox("wechat"))}
          onOcrAttachments={() => action("ocr-attachments", api.ocrAttachments)}
          busy={busy}
        />
      );
    }
    if (active === "tasks") {
      return <TasksView tasks={tasks} reminders={pendingReminders} onDispatch={() => action("dispatch", api.dispatchReminders)} onPatchTask={patchTask} busy={busy} />;
    }
    if (active === "search") {
      return <SearchView />;
    }
    if (active === "knowledge") {
      return (
        <KnowledgeView
          items={knowledge}
          selected={selectedKnowledge}
          setSelected={setSelectedKnowledge}
          onCreatePack={() => action("pack", () => api.createLearningPack(selectedKnowledge, false))}
          busy={busy}
        />
      );
    }
    if (active === "podcasts") {
      return (
        <PodcastsView
          packs={podcasts}
          onTodayPack={() => action("today-pack", () => api.createTodayLearningPack(true, false))}
          onRegenerate={(id) => action("researchcast", () => api.regenerateResearchCast(id))}
          onContinueAudio={() => action("researchcast-audio", () => api.continueResearchCastAudio(3))}
          busy={busy}
        />
      );
    }
    return (
      <SystemView
        health={health}
        runs={runs}
        externalCalls={externalCalls}
        readiness={readiness}
        onScanWorkspace={() => action("workspace", () => api.scanInbox("workspace"))}
        onRefresh={loadAll}
        busy={busy}
      />
    );
  }, [active, health, events, urgentTasks, openTasks, newestKnowledge, pendingReminders, briefing, tasks, knowledge, selectedKnowledge, busy, podcasts, runs, externalCalls, readiness]);

  return (
    <main className="min-h-screen bg-[#f6f7f3] text-[#1f2622]">
      <header className="border-b border-[#d7ddd4] bg-[#fbfcf7]">
        <div className="header-inner">
          <div>
            <h1 className="text-xl font-semibold tracking-normal">本地智能工作台</h1>
            <p className="text-sm text-[#68736a]">微信情报、任务监督、新知识播客</p>
          </div>
          <div className="flex items-center gap-2">
            <HealthPill ok={health?.wx.ok ?? false} label="微信" />
            <HealthPill ok={health?.llm.configured ?? false} label="模型" />
            <HealthPill ok={health?.notebooklm.ok ?? false} label="NotebookLM 实验" />
            <button className="icon-button" onClick={() => action("refresh", loadAll)} title="刷新">
              {busy === "refresh" ? <Loader2 size={18} className="animate-spin" /> : <RefreshCw size={18} />}
            </button>
          </div>
        </div>
      </header>
      <div className="app-shell">
        <nav className="side-nav panel p-2">
          {tabs.map((tab) => {
            const Icon = tab.icon;
            return (
              <button key={tab.id} className={`nav-item ${active === tab.id ? "nav-item-active" : ""}`} onClick={() => setActive(tab.id)}>
                <Icon size={18} />
                <span>{tab.label}</span>
              </button>
            );
          })}
        </nav>
        <section className="min-w-0">
          {error ? <div className="mb-4 rounded-md border border-[#cf6f43] bg-[#fff1e8] px-4 py-3 text-sm text-[#8a3c1e]">{error}</div> : null}
          {activeView}
        </section>
      </div>
    </main>
  );
}

function TodayView(props: {
  health: Health | null;
  events: RawEvent[];
  urgentTasks: Task[];
  openTasks: Task[];
  knowledge: KnowledgeItem[];
  reminders: Reminder[];
  briefing: Briefing | null;
  onPoll: () => void;
  onBackfill: () => void;
  onAttachments: () => void;
  onOcrAttachments: () => void;
  onAnalyze: () => void;
  onRegenerateBriefing: () => void;
  onTodayLearningPack: () => void;
  onPatchTask: (taskId: number, patch: TaskPatch) => void;
  busy: string;
}) {
  return (
    <div className="space-y-5">
      <div className="metric-grid">
        <Metric icon={Inbox} label="今日事件" value={props.events.length} />
        <Metric icon={ShieldAlert} label="高优先级" value={props.urgentTasks.length} tone="warm" />
        <Metric icon={Radar} label="新知识" value={props.knowledge.length} tone="green" />
        <Metric icon={Bell} label="待提醒" value={props.reminders.length} tone="blue" />
      </div>
      <BriefingPanel briefing={props.briefing} onRegenerate={props.onRegenerateBriefing} busy={props.busy === "briefing"} />
      <div className="toolbar">
        <button className="primary-button" onClick={props.onPoll} disabled={props.busy === "poll"}>
          <Zap size={17} /> 微信增量抓取
        </button>
        <button className="secondary-button" onClick={props.onBackfill} disabled={props.busy === "backfill"}>
          <History size={17} /> 今日全量回看
        </button>
        <button className="secondary-button" onClick={props.onAttachments} disabled={props.busy === "attachments"}>
          <FileSearch size={17} /> 图片附件抓取
        </button>
        <button className="secondary-button" onClick={props.onOcrAttachments} disabled={props.busy === "ocr-attachments"}>
          <FileSearch size={17} /> 图片 OCR
        </button>
        <button className="secondary-button" onClick={props.onAnalyze} disabled={props.busy === "analyze"}>
          <Sparkles size={17} /> 分析未处理事件
        </button>
        <button className="secondary-button" onClick={props.onTodayLearningPack} disabled={props.busy === "today-pack"}>
          {props.busy === "today-pack" ? <Loader2 size={17} className="animate-spin" /> : <BookOpen size={17} />} 生成今日 ResearchCast
        </button>
      </div>
      <div className="today-grid">
        <Panel title="现在最该盯住" icon={ListChecks}>
          <ItemList
            items={props.urgentTasks.length ? props.urgentTasks : props.openTasks.slice(0, 6)}
            render={(task) => <TaskRow task={task} onPatchTask={props.onPatchTask} busy={props.busy} />}
            empty="还没有开放任务。"
          />
        </Panel>
        <Panel title="系统状态" icon={Activity}>
          <StatusLine label="微信深抓" ok={props.health?.wx.ok ?? false} detail={props.health?.wx.message ?? "未检测"} />
          <StatusLine label="图片 OCR" ok={props.health?.ocr?.ok ?? false} detail={props.health?.ocr?.message ?? "未检测"} />
          <StatusLine label="模型 API" ok={props.health?.llm.configured ?? false} detail={props.health?.llm.model ?? "未配置"} />
          <StatusLine label="NotebookLM 实验" ok={props.health?.notebooklm.ok ?? false} detail={props.health?.notebooklm.message ?? "未检测"} />
          <StatusLine label="轮询间隔" ok detail={`${props.health?.scheduler.wx_poll_seconds ?? 90}s`} />
        </Panel>
      </div>
      <Panel title="今天冒出的新知识" icon={Radar}>
        <ItemList items={props.knowledge} render={(item) => <KnowledgeRow item={item} selectable={false} />} empty="还没有识别到新知识。" />
      </Panel>
    </div>
  );
}

function BriefingPanel({ briefing, onRegenerate, busy }: { briefing: Briefing | null; onRegenerate: () => void; busy: boolean }) {
  const sections: { title: string; items?: BriefingItem[] }[] = [
    { title: "先做", items: briefing?.focus },
    { title: "决策", items: briefing?.decisions },
    { title: "追问", items: briefing?.followups },
    { title: "等待", items: briefing?.waiting },
    { title: "学习", items: briefing?.learning },
    { title: "风险", items: briefing?.risks },
  ].filter((section) => section.items?.length);

  return (
    <Panel title="今日作战简报" icon={ClipboardList}>
      <div className="briefing-head">
        <div className="min-w-0">
          <div className="row-title">{briefing?.title || "今日作战简报"}</div>
          <p className="row-body">{briefing?.summary || "还没有生成简报。刷新后会根据开放任务、提醒和新知识生成今日行动顺序。"}</p>
          <div className="row-meta">
            {briefing?.day || "今天"} / {briefing?.updated_at || "未生成"}
          </div>
        </div>
        <button className="secondary-button" onClick={onRegenerate} disabled={busy}>
          {busy ? <Loader2 size={17} className="animate-spin" /> : <RefreshCw size={17} />} 刷新简报
        </button>
      </div>
      {sections.length ? (
        <div className="briefing-grid">
          {sections.map((section) => (
            <div className="briefing-section" key={section.title}>
              <div className="briefing-section-title">{section.title}</div>
              {(section.items || []).slice(0, 4).map((item, index) => (
                <div className="briefing-item" key={`${section.title}-${index}`}>
                  <div className="flex items-start justify-between gap-3">
                    <div className="row-title">{item.title}</div>
                    <span className={`priority ${item.priority.toLowerCase()}`}>{item.priority}</span>
                  </div>
                  {item.body ? <p className="row-body">{item.body}</p> : null}
                  <div className="row-meta">{item.action}</div>
                </div>
              ))}
            </div>
          ))}
        </div>
      ) : null}
    </Panel>
  );
}

function InboxView({
  events,
  onPoll,
  onScan,
  onScanWechatFiles,
  onOcrAttachments,
  busy,
}: {
  events: RawEvent[];
  onPoll: () => void;
  onScan: () => void;
  onScanWechatFiles: () => void;
  onOcrAttachments: () => void;
  busy: string;
}) {
  return (
    <div className="space-y-5">
      <div className="toolbar">
        <button className="primary-button" onClick={onPoll} disabled={busy === "poll"}>
          <Zap size={17} /> 微信增量抓取
        </button>
        <button className="secondary-button" onClick={onScan} disabled={busy === "scan"}>
          <FileSearch size={17} /> 扫描 inbox 文档
        </button>
        <button className="secondary-button" onClick={onScanWechatFiles} disabled={busy === "wechat-files"}>
          <FileSearch size={17} /> 扫描微信文件
        </button>
        <button className="secondary-button" onClick={onOcrAttachments} disabled={busy === "ocr-attachments"}>
          <FileSearch size={17} /> 图片 OCR
        </button>
      </div>
      <Panel title="实时收件箱" icon={Inbox}>
        <ItemList items={events} render={(event) => <EventRow event={event} />} empty="暂无事件。把文档放进 data/inbox 或运行微信抓取。" />
      </Panel>
    </div>
  );
}

function SearchView() {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SearchResult[]>([]);
  const [searching, setSearching] = useState(false);
  const [message, setMessage] = useState("输入关键词后搜索微信原文、文档、任务、新知识和播客讲稿。");

  async function runSearch(event?: React.FormEvent) {
    event?.preventDefault();
    const value = query.trim();
    if (!value) {
      setResults([]);
      setMessage("输入关键词后搜索微信原文、文档、任务、新知识和播客讲稿。");
      return;
    }
    setSearching(true);
    setMessage("");
    try {
      const next = await api.search(value, 80);
      setResults(next);
      setMessage(next.length ? `找到 ${next.length} 条结果` : "没有找到匹配结果");
    } catch (err) {
      setMessage(err instanceof Error ? err.message : String(err));
      setResults([]);
    } finally {
      setSearching(false);
    }
  }

  return (
    <div className="space-y-5">
      <Panel title="统一搜索" icon={SearchIcon}>
        <form className="search-form" onSubmit={runSearch}>
          <input className="input search-input" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="例如：电池、摄像模组、报价、认证、供应商名称" />
          <button className="primary-button" disabled={searching} type="submit">
            {searching ? <Loader2 size={17} className="animate-spin" /> : <SearchIcon size={17} />} 搜索
          </button>
        </form>
        <div className="search-summary">{message}</div>
      </Panel>
      <Panel title="搜索结果" icon={FileSearch}>
        <ItemList items={results} render={(result) => <SearchResultRow result={result} />} empty="还没有搜索结果。" />
      </Panel>
    </div>
  );
}

function SearchResultRow({ result }: { result: SearchResult }) {
  const typeLabels: Record<SearchResult["type"], string> = {
    event: "原文",
    document: "文档",
    task: "任务",
    knowledge: "知识",
    podcast: "播客",
  };
  return (
    <div className="list-row">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="row-title">{result.title}</div>
          <div className="row-meta">
            {result.source}
            {result.conversation ? ` / ${result.conversation}` : ""}
            {result.happened_at ? ` / ${result.happened_at}` : ""}
          </div>
        </div>
        <span className={`result-type result-${result.type}`}>{typeLabels[result.type]}</span>
      </div>
      <p className="row-body">{result.snippet}</p>
    </div>
  );
}

function TasksView({
  tasks,
  reminders,
  onDispatch,
  onPatchTask,
  busy,
}: {
  tasks: Task[];
  reminders: Reminder[];
  onDispatch: () => void;
  onPatchTask: (taskId: number, patch: TaskPatch) => void;
  busy: string;
}) {
  const openTasks = tasks.filter((task) => task.status === "open");
  const closedTasks = tasks.filter((task) => task.status !== "open").slice(0, 12);
  return (
    <div className="tasks-grid">
      <div className="space-y-5">
        <Panel title="开放任务" icon={ListChecks}>
          <ItemList items={openTasks} render={(task) => <TaskRow task={task} onPatchTask={onPatchTask} busy={busy} />} empty="暂无开放任务。" />
        </Panel>
        <Panel title="最近完成 / 关闭" icon={CheckCircle2}>
          <ItemList items={closedTasks} render={(task) => <TaskRow task={task} onPatchTask={onPatchTask} busy={busy} />} empty="暂无已完成任务。" />
        </Panel>
      </div>
      <Panel title="提醒队列" icon={Bell}>
        <button className="secondary-button mb-3 w-full justify-center" onClick={onDispatch} disabled={busy === "dispatch"}>
          <Bell size={17} /> 立即派发到期提醒
        </button>
        <ItemList
          items={reminders}
          render={(reminder) => (
            <div className="list-row">
              <div className="row-title">{reminder.title}</div>
              <div className="row-meta">{reminder.remind_at}</div>
              <p className="row-body">{reminder.body}</p>
            </div>
          )}
          empty="暂无待提醒事项。"
        />
      </Panel>
    </div>
  );
}

function KnowledgeView(props: {
  items: KnowledgeItem[];
  selected: number[];
  setSelected: (ids: number[]) => void;
  onCreatePack: () => void;
  busy: string;
}) {
  const toggle = (id: number) => {
    props.setSelected(props.selected.includes(id) ? props.selected.filter((x) => x !== id) : [...props.selected, id]);
  };
  return (
    <div className="space-y-5">
      <div className="toolbar">
        <button className="primary-button" disabled={!props.selected.length || props.busy === "pack"} onClick={props.onCreatePack}>
          <BookOpen size={17} /> 生成研究博客+音频
        </button>
      </div>
      <Panel title="新知识雷达" icon={Radar}>
        <ItemList
          items={props.items}
          render={(item) => <KnowledgeRow item={item} selectable selected={props.selected.includes(item.id)} onToggle={() => toggle(item.id)} />}
          empty="暂无新知识。"
        />
      </Panel>
    </div>
  );
}

function PodcastsView({
  packs,
  onTodayPack,
  onRegenerate,
  onContinueAudio,
  busy,
}: {
  packs: LearningPack[];
  onTodayPack: () => void;
  onRegenerate: (id: number) => void;
  onContinueAudio: () => void;
  busy: string;
}) {
  return (
    <div className="space-y-5">
      <div className="toolbar">
        <button className="primary-button" onClick={onTodayPack} disabled={busy === "today-pack"}>
          {busy === "today-pack" ? <Loader2 size={17} className="animate-spin" /> : <BookOpen size={17} />} 生成今日 ResearchCast
        </button>
        <button className="secondary-button" onClick={onContinueAudio} disabled={busy === "researchcast-audio"}>
          {busy === "researchcast-audio" ? <Loader2 size={17} className="animate-spin" /> : <RefreshCw size={17} />} 续跑待完成音频
        </button>
      </div>
      <Panel title="学习中心" icon={Headphones}>
        <ItemList
          items={packs}
          render={(pack) => <PodcastRow pack={pack} onRegenerate={onRegenerate} busy={busy} />}
          empty="还没有学习包。先生成今日 ResearchCast，或在新知识页选择知识项生成研究博客+音频。"
        />
      </Panel>
    </div>
  );
}

function PodcastRow({ pack, onRegenerate, busy }: { pack: LearningPack; onRegenerate: (id: number) => void; busy: string }) {
  return (
    <div className="list-row">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="row-title">{pack.title}</div>
          <div className="row-meta">
            {pack.engine || "legacy"} / research: {pack.research_status || pack.status} / audio: {pack.audio_status || pack.status}
          </div>
          {pack.audio_status === "audio_processing" ? (
            <div className="status-note">MiniMax 音频正在后台生成；博客和讲稿已经可用，音频完成后会自动出现。</div>
          ) : pack.audio_status === "audio_failed" ? (
            <div className="status-warning">{pack.audio_error || "音频生成失败，但博客和讲稿仍可用。"}</div>
          ) : null}
        </div>
        <button className="icon-button" title="重新生成 ResearchCast" onClick={() => onRegenerate(pack.id)} disabled={busy === "researchcast"}>
          {busy === "researchcast" ? <Loader2 size={17} className="animate-spin" /> : <RefreshCw size={17} />}
        </button>
      </div>
      {pack.local_audio_url ? (
        <div className="audio-box notebooklm-audio">
          <div className="audio-label">ResearchCast 学习音频</div>
          <audio className="audio-player" controls preload="metadata" src={pack.local_audio_url} />
          <div className="audio-links">
            <a href={pack.local_audio_url} download>
              下载音频
            </a>
          </div>
        </div>
      ) : null}
      <div className="audio-links mt-3">
        {pack.blog_url ? (
          <a href={pack.blog_url} target="_blank" rel="noreferrer">
            打开研究博客
          </a>
        ) : null}
        {pack.script_url ? (
          <a href={pack.script_url} target="_blank" rel="noreferrer">
            打开讲稿
          </a>
        ) : null}
        {pack.source_manifest_url ? (
          <a href={pack.source_manifest_url} target="_blank" rel="noreferrer">
            来源清单
          </a>
        ) : null}
      </div>
      <p className="row-body whitespace-pre-line">{pack.script_text.slice(0, 420)}</p>
      <div className="row-meta">{pack.local_audio_path || "音频尚未完成，先看博客和讲稿"}</div>
    </div>
  );
}

function LLMSettingsPanel({ health, onRefresh }: { health: Health | null; onRefresh: () => Promise<void> }) {
  const [baseUrl, setBaseUrl] = useState("");
  const [model, setModel] = useState("");
  const [embeddingModel, setEmbeddingModel] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [working, setWorking] = useState("");
  const [message, setMessage] = useState("");

  useEffect(() => {
    if (!health) {
      return;
    }
    setBaseUrl(health.llm.base_url || "");
    setModel(health.llm.model || "");
    setEmbeddingModel(health.llm.embedding_model || "");
  }, [health]);

  const payload = (includeEmptyKey = false): LLMSettingsPayload => ({
    base_url: baseUrl,
    model,
    embedding_model: embeddingModel,
    ...(apiKey || includeEmptyKey ? { api_key: apiKey } : {}),
  });

  async function save() {
    setWorking("save-llm");
    setMessage("");
    try {
      const result = await api.updateLLM(payload());
      setMessage(result.message || "模型配置已保存");
      setApiKey("");
      await onRefresh();
    } catch (err) {
      setMessage(err instanceof Error ? err.message : String(err));
    } finally {
      setWorking("");
    }
  }

  async function test() {
    setWorking("test-llm");
    setMessage("");
    try {
      const result = await api.testLLM(payload());
      setMessage(result.message || (result.ok ? "模型连通正常" : "模型连通失败"));
    } catch (err) {
      setMessage(err instanceof Error ? err.message : String(err));
    } finally {
      setWorking("");
    }
  }

  return (
    <Panel title="模型 API" icon={KeyRound}>
      <div className="settings-grid">
        <label className="field">
          <span>Base URL</span>
          <input className="input" value={baseUrl} onChange={(event) => setBaseUrl(event.target.value)} placeholder="https://api.openai.com/v1" />
        </label>
        <label className="field">
          <span>模型</span>
          <input className="input" value={model} onChange={(event) => setModel(event.target.value)} placeholder="gpt-4o-mini" />
        </label>
        <label className="field">
          <span>Embedding 模型</span>
          <input className="input" value={embeddingModel} onChange={(event) => setEmbeddingModel(event.target.value)} placeholder="可选" />
        </label>
        <label className="field">
          <span>API Key</span>
          <input className="input" value={apiKey} onChange={(event) => setApiKey(event.target.value)} placeholder={health?.llm.api_key_set ? "已保存，留空不覆盖" : "sk-..."} type="password" />
        </label>
      </div>
      <div className="toolbar mt-4">
        <button className="primary-button" onClick={save} disabled={working === "save-llm"}>
          {working === "save-llm" ? <Loader2 size={17} className="animate-spin" /> : <Save size={17} />} 保存
        </button>
        <button className="secondary-button" onClick={test} disabled={working === "test-llm"}>
          {working === "test-llm" ? <Loader2 size={17} className="animate-spin" /> : <FlaskConical size={17} />} 测试连通
        </button>
        <span className="form-hint">{health?.llm.api_key_set ? "API Key 已保存" : "API Key 未保存"}</span>
      </div>
      {message ? <div className="form-message">{message}</div> : null}
    </Panel>
  );
}

function SystemView({
  health,
  runs,
  externalCalls,
  readiness,
  onScanWorkspace,
  onRefresh,
  busy,
}: {
  health: Health | null;
  runs: Run[];
  externalCalls: ExternalCall[];
  readiness: Readiness | null;
  onScanWorkspace: () => void;
  onRefresh: () => Promise<void>;
  busy: string;
}) {
  return (
    <div className="space-y-5">
      <LLMSettingsPanel health={health} onRefresh={onRefresh} />
      <ReadinessPanel readiness={readiness} />
      <Panel title="系统健康" icon={Settings}>
        <div className="status-grid">
          <StatusLine label="wx-cli" ok={health?.wx.ok ?? false} detail={health?.wx.message ?? "未检测"} />
          <StatusLine label="图片 OCR" ok={health?.ocr?.ok ?? false} detail={health?.ocr?.message ?? "未检测"} />
          <StatusLine label="NotebookLM 实验" ok={health?.notebooklm.ok ?? false} detail={health?.notebooklm.message ?? "未检测"} />
          <StatusLine label="LLM" ok={health?.llm.configured ?? false} detail={health?.llm.base_url ?? "未配置"} />
          <StatusLine label="Inbox" ok detail={health?.paths.inbox_dir ?? ""} />
          <StatusLine label="微信回看" ok detail={`会话 ${limitText(health?.wechat_limits?.backfill_session_limit)} / 消息 ${limitText(health?.wechat_limits?.backfill_history_limit)}`} />
          <StatusLine label="微信附件" ok detail={`会话 ${limitText(health?.wechat_limits?.attachment_session_limit)} / 图片 ${limitText(health?.wechat_limits?.attachment_limit)}`} />
          <StatusLine label="微信文件" ok detail={`文件 ${limitText(health?.wechat_limits?.file_scan_limit)}`} />
          <StatusLine label="登录自启动" ok={health?.startup?.ok ?? false} detail={health?.startup?.installed ? `${health.startup.task_name} / ${health.startup.state}` : "未安装，运行 install_startup_task.ps1"} />
          <StatusLine label="启动同步" ok={health?.scheduler.auto_startup_sync ?? false} detail={(health?.scheduler.auto_startup_sync ?? false) ? "已启用" : "已关闭"} />
          <StatusLine label="自动简报" ok={health?.scheduler.auto_daily_briefing ?? false} detail={(health?.scheduler.auto_daily_briefing ?? false) ? "15 分钟刷新" : "已关闭"} />
          <StatusLine label="自动音频" ok={health?.scheduler.auto_daily_learning_audio ?? false} detail={(health?.scheduler.auto_daily_learning_audio ?? false) ? "每日 9/13/18 点" : "已关闭"} />
        </div>
        {health?.startup?.installed ? null : (
          <div className="form-message">
            要让微信抓取和提醒每天自动运行，安装一次登录自启动：
            <code className="ml-2">.\install_startup_task.ps1</code>
          </div>
        )}
        {health?.ocr?.ok ? null : (
          <div className="form-message">
            要识别微信截图里的规格/报价文字，请安装 Tesseract OCR，并确认命令行可运行：
            <code className="ml-2">tesseract --version</code>
          </div>
        )}
        <button className="secondary-button mt-4" onClick={onScanWorkspace} disabled={busy === "workspace"}>
          <FileSearch size={17} /> 扫描本地工作区文档
        </button>
      </Panel>
      <Panel title="模型外发审计" icon={ShieldAlert}>
        <ItemList
          items={externalCalls}
          render={(call) => (
            <div className="list-row">
              <div className="flex items-start justify-between gap-3">
                <div className="row-title">{call.purpose}</div>
                <span className={`status-chip ${call.status === "ok" ? "status-open" : "status-done"}`}>{call.status}</span>
              </div>
              <div className="row-meta">{call.provider} / {call.model} / {call.base_url}</div>
              <p className="row-body">{call.content_summary}</p>
              {call.response_summary ? <div className="row-meta">返回：{call.response_summary}</div> : null}
              {call.error ? <div className="row-meta">错误：{call.error}</div> : null}
              <div className="row-meta">{call.started_at} / {call.prompt_chars} chars</div>
            </div>
          )}
          empty="还没有模型外发记录。配置模型 API 后，任务分析、简报和学习讲稿调用会出现在这里。"
        />
      </Panel>
      <Panel title="运行日志" icon={Activity}>
        <ItemList
          items={runs}
          render={(run) => (
            <div className="list-row">
              <div className="row-title">{run.kind} / {run.status}</div>
              <div className="row-meta">{run.started_at}</div>
              <p className="row-body">{run.message}</p>
            </div>
          )}
          empty="暂无运行日志。"
        />
      </Panel>
    </div>
  );
}

function ReadinessPanel({ readiness }: { readiness: Readiness | null }) {
  return (
    <Panel title="交付就绪清单" icon={ClipboardList}>
      <div className="readiness-head">
        <div>
          <div className="row-title">{readiness?.complete ? "核心链路已可验收" : "核心链路还需要补齐"}</div>
          <p className="row-body">{readiness?.summary || "正在检测本地工作台配置和数据链路。"}</p>
        </div>
        <div className={`readiness-score ${readiness?.complete ? "readiness-score-ok" : ""}`}>{readiness?.score ?? 0}%</div>
      </div>
      <div className="readiness-list">
        {(readiness?.items || []).map((item) => (
          <ReadinessRow item={item} key={item.id} />
        ))}
      </div>
    </Panel>
  );
}

function ReadinessRow({ item }: { item: ReadinessItem }) {
  const done = item.status === "done";
  return (
    <div className={`readiness-row ${done ? "readiness-row-done" : ""}`}>
      <div className="flex min-w-0 items-start gap-3">
        <span className={`readiness-dot ${done ? "readiness-dot-done" : ""}`}>{done ? <CheckCircle2 size={14} /> : <ShieldAlert size={14} />}</span>
        <div className="min-w-0 flex-1">
          <div className="flex items-start justify-between gap-3">
            <div className="row-title">{item.label}</div>
            <span className={`status-chip ${done ? "status-open" : item.required ? "priority p1" : "status-done"}`}>{done ? "完成" : item.required ? "必须" : "建议"}</span>
          </div>
          <div className="row-meta">{item.detail}</div>
          {!done ? <p className="row-body">{item.action}</p> : null}
          {!done && item.command ? <code className="command-chip">{item.command}</code> : null}
        </div>
      </div>
    </div>
  );
}

function Panel({ title, icon: Icon, children }: { title: string; icon: React.ComponentType<{ size?: number }>; children: React.ReactNode }) {
  return (
    <section className="panel">
      <div className="panel-title">
        <Icon size={18} />
        <span>{title}</span>
      </div>
      {children}
    </section>
  );
}

function Metric({ icon: Icon, label, value, tone = "default" }: { icon: React.ComponentType<{ size?: number }>; label: string; value: number; tone?: string }) {
  return (
    <div className={`metric metric-${tone}`}>
      <Icon size={19} />
      <div>
        <div className="metric-value">{value}</div>
        <div className="metric-label">{label}</div>
      </div>
    </div>
  );
}

function ItemList<T>({ items, render, empty }: { items: T[]; render: (item: T) => React.ReactNode; empty: string }) {
  if (!items.length) {
    return <div className="empty">{empty}</div>;
  }
  return <div className="list">{items.map((item, index) => <div key={index}>{render(item)}</div>)}</div>;
}

function TaskRow({ task, onPatchTask, busy }: { task: Task; onPatchTask: (taskId: number, patch: TaskPatch) => void; busy: string }) {
  const disabled = busy === `task-${task.id}`;
  const isOpen = task.status === "open";
  const [contextOpen, setContextOpen] = useState(false);
  const [context, setContext] = useState<TaskContext | null>(null);
  const [contextBusy, setContextBusy] = useState(false);
  const [contextMessage, setContextMessage] = useState("");

  async function toggleContext() {
    if (contextOpen) {
      setContextOpen(false);
      return;
    }
    setContextOpen(true);
    if (context) {
      return;
    }
    setContextBusy(true);
    setContextMessage("");
    try {
      setContext(await api.taskContext(task.id));
    } catch (err) {
      setContextMessage(err instanceof Error ? err.message : String(err));
    } finally {
      setContextBusy(false);
    }
  }

  async function copyDraft() {
    if (!context?.suggested_reply) {
      return;
    }
    await navigator.clipboard.writeText(context.suggested_reply);
    setContextMessage("草稿已复制");
  }

  return (
    <div className={`list-row ${isOpen ? "" : "muted-row"}`}>
      <div className="flex items-start justify-between gap-3">
        <div className="row-title">{task.title}</div>
        <div className="flex flex-none flex-wrap justify-end gap-2">
          <span className={`status-chip ${isOpen ? "status-open" : "status-done"}`}>{isOpen ? "open" : task.status}</span>
          <span className={`priority ${task.priority_label.toLowerCase()}`}>{task.priority_label}</span>
        </div>
      </div>
      <p className="row-body">{task.next_action}</p>
      <div className="row-meta">{task.summary}</div>
      <div className="row-meta">{task.due_at ? `提醒：${task.due_at}` : "未设置提醒时间"}</div>
      <div className="task-actions">
        {isOpen ? (
          <>
            <button className="mini-button" onClick={() => onPatchTask(task.id, { status: "done" })} disabled={disabled} title="完成任务">
              <CheckCircle2 size={15} /> 完成
            </button>
            <button className="mini-button" onClick={() => onPatchTask(task.id, { due_at: taskDueAt(0) })} disabled={disabled} title="今晚提醒">
              <CalendarClock size={15} /> 今天提醒
            </button>
            <button className="mini-button" onClick={() => onPatchTask(task.id, { due_at: taskDueAt(1) })} disabled={disabled} title="明天上午提醒">
              <Bell size={15} /> 明天提醒
            </button>
            <button className="mini-button" onClick={toggleContext} disabled={contextBusy} title="查看原始证据和回复草稿">
              {contextBusy ? <Loader2 size={15} className="animate-spin" /> : <FileSearch size={15} />} 证据/草稿
            </button>
          </>
        ) : (
          <>
            <button className="mini-button" onClick={() => onPatchTask(task.id, { status: "open" })} disabled={disabled} title="重新打开任务">
              <RefreshCw size={15} /> 重新打开
            </button>
            <button className="mini-button" onClick={toggleContext} disabled={contextBusy} title="查看原始证据和回复草稿">
              {contextBusy ? <Loader2 size={15} className="animate-spin" /> : <FileSearch size={15} />} 证据/草稿
            </button>
          </>
        )}
      </div>
      {contextOpen ? (
        <div className="context-panel">
          {contextMessage ? <div className="form-message">{contextMessage}</div> : null}
          {context ? (
            <>
              <div className="context-head">
                <div>
                  <div className="row-title">建议回复草稿</div>
                  <div className="row-meta">{context.confidence === "source_linked" ? "已绑定原始证据" : "按关键词兜底匹配证据"}</div>
                </div>
                <button className="mini-button" onClick={copyDraft} title="复制草稿">
                  <ClipboardList size={15} /> 复制
                </button>
              </div>
              <pre className="draft-box">{context.suggested_reply}</pre>
              <div className="context-grid">
                <div>
                  <div className="context-title">下一轮该问</div>
                  <ul className="question-list">
                    {context.next_questions.map((question) => (
                      <li key={question}>{question}</li>
                    ))}
                  </ul>
                </div>
                <div>
                  <div className="context-title">原始证据</div>
                  <ItemList items={context.source_events.slice(0, 5)} render={(event) => <EvidenceRow event={event} />} empty="没有找到原始事件。" />
                </div>
              </div>
              {context.related_knowledge.length ? (
                <div className="mt-3">
                  <div className="context-title">相关新知识</div>
                  <ItemList items={context.related_knowledge} render={(item) => <KnowledgeRow item={item} selectable={false} />} empty="暂无相关知识。" />
                </div>
              ) : null}
            </>
          ) : contextBusy ? (
            <div className="empty">正在读取证据和草稿...</div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

function taskDueAt(dayOffset: number) {
  const due = new Date();
  due.setDate(due.getDate() + dayOffset);
  due.setHours(dayOffset === 0 ? 20 : 9, dayOffset === 0 ? 0 : 30, 0, 0);
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${due.getFullYear()}-${pad(due.getMonth() + 1)}-${pad(due.getDate())}T${pad(due.getHours())}:${pad(due.getMinutes())}:00`;
}

function KnowledgeRow({
  item,
  selectable,
  selected,
  onToggle,
}: {
  item: KnowledgeItem;
  selectable: boolean;
  selected?: boolean;
  onToggle?: () => void;
}) {
  const [contextOpen, setContextOpen] = useState(false);
  const [context, setContext] = useState<KnowledgeContext | null>(null);
  const [contextBusy, setContextBusy] = useState(false);
  const [contextMessage, setContextMessage] = useState("");

  async function toggleContext() {
    if (contextOpen) {
      setContextOpen(false);
      return;
    }
    setContextOpen(true);
    if (context) {
      return;
    }
    setContextBusy(true);
    setContextMessage("");
    try {
      setContext(await api.knowledgeContext(item.id));
    } catch (err) {
      setContextMessage(err instanceof Error ? err.message : String(err));
    } finally {
      setContextBusy(false);
    }
  }

  async function copyQuestions() {
    if (!context?.supplier_questions.length) {
      return;
    }
    await navigator.clipboard.writeText(context.supplier_questions.map((question, index) => `${index + 1}. ${question}`).join("\n"));
    setContextMessage("追问清单已复制");
  }

  return (
    <div className={`list-row w-full text-left ${selected ? "selected-row" : ""}`}>
      <div className="flex items-start justify-between gap-3">
        <div className="row-title">{item.title}</div>
        <span className="domain">{item.domain}</span>
      </div>
      <p className="row-body">{item.work_relevance}</p>
      <div className="row-meta">{item.summary}</div>
      <div className="task-actions">
        {selectable ? (
          <button className="mini-button" onClick={onToggle} title={selected ? "取消选择" : "选择生成播客"}>
            <CheckCircle2 size={15} /> {selected ? "已选择" : "选入播客"}
          </button>
        ) : null}
        <button className="mini-button" onClick={toggleContext} disabled={contextBusy} title="查看学习上下文">
          {contextBusy ? <Loader2 size={15} className="animate-spin" /> : <BookOpen size={15} />} 学习/证据
        </button>
      </div>
      {contextOpen ? (
        <div className="context-panel">
          {contextMessage ? <div className="form-message">{contextMessage}</div> : null}
          {context ? (
            <>
              <div className="context-head">
                <div>
                  <div className="row-title">学习上下文</div>
                  <div className="row-meta">{context.confidence === "source_linked" ? "已绑定原始证据" : "按关键词兜底匹配证据"}</div>
                </div>
                <button className="mini-button" onClick={copyQuestions} title="复制供应商追问">
                  <ClipboardList size={15} /> 复制追问
                </button>
              </div>
              <p className="study-note">{context.study_note}</p>
              <div className="context-grid">
                <div>
                  <div className="context-title">我需要懂什么</div>
                  <ul className="question-list">
                    {context.learning_goals.map((goal) => (
                      <li key={goal}>{goal}</li>
                    ))}
                  </ul>
                </div>
                <div>
                  <div className="context-title">下一轮问供应商</div>
                  <ul className="question-list">
                    {context.supplier_questions.map((question) => (
                      <li key={question}>{question}</li>
                    ))}
                  </ul>
                </div>
              </div>
              <div>
                <div className="context-title">术语速记</div>
                <div className="term-grid">
                  {context.glossary.map((term) => (
                    <div className="term-card" key={term.term}>
                      <div className="row-title">{term.term}</div>
                      <p className="row-body">{term.meaning}</p>
                    </div>
                  ))}
                </div>
              </div>
              <div className="context-grid">
                <div>
                  <div className="context-title">播客提纲</div>
                  <ul className="question-list">
                    {context.podcast_outline.map((line) => (
                      <li key={line}>{line}</li>
                    ))}
                  </ul>
                </div>
                <div>
                  <div className="context-title">原始证据</div>
                  <ItemList items={context.source_events.slice(0, 5)} render={(event) => <EvidenceRow event={event} />} empty="没有找到原始事件。" />
                </div>
              </div>
              {context.related_tasks.length ? (
                <div>
                  <div className="context-title">相关任务</div>
                  <ItemList
                    items={context.related_tasks}
                    render={(task) => (
                      <div className="evidence-row">
                        <div className="row-title">{task.title}</div>
                        <p className="row-body">{task.next_action}</p>
                        <div className="row-meta">{task.priority_label} / {task.status}</div>
                      </div>
                    )}
                    empty="暂无相关任务。"
                  />
                </div>
              ) : null}
            </>
          ) : contextBusy ? (
            <div className="empty">正在读取学习上下文...</div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

function EventRow({ event }: { event: RawEvent }) {
  return (
    <div className="list-row">
      <div className="flex items-start justify-between gap-3">
        <div className="row-title">{event.title}</div>
        <span className="source">{event.source}</span>
      </div>
      <p className="row-body">{event.body}</p>
      <div className="row-meta">{event.conversation || event.author || "无会话"} / {event.happened_at || event.received_at}</div>
    </div>
  );
}

function EvidenceRow({ event }: { event: RawEvent }) {
  return (
    <div className="evidence-row">
      <div className="flex items-start justify-between gap-2">
        <div className="row-title">{event.title}</div>
        <span className="source">{event.source}</span>
      </div>
      <p className="row-body">{event.body}</p>
      <div className="row-meta">{event.conversation || event.author || "无会话"} / {event.happened_at || event.received_at}</div>
    </div>
  );
}

function HealthPill({ ok, label }: { ok: boolean; label: string }) {
  return (
    <span className={`health-pill ${ok ? "health-ok" : "health-bad"}`}>
      {ok ? <CheckCircle2 size={14} /> : <ShieldAlert size={14} />}
      {label}
    </span>
  );
}

function StatusLine({ label, ok, detail }: { label: string; ok: boolean; detail: string }) {
  return (
    <div className="status-line">
      <HealthPill ok={ok} label={label} />
      <span className="truncate text-sm text-[#5f6a62]">{detail}</span>
    </div>
  );
}

function limitText(value?: number) {
  if (value === 0) {
    return "全部";
  }
  if (value === undefined || value === null) {
    return "未检测";
  }
  return String(value);
}

