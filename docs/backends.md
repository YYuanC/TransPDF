# 🔧 Setting up translation backends

TransPDF supports four translation backends and can run from source, Docker, or precompiled binaries (see the [README](../README.md#快速开始)). **LibreTranslate** is included as a separate service in the repository's Docker Compose setup. With source or binary installations, install **LibreTranslate** or **Ollama** separately, or connect to an existing instance. **Google Translate** and **Bing Translator** work without API keys but send your PDF text to their cloud services; use them only for quick tests or non-sensitive documents.

## 🦙 Ollama

Ollama runs open-source LLMs locally. Your documents never leave your machine or LAN.

→ [ollama.com](https://ollama.com) for downloads and the full model library.

### 🖥️ Local install (same machine as TransPDF)

**1. Install Ollama**

```bash
# macOS / Linux
curl -fsSL https://ollama.com/install.sh | sh
```

Windows: download from [ollama.com/download](https://ollama.com/download).

**2. Pull a translation model**

```bash
ollama pull translategemma:latest
```

Recommended models:

| Model | Size | Notes |
|-------|------|-------|
| `translategemma:latest` | ~4 GB | Good general-purpose translation; tested with TransPDF |
| `qwen2.5:7b` | ~4 GB | Strong multilingual support |
| `mistral:7b` | ~4 GB | Good European language coverage |

**3. Configure TransPDF**

In the UI, expand **Backend settings** and set:
- **Translation service** → `Ollama`
- **Ollama URL** → `http://localhost:11434`
- **Model** → `translategemma:latest`

Click **Test connection**, then **Save configuration**.

For an Ollama instance on another machine, set **Ollama URL** to its reachable address, such as `http://your-server-ip:11434`.

### Docker networking

When TransPDF runs in Docker, `localhost` refers to the TransPDF container itself. For Ollama on the host, use a host address reachable from the container (for example `http://host.docker.internal:11434` on Docker Desktop); Ollama must listen on that reachable interface. For Ollama in another container on the same network, use its service name, such as `http://ollama:11434`. Pull the model on the Ollama instance you connect to.

### ⚙️ OCR with Ollama vision models

When **Force OCR** is enabled, Ollama is used as a vision model to read scanned pages:

```bash
ollama pull glm-ocr   # 1.1 GB — purpose-built OCR, low VRAM
```

Set **OCR engine** → `Ollama (vision)` and **OCR model** → `glm-ocr` in Backend settings.

> `minicpm-v` (7.6 GB) is an alternative but requires significantly more VRAM. Stick with `glm-ocr` on machines with <8 GB VRAM.

**Performance reference:**

| Hardware | Expected speed |
|----------|---------------|
| CPU only | 1–5 min/page |
| Apple Silicon (M1/M2/M3) | 10–30 s/page (Metal) |
| NVIDIA GPU | 2–10 s/page |

## 🟩 LibreTranslate

LibreTranslate is a free, open-source translation API — fully self-hosted, no data leaves your network.

→ [libretranslate.com](https://libretranslate.com) · [GitHub](https://github.com/LibreTranslate/LibreTranslate)

### Docker Compose

From the TransPDF repository root:

```bash
docker compose up -d --build
docker compose logs -f libretranslate
```

The bundled LibreTranslate service downloads its language models on first startup. The Compose file sets `--req-limit 0`, restricts models with `--load-only`, and persists them in `libretranslate_data`. In the TransPDF UI, select **LibreTranslate**, set the URL to `http://libretranslate:5000`, then test the connection and save configuration. The default engine remains Bing until you change it.

For TransPDF running from source or a binary on the same host, the exposed LibreTranslate port is available at `http://localhost:5000`. To use an external instance instead, follow the comments in [`docker-compose.yml`](../docker-compose.yml) to remove the bundled service, its volume, and the `depends_on` entry, and set `LIBRETRANSLATE_URL` to the external address.

### 🖥️ Local install

Install LibreTranslate in a separate virtual environment using the Python version supported by [LibreTranslate](https://github.com/LibreTranslate/LibreTranslate). Keep it separate from TransPDF's dependencies.

```bash
python -m pip install libretranslate
libretranslate --req-limit 0
```

Set the URL to `http://localhost:5000` in TransPDF, or the reachable address of an existing instance. Click **Test connection**, then **Save configuration**.

**`--req-limit 0`** disables the per-minute request limit for long documents. Optionally use **`--load-only en,de,fr,es,it,nl,pt,ru,pl,ar,zh,ja,ko,tr`** to restrict downloaded language models. First startup may take a few minutes while models download.

### 🔑 API keys (optional)

By default, no key is required. To enforce one:

```
libretranslate --api-keys
```

Then set the key in **Backend settings → LibreTranslate API key**.

### ⚡ Rate limiting and throughput

| Setting | Default | Purpose |
|---------|---------|---------|
| `LIBRETRANSLATE_BLOCK_DELAY_MS` env var | `200` ms | Pause between API calls to stay under rate limit |
| **Merge split lines** checkbox | Off | Reduces block count 5–10× for DTP/InDesign PDFs |
| `--req-limit 0` in LT command | Not default | Disables per-minute limit on the LibreTranslate side |

### 🌐 Translation quality

LibreTranslate uses [Argos Translate](https://github.com/argosopentech/argos-translate) models:

- **Good:** European language pairs (EN ↔ DE, FR, ES, IT, NL, PT)
- **Moderate:** EN ↔ RU, PL, ZH, JA
- **Variable:** less common pairs via pivot translation through English

For best results, set **Source language** explicitly — avoid Auto-detect with LibreTranslate.

## 🟢 Google Translate (testing / fallback only)

No setup required. Uses the unofficial `translate.googleapis.com` endpoint — no API key, no account. Requires internet access.

**Limitations:** your PDF text is sent to Google's servers. Not suitable for confidential documents. No enforced file size limit in TransPDF, but large documents may hit undocumented Google rate limits.

Use this backend for quick tests or when translating non-sensitive public documents. For anything private, use Ollama or LibreTranslate instead.

## 🔵 Bing Translator (testing / fallback only)

No setup or API key is required. The backend uses Bing's web translator endpoint and refreshes its session token for each text block. It requires internet access and sends PDF text to Bing. Bing's web endpoint accepts up to 1,000 characters per request and may change without notice.

Select **Bing** from **翻译引擎**. Bing has no connection-test button in the current UI. The backend currently truncates blocks to 1,000 characters and treats `auto` as English; choose the source language explicitly and use another backend for longer blocks.
