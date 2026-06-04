# Local ResearchCast Workbench

Local ResearchCast Workbench is a local-first personal intelligence desk for busy operators. It ingests daily work information, extracts tasks and risks, detects new knowledge, then turns selected topics into a research blog, source manifest, listening script, and MP3 learning audio.

The project is designed to run on your own machine. Runtime data, chat exports, attachments, model keys, generated audio, and local databases stay under `data/` and are ignored by Git.

## What It Does

- Ingests messages and files from local sources, including optional WeChat capture through `wx-cli`.
- Parses PDF, DOCX, XLSX, Markdown, TXT, CSV, and OCR text from images.
- Extracts tasks, next actions, waiting items, reminders, risks, and new knowledge.
- Builds a daily action briefing.
- Generates ResearchCast learning packs: Markdown research blog, evidence/source manifest, audio script, and MP3.
- Uses OpenAI-compatible chat APIs for analysis; MiniMax async TTS or Edge TTS for audio.
- Keeps NotebookLM as an optional experiment plugin, not a blocker.
- Records model-call audit metadata without storing full outbound prompts in the audit table.

## Privacy Defaults

The repository intentionally ignores:

- `.env`
- `.venv/`
- `data/`
- `frontend/node_modules/`
- `frontend/dist/`
- SQLite databases, logs, generated audio, attachments, OCR output, and local inbox files

Before publishing or sharing your fork, run:

```powershell
git status --short
rg -n "api_key|xwechat|C:\\Users|data/" .
```

Do not commit your `.env`, local databases, chat records, attachments, or generated podcasts.

## Install

```powershell
cd C:\path\to\local-researchcast-workbench
.\install.ps1
```

The installer creates `.venv`, installs Python dependencies, installs frontend dependencies, and attempts to install `@jackwener/wx-cli`.

## Configure

Copy `.env.example` to `.env`, then set your model API:

```env
LLM_BASE_URL=https://your-openai-compatible-host/v1
LLM_API_KEY=<your-api-key>
LLM_MODEL=your-model
TTS_PROVIDER=minimax
MINIMAX_TTS_MODEL=speech-2.8-hd
MINIMAX_TTS_VOICE=audiobook_male_1
WORKSPACE_ROOT=..
```

You can also configure and test the model from the system page after the app starts.

## Optional WeChat Setup

Keep WeChat logged in, then run PowerShell as Administrator:

```powershell
cd C:\path\to\local-researchcast-workbench
.\init_wechat_admin.ps1
```

Check:

```powershell
wx sessions --json
wx new-messages --json
```

If `wx-cli` reports stale or unknown shards, run:

```powershell
wx init --force
```

## Run

Backend:

```powershell
.\start_backend.ps1
```

Frontend:

```powershell
.\start_frontend.ps1
```

Open:

```text
http://127.0.0.1:5173
```

API docs:

```text
http://127.0.0.1:8787/docs
```

To start both hidden:

```powershell
.\start_hidden.ps1
```

## Startup

To run the workbench automatically on Windows logon:

```powershell
.\install_startup_task.ps1
```

This registers a Windows scheduled task when possible, otherwise it creates a user Startup-folder fallback. To remove it:

```powershell
.\uninstall_startup_task.ps1
```

## Daily Use

- Put documents into `data/inbox/`, then scan inbox documents.
- Poll messages or run a daily backfill if `wx-cli` is configured.
- Run analysis to extract tasks and knowledge items.
- Select knowledge items and generate "research blog + audio", or generate the daily ResearchCast.
- If async TTS is still processing, the blog and script are delivered first; audio can continue in the background.

## Tests

Backend:

```powershell
cd backend
python -m unittest discover -s tests
```

Frontend:

```powershell
cd frontend
npm run build
```

## Notes

- This is a personal local tool, not a hosted SaaS.
- It does not automatically send messages on your behalf.
- WeChat support depends on the unofficial `wx-cli` adapter and can break when the local WeChat storage format changes.
- NotebookLM support depends on an unofficial client and is kept outside the main ResearchCast flow.

## License

MIT
