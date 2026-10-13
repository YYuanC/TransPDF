"""
TransPDF — backends package.

Public API (imported by app.py):
  translate(...)               generator dispatcher (Gradio UI)
  translate_sync(...)          synchronous dispatcher (REST API)
  test_ollama_connection(...)  → str
  test_libre_connection(...)   → str
  test_bing_connection(...)    → str

Adding a new backend
--------------------
1. Create backends/<name>.py with:
   - call(text, source, target, **params) -> str
   - test_connection(...) -> str  (optional)
2. Add an elif branch in both translate() and translate_sync() below.
   Each branch builds a call_fn closure and a meta dict, then delegates to
   pipeline — no per-backend translate_pdf() wrapper needed.
3. Add any new UI inputs in app.py and thread them through.
"""

import threading

import gradio as gr

import pipeline as _pipeline
from backends import bing, google, libretranslate, ollama
from config import (
    load_config,
    LIBRE_DEFAULT_URL,
    OLLAMA_DEFAULT_URL,
    OLLAMA_DEFAULT_MODEL,
    OLLAMA_DEFAULT_SYSTEM_PROMPT,
    OCR_DEFAULT_SERVICE,
    OCR_LLM_DEFAULT_MODEL,
    OCR_LLM_DEFAULT_PROMPT,
    validate_translation_threads,
)

# Re-export test functions under the names app.py expects
test_ollama_connection = ollama.test_connection
test_libre_connection  = libretranslate.test_connection
test_bing_connection    = bing.test_connection


def translate(
    pdf_path: str,
    source_lang: str,
    target_lang: str,
    service: str,
    ollama_url: str,
    ollama_model: str,
    ollama_system_prompt: str,
    ollama_key: str,
    libre_url: str,
    libre_key: str,
    allow_wrap: bool = False,
    filter_icons: bool = True,
    ocr_service: str = "Tesseract",
    ocr_ollama_model: str = "glm-ocr",
    ocr_ollama_prompt: str = "",
    merge_blocks: bool = False,
    detect_tables: bool = True,
    ocr_mode: str = "auto",
    translation_threads: int | None = None,
):
    """
    Generator dispatcher — builds a call_fn closure per backend, then yields
    from pipeline.translate_pdf_generic.  Yields 11-tuple; see pipeline for
    field order.
    """
    if not pdf_path:
        raise gr.Error("Please upload a PDF file.")

    try:
        threads = validate_translation_threads(
            load_config()["translation_threads"] if translation_threads is None else translation_threads
        )
    except ValueError as exc:
        raise gr.Error(str(exc)) from exc

    ocr_config = {"url": ollama_url, "model": ocr_ollama_model, "prompt": ocr_ollama_prompt}

    if service == "LibreTranslate":
        def call_fn(text: str, src: str, tgt: str) -> str:
            return libretranslate.call(text, src, tgt, libre_url, libre_key)
        meta = {"service": service, "url": libre_url}
    elif service == "Ollama":
        def call_fn(text: str, src: str, tgt: str) -> str:
            return ollama.call(text, src, tgt, ollama_url, ollama_model, ollama_system_prompt, ollama_key)
        meta = {"service": service, "url": ollama_url, "model": ollama_model}
    elif service == "Google":
        call_fn = google.call
        meta = {"service": service, "url": "https://translate.googleapis.com"}
    elif service == "Bing":
        call_fn = bing.call
        meta = {"service": service, "url": "https://www.bing.com/translator"}
    else:
        raise gr.Error(f"Unknown backend: {service}")

    yield from _pipeline.translate_pdf_generic(
        pdf_path, source_lang, target_lang, service, call_fn, allow_wrap, filter_icons,
        meta=meta, ocr_service=ocr_service, ocr_config=ocr_config,
        merge_blocks=merge_blocks, detect_tables=detect_tables,
        ocr_mode=ocr_mode,
        translation_threads=threads,
    )


def translate_sync(
    pdf_path: str,
    source_lang: str,
    target_lang: str,
    service: str,
    ollama_url: str | None = None,
    ollama_model: str | None = None,
    ollama_system_prompt: str | None = None,
    ollama_key: str | None = None,
    libre_url: str | None = None,
    libre_key: str | None = None,
    allow_wrap: bool = False,
    filter_icons: bool = True,
    cancel_event: threading.Event | None = None,
    ocr_service: str | None = None,
    ocr_ollama_model: str | None = None,
    ocr_ollama_prompt: str | None = None,
    merge_blocks: bool = False,
    detect_tables: bool = True,
    ocr_mode: str = "auto",
    translation_threads: int | None = None,
) -> tuple[str, str, str]:
    """
    Synchronous (non-generator) translation for the REST API.

    The global translation thread count and output Chinese font style fall
    back to config.json; backend settings use module-level defaults. Cancellation stops scheduling new
    blocks and waits for in-flight calls to finish (best-effort cancellation).
    Returns (translated_pdf_path, sbs_pdf_path, html_path).
    Raises ValueError for unknown backend or empty document.
    """
    import pipeline as _pipeline

    # OCR settings — fall back to module constants
    _ocr_service = ocr_service or OCR_DEFAULT_SERVICE
    _ocr_url     = ollama_url or OLLAMA_DEFAULT_URL
    _ocr_model   = ocr_ollama_model or OCR_LLM_DEFAULT_MODEL
    _ocr_prompt  = ocr_ollama_prompt or OCR_LLM_DEFAULT_PROMPT
    ocr_cfg      = {"url": _ocr_url, "model": _ocr_model, "prompt": _ocr_prompt}

    if service == "LibreTranslate":
        url = libre_url or LIBRE_DEFAULT_URL
        key = libre_key or ""
        def call_fn(text: str, src: str, tgt: str) -> str:
            return libretranslate.call(text, src, tgt, url, key)
    elif service == "Ollama":
        url    = ollama_url    or OLLAMA_DEFAULT_URL
        model  = ollama_model  or OLLAMA_DEFAULT_MODEL
        prompt = ollama_system_prompt or OLLAMA_DEFAULT_SYSTEM_PROMPT
        key    = ollama_key or ""
        def call_fn(text: str, src: str, tgt: str) -> str:
            return ollama.call(text, src, tgt, url, model, prompt, key)
    elif service == "Google":
        call_fn = google.call
    elif service == "Bing":
        call_fn = bing.call
    else:
        raise ValueError(f"Unknown backend: {service!r}")

    return _pipeline.translate_pdf_sync(
        pdf_path, source_lang, target_lang, service, call_fn, allow_wrap, filter_icons,
        ocr_service=_ocr_service, ocr_config=ocr_cfg,
        merge_blocks=merge_blocks, detect_tables=detect_tables,
        ocr_mode=ocr_mode,
        translation_threads=translation_threads, cancel_event=cancel_event,
    )
