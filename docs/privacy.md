# Privacy

TransPDF is designed to keep your documents on your infrastructure. This page explains exactly what each backend does and how to verify it.

## What each backend does with your text

| Backend | Where your text goes | Private? |
|---|---|---|
| **Ollama** | The URL you configure (default: `http://localhost:11434`) | ✅ Your server |
| **LibreTranslate** | The URL you configure (default: `http://localhost:5000`) | ✅ Your server |
| **Google Translate** | `translate.googleapis.com` (Google's servers) | ❌ External |
| **Bing Translator** | `www.bing.com` (Bing's servers) | ❌ External |

The Google and Bing backends are zero-config fallbacks for quick local testing. They are labelled "Cloud" in the UI and the backend comparison table — do not use them for sensitive documents.

## What data is sent

For Ollama and LibreTranslate: **only the text blocks extracted from the PDF** — no filename, no metadata, no raw PDF bytes. Text is sent per block to the configured server URL and nowhere else.

For Google Translate: text blocks are sent to `translate.googleapis.com` in plain HTTP GET requests (Google's public translate API).

For Bing Translator: text blocks are sent to `www.bing.com` over HTTPS. The backend first loads Bing's translator page to obtain a short-lived session token, then submits each block to its web translation endpoint.

## Paperless-ngx webhook data flow

When running the webhook from source or Docker, the full chain is:

```
Paperless-ngx → webhook receiver → TransPDF → translation backend
```

- The webhook receives a document notification from Paperless (on your LAN)
- It downloads the PDF from your Paperless instance (LAN)
- It calls TransPDF, which extracts text and forwards it to the configured backend (Ollama or LibreTranslate on your LAN)
- The translated companion PDF is uploaded back to Paperless (LAN)

The webhook contacts the configured Paperless, TransPDF, and LibreTranslate endpoints. The privacy guarantee is the same as the main app — determined entirely by which endpoints and translation backend are configured.

## Verify it yourself

### Option 1 — tcpdump (Linux/macOS)

Capture all outbound traffic while translating a document. Replace `YOUR_LAN_IP` with the IP of your Ollama or LibreTranslate server.

```bash
sudo tcpdump -i any -n \
  'not host YOUR_LAN_IP and not port 22 and not port 53' \
  -w capture.pcap
```

Translate a document with the Ollama or LibreTranslate backend, then stop the capture. Open `capture.pcap` in Wireshark — if there are no external connections, nothing left your network.

### Option 2 — Read the source

The backends are short and self-contained:

- [`backends/ollama.py`](../backends/ollama.py) — ~65 lines; one `httpx.post` to `{url}/api/chat`
- [`backends/libretranslate.py`](../backends/libretranslate.py) — ~80 lines; one `httpx.post` to `{url}/translate`
- [`backends/google.py`](../backends/google.py) — ~30 lines; one `httpx.get` to `translate.googleapis.com`
- [`backends/bing.py`](../backends/bing.py) — Bing web session discovery plus `ttranslatev3` requests

No telemetry, no analytics, no crash reporting. The app does not call home.

## Securing the API

The REST API (`POST /api/translate`, `GET /api/config`, etc.) has no authentication by default. On localhost or a trusted private LAN this is acceptable. If you expose the service beyond a trusted network, protect it.

### Option A — API key (built-in)

Set the `PDF_TRANSLATE_API_KEY` environment variable. All API endpoints except `GET /api/health` will require an `Authorization: Bearer <key>` header. The Gradio UI is unaffected.

**Generate a key:**
```bash
openssl rand -hex 32
# example output: a3f8c1d2e4b5a6f7...
```

Or on Windows:
```powershell
[System.Convert]::ToBase64String([System.Security.Cryptography.RandomNumberGenerator]::GetBytes(32))
```

**Set it before starting from source (Linux/macOS):**
```bash
export PDF_TRANSLATE_API_KEY="your-key-here"
python app.py
```

**Windows PowerShell:**
```powershell
$env:PDF_TRANSLATE_API_KEY = "your-key-here"
python app.py
```

**Docker Compose:** add the key to the `transpdf` service and recreate it:

```yaml
services:
  transpdf:
    environment:
      - PDF_TRANSLATE_API_KEY=your-key-here
```

Keep the service's other environment entries when adding this setting, then run `docker compose up -d`. For a binary installation, set the same environment variable before launching the executable.

**Use it in curl:**
```bash
curl -X POST http://localhost:7860/api/translate \
  -H "Authorization: Bearer your-key-here" \
  -F "file=@document.pdf" \
  -F "source=de" -F "target=en" \
  --output translated.pdf
```

If `PDF_TRANSLATE_API_KEY` is not set, the API remains open (backward-compatible default).

### Option B — Reverse proxy with basic auth (protects UI too)

Put the service behind nginx, Caddy, or Authelia. This is the standard self-hosted approach and protects both the REST API and the Gradio UI.

Example Caddy config:
```
translate.yourdomain.com {
    basicauth {
        username <bcrypt-hash>
    }
    reverse_proxy localhost:7860
}
```

### Webhook receiver

The same pattern applies to the webhook receiver via `WEBHOOK_API_KEY`. If set, `POST /webhook` requires `Authorization: Bearer <key>`. Configure the matching header in your Paperless Workflow webhook settings.

```bash
# In the same terminal as the webhook's other environment variables:
export WEBHOOK_API_KEY="your-webhook-key-here"
python -m uvicorn webhook:app --host 0.0.0.0 --port 8081
```

On Windows PowerShell, use `$env:WEBHOOK_API_KEY = "your-webhook-key-here"`. Run the webhook from the `paperless_webhook` directory as described in its [README](../paperless_webhook/README.md).

For Docker, set `WEBHOOK_API_KEY` in the webhook service's `environment` section and recreate the container. See the [webhook Docker setup](../paperless_webhook/README.md#docker-deployment).
