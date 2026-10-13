# 🧪 Testing

## ⚙️ Setup

Install dev dependencies (includes `pytest`):

```bash
pip install -r requirements-dev.txt
```

## ▶️ Running tests

Run the full suite from the project root:

```bash
python -m pytest tests/ -v
```

Run a single file:

```bash
python -m pytest tests/test_api.py -v
```

Run a single test by name:

```bash
python -m pytest tests/test_backends.py::TestOllamaCall::test_prompt_substitution -v
```

## 📋 Test files

| File | What it covers |
|---|---|
| `tests/test_fonts.py` | Font name normalisation, family detection, bold/italic style resolution, `resolve_font` tiers 1–3 |
| `tests/test_pdf_chinese.py` | Simplified/traditional Chinese glyph coverage, embedded fonts, mixed text, short translations, wrapping, table cells, registration fallback, selectable text, and side-by-side PDFs. Also runs via `python -m unittest tests.test_pdf_chinese -v` without pytest. |
| `tests/test_config.py` | `load_config` default merging, `save_config` roundtrip, `update_config` partial update, corrupt config warning |
| `tests/test_backends.py` | `call()` and `test_connection()` for Google, Bing, LibreTranslate, and Ollama — all HTTP calls mocked; `RateLimitError` after retries |
| `tests/test_api.py` | FastAPI routes via `TestClient`: health, config GET/PATCH, cancel, translate (success, error, upload limit, temp cleanup, `_classify_error` typed exceptions) |
| `tests/test_ocr_utils.py` | Tesseract OCR dispatch, LLM OCR (httpx mocked via `sys.modules`), scanned-page detection |
| `tests/test_batch.py` | Multi-file UI/API processing, ZIP formats, duplicate names, partial/all failures, cancellation, upload limits, cleanup and Gradio serialization |
| `tests/test_webhook.py` | Paperless webhook integration |
| `tests/test_backfill.py` | Historical Paperless document processing |
| `tests/test_pipeline.py` | `_translate_one_block`: reflow, delay, no delay, exception propagation, field preservation |
| `tests/test_e2e.py` | End-to-end pipeline with mock `call_fn` against `docs/assets/demo-document-de.pdf`: PDF validity, SBS geometry, HTML content, PII presence |

## 📝 Notes

- GitHub Actions source tests run independently in `Tests` (`tests.yml`) on pushes to all branches, pull requests, and pushes of all tags. Tests can also be triggered manually with `workflow_dispatch`. A standalone manual Docker build does not depend on the Tests workflow; the Release workflow explicitly gates both build types on Tests.
- `Build application` starts each packaged executable and checks `/api/health` and the Gradio UI before uploading it. To run the same check locally after a PyInstaller build, use `python scripts/smoke_test_application.py`.
- `Release` is manually triggered and first reuses `Tests` for the selected version tag. After tests pass, it runs `Build application` and `Build and push Docker image` in parallel. The release archives are published only after both build workflows and all executable startup checks pass. See the [release guide](releasing.md).
- Chinese PDF text comparisons use NFC normalization to handle canonically equivalent CJK glyph mappings without folding mathematical symbols or full-width punctuation.
- No live services required — all HTTP calls are mocked via `unittest.mock` or `sys.modules` patching.
- `test_api.py` uses a mocked `translate_sync` — no actual PDF translation runs.
- `test_e2e.py` runs the full pipeline (prescan → translate → write) with a mock translation function; requires `docs/assets/demo-document-de.pdf`.
- `tests/conftest.py` provides shared fixtures: `isolated_config` (redirects `config.json` to a temp file), `minimal_pdf` (one-page fitz PDF), and `reset_job` (clears the `_job` singleton between API tests).
- Use `python -m pytest --collect-only -q` for the current test count. HTTP calls are mocked; this does not validate live provider availability.
