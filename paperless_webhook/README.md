# Paperless-ngx Integration

Automatically translate documents as they are ingested into [Paperless-ngx](https://docs.paperless-ngx.com/). A companion translated PDF is uploaded alongside the original and linked via custom fields — fully local, no cloud.

## How it works

A lightweight webhook receiver runs from Python source or Docker alongside Paperless. When Paperless ingests a new document, its Workflow engine calls the webhook. The receiver detects the document language, sends the PDF to [TransPDF](../README.md), and uploads the translated companion back to Paperless. The original is never modified.

```
Paperless ingests document → OCR completes
        ↓
Paperless Workflow fires POST /webhook
        ↓
  detect language from OCR content (LibreTranslate /detect)
  ├── not <source-lang> → skip (logged)
  └── <source-lang> → download PDF from Paperless
                    → POST to TransPDF
                    → upload translated PDF to Paperless
                    → link both docs via Document Link custom fields
                    → log result
```

The webhook responds 200 immediately — translation runs in the background and never blocks document consumption.

## Prerequisites

- Paperless-ngx v2.x
- Docker, or Python 3.11+ for running the receiver from source
- [TransPDF](../README.md) running and reachable
- LibreTranslate running and reachable (used for language detection)

## One-time setup in Paperless

### 1. Create the custom field

Go to **Settings → Custom Fields** and create one field:

| Name | Data type |
|---|---|
| `translation` | Document Link |

**Document Link** fields render as clickable links in the Paperless UI. Paperless automatically creates the reverse link — when the companion gets `translation=[original]`, the original is automatically updated with `translation=[companion]`. One field, both directions.

The webhook logs a warning if this field is missing but still uploads the translation — the document just won't be linked.

### 2. Generate a Paperless API token

Go to **My Profile → API Token** and copy the token. Set it as the `PAPERLESS_API_TOKEN` environment variable before starting the webhook (see below).

### 3. Configure a Paperless Workflow

Go to **Settings → Workflows → Add Workflow**:

| Setting | Value |
|---|---|
| Name | Auto-translate |
| Trigger | Document added |
| Filter | *(leave empty to process all documents)* |
| Action | Webhook |
| URL | `http://<webhook-host>:8081/webhook` |

Replace `<webhook-host>` with the hostname or IP of the machine running the receiver, reachable from Paperless. If Paperless runs in a container, `localhost` refers to that container; use the receiver host's reachable address.

## Docker deployment

Add this service to your Paperless Compose file, adjusting the build path and service URLs for your setup:

```yaml
services:
  transpdf-webhook:
    build: ./TransPDF/paperless_webhook
    image: transpdf-paperless-webhook:local
    ports:
      - "8081:8081"
    volumes:
      - transpdf_webhook_data:/data
    environment:
      PAPERLESS_URL: http://paperless-ngx:8000
      PAPERLESS_API_TOKEN: ${PAPERLESS_API_TOKEN}
      PDF_TRANSLATE_URL: http://<TransPDF-host>:7860
      LIBRETRANSLATE_URL: http://<libretranslate-host>:5000
      TRANSLATE_SOURCE_LANG: de
      TRANSLATE_TARGET_LANG: en
      TRANSLATE_LOG_FILE: /data/translate.log
      PDF_TRANSLATE_MERGE_BLOCKS: "true"
    restart: unless-stopped

volumes:
  transpdf_webhook_data:
```

Set `PAPERLESS_API_TOKEN` in the Compose `.env` file, then run `docker compose up -d --build`. To use a published image, replace the `build` and `image` entries with `image: ghcr.io/yyuanc/transpdf-paperless-webhook:latest` after publishing it with the Docker workflow.

When Paperless shares this Compose network, set its Workflow URL to `http://transpdf-webhook:8081/webhook`. For other network arrangements, use the receiver host's reachable address. All configured service URLs must be reachable from the webhook container; `localhost` refers to the webhook container itself.

## Run from source

Start TransPDF using any supported installation method in the [main README](../README.md#快速开始), and start or connect to LibreTranslate for language detection.

From the repository root, create a separate virtual environment for the webhook:

```bash
cd paperless_webhook
python -m venv .venv
```

Activate it with `source .venv/bin/activate` on Linux/macOS, or `.\.venv\Scripts\Activate.ps1` in Windows PowerShell. Then install dependencies:

```bash
python -m pip install -r requirements.txt
```

Set the environment variables in the same terminal. Linux/macOS example:

```bash
export PAPERLESS_URL="http://localhost:8000"
export PAPERLESS_API_TOKEN="your-paperless-token"
export PDF_TRANSLATE_URL="http://localhost:7860"
export LIBRETRANSLATE_URL="http://localhost:5000"
export TRANSLATE_SOURCE_LANG="de"
export TRANSLATE_TARGET_LANG="en"
export TRANSLATE_LOG_FILE="./translate.log"
export PDF_TRANSLATE_MERGE_BLOCKS="true"
```

Windows PowerShell example:

```powershell
$env:PAPERLESS_URL = "http://localhost:8000"
$env:PAPERLESS_API_TOKEN = "your-paperless-token"
$env:PDF_TRANSLATE_URL = "http://localhost:7860"
$env:LIBRETRANSLATE_URL = "http://localhost:5000"
$env:TRANSLATE_SOURCE_LANG = "de"
$env:TRANSLATE_TARGET_LANG = "en"
$env:TRANSLATE_LOG_FILE = "./translate.log"
$env:PDF_TRANSLATE_MERGE_BLOCKS = "true"
```

Replace the URLs with addresses reachable from the receiver if the services run on other machines. The receiver does not load `.env` files automatically. Set `TRANSLATE_LOG_FILE` to a writable local path; its parent directory must already exist.

Start the receiver and keep this terminal running:

```bash
python -m uvicorn webhook:app --host 0.0.0.0 --port 8081
```

## Environment variables

### Paperless connection

| Variable | Default | Description |
|---|---|---|
| `PAPERLESS_URL` | `http://paperless-ngx:8000` | Paperless base URL — use its Compose service name on the same network, or a reachable host address |
| `PAPERLESS_API_TOKEN` | — | Paperless API token — set in the receiver's environment |

### Translation pipeline

| Variable | Default | Description |
|---|---|---|
| `PDF_TRANSLATE_URL` | `http://localhost:7860` | TransPDF base URL |
| `TRANSLATE_SOURCE_LANG` | `auto` | Source language (ISO 639-1, e.g. `de`, `fr`, `nl`). Use `auto` to translate every document regardless of detected language. |
| `TRANSLATE_TARGET_LANG` | `en` | Target language (ISO 639-1) |
| `TRANSLATE_OUTPUT` | `pdf` | What to upload: `pdf` (translated PDF), `sbs` (side-by-side bilingual PDF), or `both` (one of each — two companions per original) |
| `TRANSLATE_TIMEOUT` | `300` | Seconds to wait for TransPDF. The orphaned job is cancelled automatically on timeout. |
| `TRANSLATE_LOG_FILE` | `/data/translate.log` | Append-only JSON log. Docker mounts `/data` for persistence; source installs should override with a writable local path such as `./translate.log`. The parent directory must exist. |

### TransPDF API settings

These are passed as request parameters on every call to TransPDF. They configure how TransPDF processes and translates the PDF. Backend settings come from the caller; translation threads and Chinese font style use the saved global TransPDF defaults.

| Variable | Default | Description |
|---|---|---|
| `LIBRETRANSLATE_URL` | `http://localhost:5000` | LibreTranslate base URL — used for **language detection** by the receiver |
| `PDF_TRANSLATE_SERVICE` | `LibreTranslate` | Translation backend passed to TransPDF: `LibreTranslate`, `Ollama`, `Google`, or `Bing` |
| `PDF_TRANSLATE_LIBRE_URL` | *(= `LIBRETRANSLATE_URL`)* | LibreTranslate URL passed to TransPDF for **translation**. Defaults to `LIBRETRANSLATE_URL`. Set separately if TransPDF reaches LT via a different network path. |
| `PDF_TRANSLATE_LIBRE_KEY` | *(empty)* | LibreTranslate API key (if your instance requires one) |
| `PDF_TRANSLATE_OLLAMA_URL` | `http://localhost:11434` | Ollama base URL (only relevant when `PDF_TRANSLATE_SERVICE=Ollama`) |
| `PDF_TRANSLATE_OLLAMA_MODEL` | *(TransPDF default)* | Ollama translation model name |
| `PDF_TRANSLATE_OLLAMA_KEY` | *(empty)* | Ollama API key (optional) |
| `PDF_TRANSLATE_MERGE_BLOCKS` | `false` | Merge split word-level text fragments before translation. **Enable for DTP/InDesign PDFs** or Paperless-archived PDFs with fragmented text. |
| `PDF_TRANSLATE_FORCE_OCR` | `false` | Ignore the text layer and OCR every page. Use for scanned PDFs with no text layer. |
| `PDF_TRANSLATE_ALLOW_WRAP` | `false` | Collapse line breaks before translation (helps with hard-wrapped paragraphs) |
| `PDF_TRANSLATE_FILTER_ICONS` | `true` | Strip single-character icon glyphs from mixed text blocks |
| `PDF_TRANSLATE_DETECT_TABLES` | `true` | Detect table cells and apply shrink-to-fit text fitting |

## Paperless tags

The webhook creates and manages two tags in Paperless automatically — no manual setup needed.

| Tag | Applied to | Purpose |
|---|---|---|
| `auto-translated` | Companion documents (translated / SBS PDFs) | Prevents the companion from being re-translated. Applied at upload time so it is present before Paperless OCR completes and the Workflow fires — race-condition-free. |
| `translation-failed` | Original documents | Signals that translation failed. Cleared automatically on a successful retry. Filter `tag:translation-failed` in Paperless to find documents that need attention. |

To retry a failed document: open it in Paperless → **Actions → Run Workflow**. On success the `translation-failed` tag is removed and the companion appears linked via `translation`.

## Backfill — translating existing documents

The webhook only processes new documents as they arrive. To translate documents already in Paperless, run `backfill.py` from source in the `paperless_webhook` directory or inside the running webhook container.

It applies the same guards as the webhook: skips companions tagged `auto-translated`, skips originals that already have a `translation` link, and skips documents in the wrong language. Any translations it performs write to the same `TRANSLATE_LOG_FILE` and apply the same `translation-failed` tag on failure.

### Run

Activate the webhook virtual environment and set the same environment variables shown above in the terminal used for backfill:

```bash
# Preview first — no changes:
python backfill.py --start 1 --end 20 --dry-run

# Translate after reviewing the dry-run output:
python backfill.py --start 1 --end 20
```

For Docker deployment, the running container already has the configured environment:

```bash
docker compose exec transpdf-webhook python backfill.py --start 1 --end 20 --dry-run
docker compose exec transpdf-webhook python backfill.py --start 1 --end 20
```

### Output

The script prints progress to the terminal as it runs:

```
TransPDF backfill — IDs 1–20 (20 to check)

  · [     3] skip: lang=en (want de)
  · [     7] skip: already translated
  → [    12] "Mietvertrag 2024"
  → [    15] "Arztbrief Dr. Müller"

Scan complete: 2 to translate, 2 skipped, 16 not found, 0 errors

[1/2] doc 12: 'Mietvertrag 2024' ... ✓  → [EN] Mietvertrag 2024
[2/2] doc 15: 'Arztbrief Dr. Müller' ... ✓  → [EN] Arztbrief Dr. Müller

Done. translated=2 skipped=2 errors=0
```

Failures appear as `✗ failed: <reason>` in the terminal and as `"action": "failed"` entries in `TRANSLATE_LOG_FILE`.

### Options

| Flag | Default | Description |
|---|---|---|
| `--start N` | — | First document ID to check (required) |
| `--end N` | — | Last document ID to check, inclusive (required) |
| `--dry-run` | off | Scan and report eligibility without translating |
| `--delay SECONDS` | `2.0` | Pause between translations — reduce to speed up, increase if TransPDF is slow |

### Tips

- **Start small.** Run `--dry-run --start 1 --end 50` first to see what qualifies before committing.
- **Work in batches.** Paperless IDs are not contiguous — there will be many "not found" gaps. Running 1–800 with `--dry-run` first tells you exactly how many documents will actually be translated.
- **Check the log after.** `grep '"action": "failed"' ./translate.log` shows anything that didn't make it through (use your configured `TRANSLATE_LOG_FILE` path).
- **Safe to re-run.** Already-translated documents are always skipped — running twice does nothing extra.

## Output format

`TRANSLATE_OUTPUT` controls what is uploaded to Paperless alongside the original:

| Value | Uploaded | Paperless indexes | Notes |
|---|---|---|---|
| `pdf` (default) | Translated PDF | Target language text | Portrait, clean, readable |
| `sbs` | Side-by-side PDF | Both source and target language | Landscape; both languages visible |
| `both` | Translated PDF + SBS PDF | Both | Two companion documents; TransPDF called twice |

## Log format

One JSON line per document, appended to `TRANSLATE_LOG_FILE`.

```jsonc
// Successful translation
{"action": "translated", "source_id": 142, "source_title": "Document title", "uploaded": [{"fmt": "pdf", "id": 201, "title": "[EN] Document title"}], "ts": "2026-05-27T10:03:00Z"}

// Skipped — not the configured source language
{"action": "skipped", "source_id": 143, "source_title": "Document title", "reason": "lang=en", "ts": "..."}

// Skipped — already translated (idempotency guard on original)
{"action": "skipped", "source_id": 142, "source_title": "Document title", "reason": "already translated", "ts": "..."}

// Skipped — companion document (prevents infinite loop on sbs/both output)
{"action": "skipped", "source_id": 201, "source_title": "[EN] Document title", "reason": "auto-translated companion", "ts": "..."}

// Timed out
{"action": "timeout", "source_id": 144, "source_title": "Document title", "reason": "exceeded 300s", "ts": "..."}
```

To review failures: `grep '"action": "failed"\|"action": "timeout"' ./translate.log` (use your configured `TRANSLATE_LOG_FILE` path).

## Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Health check — returns `{"status": "ok"}` |
| `POST` | `/webhook` | Paperless-ngx document webhook receiver |

## Troubleshooting

**Translation triggered for documents in the wrong language**
Language detection failed (LibreTranslate unreachable) or `TRANSLATE_SOURCE_LANG=auto`. Check `LIBRETRANSLATE_URL` and that the service is running.

**Custom field not set after translation**
The `translation` field was not found in Paperless. Complete the one-time setup above. To link an already-translated document manually: set `translation` in the Paperless UI on the companion using the original's `source_id` from the log.

**Webhook not firing**
Verify the Paperless Workflow configuration. The webhook URL (`http://<webhook-host>:8081/webhook`) must be reachable from Paperless. If Paperless runs in a container, use the receiver host's reachable address; `localhost` refers to the Paperless container itself.

**Timeout on large documents**
Increase `TRANSLATE_TIMEOUT`. Check `GET <TransPDF-url>/api/health` to see if a job is still running or was cancelled.

**TransPDF queue full (HTTP 429)**
Another translation is already running or queued. The webhook logs the failure. The document will not be retried automatically — re-trigger the Paperless Workflow for that document after the queue clears.
