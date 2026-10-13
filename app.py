"""
TransPDF — Gradio UI.
Run: python app.py
"""

import asyncio
import os
import shutil
import subprocess
import tempfile
import threading
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional

import fitz  # PyMuPDF
import gradio as gr
import uvicorn
from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from backends import (
    test_libre_connection,
    test_ollama_connection,
    translate,
    translate_sync,
)
from batch import create_batch_archive, safe_name
from exceptions import BackendConnectionError, OcrModelError, OcrOomError, RateLimitError
from config import (
    CONFIG_PATH,
    DEFAULT_BACKEND,
    MAX_TRANSLATION_THREADS,
    OCR_DEFAULT_SERVICE,
    OCR_LLM_DEFAULT_MODEL,
    OCR_LLM_DEFAULT_PROMPT,
    SOURCE_LANGUAGES,
    TARGET_LANGUAGES,
    load_config,
    save_config,
    update_config,
)
from pdf_utils import pdf_to_image

# ---------------------------------------------------------------------------
# Page navigation helpers
# ---------------------------------------------------------------------------


def _page_count(pdf_path: str | None) -> int:
    if not pdf_path:
        return 1
    try:
        doc = fitz.open(pdf_path)
        n = len(doc)
        doc.close()
        return n
    except Exception:
        return 1


def go_prev(orig_path: str | None, trans_path: str | None, page_idx: int):
    if not orig_path:
        return gr.update(), gr.update(), page_idx, "—"
    n = _page_count(orig_path)
    new_idx = (page_idx - 1) % n  # wraps: page 1 → last page
    orig_img = pdf_to_image(orig_path, new_idx)
    trans_update = pdf_to_image(trans_path, new_idx) if trans_path else gr.update()
    return orig_img, trans_update, new_idx, f"Page {new_idx + 1} / {n}"


def go_next(orig_path: str | None, trans_path: str | None, page_idx: int):
    if not orig_path:
        return gr.update(), gr.update(), page_idx, "—"
    n = _page_count(orig_path)
    new_idx = (page_idx + 1) % n  # wraps: last page → page 1
    orig_img = pdf_to_image(orig_path, new_idx)
    trans_update = pdf_to_image(trans_path, new_idx) if trans_path else gr.update()
    return orig_img, trans_update, new_idx, f"Page {new_idx + 1} / {n}"


def select_preview(file_index: str | None, results: list[dict]):
    """Select a document and reset both previews to its first page."""
    if file_index is None or not results:
        return None, None, 0, "—", None, gr.update(visible=False), gr.update(visible=False), None
    result = results[int(file_index)]
    original = result["original_path"]
    translated = result.get("translated_path")
    try:
        orig_image = pdf_to_image(original, 0)
        trans_image = pdf_to_image(translated, 0) if translated else None
        indicator = f"Page 1 / {_page_count(original)}"
        nav = gr.update(visible=True)
    except Exception:
        orig_image = trans_image = None
        indicator = "无法预览此 PDF"
        nav = gr.update(visible=False)
    return orig_image, trans_image, 0, indicator, translated, nav, nav, original


def on_files_upload(pdf_paths: list[str] | None):
    """Reset results and downloads whenever the upload list changes."""
    results = [
        {"filename": Path(path).name, "original_path": path, "status": "pending"}
        for path in (pdf_paths or [])
    ]
    choices = [(f"{i + 1}. {r['filename']}", str(i)) for i, r in enumerate(results)]
    selected = "0" if results else None
    orig, trans, page, indicator, translated, prev, nxt, original = select_preview(selected, results)
    hide = gr.update(value=None, visible=False)
    return (
        hide, hide, hide, orig, trans,
        f"已选择 {len(results)} 个文件。" if results else "",
        page, indicator, translated, prev, nxt, hide, results,
        gr.update(choices=choices, value=selected, visible=bool(results), interactive=True),
        original,
    )


def translate_files(pdf_paths: list[str] | None, *args):
    """Translate PDFs sequentially, keeping successful downloads after failures."""
    results = [
        {"filename": Path(path).name, "original_path": path, "status": "pending"}
        for path in (pdf_paths or [])
    ]
    choices = [(f"{i + 1}. {r['filename']}", str(i)) for i, r in enumerate(results)]
    summaries = []
    last_success = None
    last_step = None

    def download(key):
        paths = [r[key] for r in results if r.get(key)]
        return gr.update(value=paths or None, visible=bool(paths))

    def update(step, index, message, finished=False, archive=None):
        # Use completed-file downloads even while the next file is running.
        return (
            download("translated_path"), download("sbs_path"), download("html_path"),
            step[3], step[4], message, step[6], step[7], step[8],
            step[9] if finished else gr.update(visible=False),
            step[10] if finished else gr.update(visible=False),
            gr.update(value=archive, visible=bool(archive)),
            [dict(r) for r in results],
            gr.update(choices=choices, value=str(index) if results else None,
                      visible=bool(results), interactive=finished),
            results[index]["original_path"] if results else None,
            gr.update(interactive=finished), gr.update(interactive=finished),
        )

    empty = (None, None, None, None, None, "", 0, "—", None,
             gr.update(visible=False), gr.update(visible=False))
    if not results:
        yield update(empty, 0, "请先上传 PDF 文件。", finished=True)
        return

    yield update(empty, 0, f"准备翻译 {len(results)} 个文件……")
    for index, result in enumerate(results):
        prefix = f"[{index + 1}/{len(results)}] {result['filename']}"
        step = empty
        result["status"] = "running"
        try:
            for step in translate(result["original_path"], *args):
                yield update(step, index, "\n".join([*summaries, f"{prefix}：{step[5]}"]))
            if not step[8] or not isinstance(step[8], str):
                raise RuntimeError("Translation produced no output.")
            result.update(status="completed", translated_path=step[8],
                          sbs_path=step[1]["value"], html_path=step[2]["value"])
            last_success, last_step = index, step
            summaries.append(f"{prefix}：完成")
        except Exception as exc:
            result.update(status="failed", error=str(exc))
            summaries.append(f"{prefix}：失败 — {exc}")
            yield update(empty, index, "\n".join(summaries))

    succeeded = sum(r["status"] == "completed" for r in results)
    archive = None
    try:
        if len(results) > 1:
            archive = create_batch_archive(results, args[0], args[1])
    except Exception as exc:
        summaries.append(f"打包失败：{exc}")
    summaries.insert(0, f"批量处理完成：成功 {succeeded}，失败 {len(results) - succeeded}。")
    yield update(last_step or empty, last_success if last_success is not None else 0,
                 "\n".join(summaries), finished=True, archive=archive)


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

def _build_version() -> str:
    """Return a version string.

    Priority:
    1. ``git describe --tags --always --dirty`` — dev runs with a git checkout.
       Produces ``v0.1.0`` on a tagged release, ``v0.1.0-5-g68f2e06`` on a dev
       build, ``g68f2e06-dirty`` when the working tree has uncommitted changes.
    2. ``VERSION`` file — written by CI (``git describe --tags --always``) before
       the Docker image is built.  Always clean (no ``-dirty`` suffix).
    3. ``"unknown"`` — fallback when neither is available.
    """
    try:
        label = subprocess.check_output(
            ["git", "describe", "--tags", "--always", "--dirty"],
            cwd=Path(__file__).parent,
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        if label:
            return label
    except Exception:
        pass
    # Fallback: VERSION file written by CI before docker build
    try:
        return (Path(__file__).parent / "VERSION").read_text().strip()
    except Exception:
        return "unknown"


_DEBUG = os.environ.get("PDF_TRANSLATE_DEBUG", "").lower() in ("1", "true", "yes")

cfg = load_config()
_VERSION = _build_version()

# Startup log — always visible in docker logs; helps diagnose config-path issues
_config_status = "found" if CONFIG_PATH.exists() else "not found"
print(f"[TransPDF] version={_VERSION} config={CONFIG_PATH} ({_config_status}) debug={_DEBUG}", flush=True)

_ICON_SVG = (
    (Path(__file__).parent / "icon.svg")
    .read_text(encoding="utf-8")
    .replace('height="48px"', 'height="36px"')
    .replace('width="48px"', 'width="36px"')
    # The source SVG is white for the browser favicon.  Give the inline
    # header icon an explicit accent color so it stays visible in every theme.
    .replace('fill="#ffffff"', 'fill="#2563eb"')
    .replace('<svg ', '<svg style="display:block;width:36px;height:36px;flex:none;" ')
)

with gr.Blocks(title="TransPDF — PDF 翻译", fill_width=False) as gradio_app:
    gr.HTML(
        f"""<div style="display:flex;align-items:center;gap:0.5em;margin-bottom:0.25em;">
  {_ICON_SVG}
  <h1 style="margin:0;font-size:1.8em;line-height:1;">TransPDF
    <span style="font-size:0.45em;font-weight:normal;vertical-align:middle;margin-left:0.6em;">版本：<code>{_VERSION}</code></span>
  </h1>
</div>""",
        elem_id="app-header",
    )

    # Persistent state
    page_state           = gr.State(0)     # current page index (0-based)
    translated_path_state = gr.State(None)  # path to translated PDF (for navigation)
    original_path_state = gr.State(None)
    batch_results_state = gr.State([])

    # Keep the controls and preview panel in one horizontal track.  Gradio
    # normally wraps row children when their minimum widths do not fit, which
    # puts the translated preview below the (tall) engine-settings column.
    with gr.Row():

        # ------------------------------------------------------------------
        # Column 1 — controls
        # ------------------------------------------------------------------
        with gr.Column(scale=1, min_width=320):
            pdf_input = gr.File(
                label="PDF 文件（可多选）",
                file_types=[".pdf"],
                type="filepath",
                file_count="multiple",
            )
            preview_file = gr.Dropdown(label="预览文件", choices=[], visible=False)
            source_lang = gr.Dropdown(
                label="源语言",
                choices=[(label, code) for label, code in SOURCE_LANGUAGES],
                value=cfg["source"],
            )
            target_lang = gr.Dropdown(
                label="目标语言",
                choices=[(label, code) for label, code in TARGET_LANGUAGES],
                value=cfg["target"],
            )
            backend = gr.Dropdown(
                label="翻译引擎",
                choices=["Ollama", "LibreTranslate", "Google", "Bing"],
                value=cfg["backend"],
            )
            translate_btn = gr.Button("开始翻译", variant="primary", size="lg")
            with gr.Accordion("高级设置", open=False):
                cjk_font_family = gr.Dropdown(
                    label="中文字体样式",
                    choices=[
                        ("匹配原文样式", "auto"),
                        ("Source Han Serif (宋体)", "serif"),
                        ("Source Han Sans (黑体)", "sans-serif"),
                        ("LXGW WenKai (文楷)", "wenkai"),
                    ],
                    value=cfg["cjk_font_family"],
                    info="保存设置后将应用于中文 PDF 导出；粗体文字会使用匹配的思源粗体字体。",
                )
                translation_threads = gr.Number(
                    label="翻译线程数（所有引擎）",
                    value=cfg["translation_threads"],
                    minimum=1,
                    maximum=MAX_TRANSLATION_THREADS,
                    precision=0,
                    step=1,
                    info="每个 PDF 并发处理的文本块请求数。1 表示顺序处理。保存后将作为网页和 API 的全局默认值。",
                )
                ocr_mode_dropdown = gr.Dropdown(
                    label="OCR 模式",
                    choices=[("关闭", "off"), ("开启", "auto"), ("强制", "force")],
                    value=cfg["ocr_mode"],
                    info="关闭：不进行 OCR；开启：无可用文本时自动回退 OCR；强制：忽略文本层，对所有页面进行 OCR。",
                )
                ocr_service = gr.Dropdown(
                    label="OCR 引擎（仅扫描 PDF）",
                    choices=["Tesseract", "Ollama"],
                    value=cfg.get("ocr_service", OCR_DEFAULT_SERVICE),
                )
                allow_wrap_cb = gr.Checkbox(
                    label="允许文本重排（合并换行）",
                    value=cfg["allow_wrap"],
                )
                filter_icons_cb = gr.Checkbox(
                    label="过滤图标/符号字符",
                    value=cfg["filter_icons"],
                )
                merge_blocks_cb = gr.Checkbox(
                    label="合并拆分行（DTP/InDesign PDF）",
                    value=cfg.get("merge_blocks", False),
                )
                detect_tables_cb = gr.Checkbox(
                    label="检测表格单元格（缩小适配）",
                    value=cfg.get("detect_tables", True),
                )
            status_box = gr.Textbox(
                label="状态",
                interactive=False,
                lines=3,
                placeholder="翻译开始后将在此显示状态……",
            )
            download_translated = gr.File(label="翻译后的 PDF", file_count="multiple", visible=False)
            download_sbs  = gr.File(label="对照 PDF（原文 | 译文）", file_count="multiple", visible=False)
            download_html = gr.File(label="对照 HTML（原文 | 译文）", file_count="multiple", visible=False)
            download_batch = gr.File(label="全部结果（ZIP，含处理报告）", visible=False)

            with gr.Accordion("引擎设置", open=False):
                with gr.Group():
                    gr.Markdown("**Ollama**")
                    ollama_url = gr.Textbox(label="URL（翻译和 OCR 共用）", value=cfg["ollama_url"])
                    ollama_key = gr.Textbox(
                        label="API 密钥（可选）",
                        value=cfg["ollama_key"],
                        type="password",
                    )
                    ollama_test_btn    = gr.Button("测试连接", size="sm")
                    ollama_test_result = gr.Markdown("")
                    ollama_model = gr.Textbox(label="翻译模型", value=cfg["ollama_model"])
                    with gr.Accordion("翻译系统提示词", open=False):
                        ollama_system_prompt = gr.Textbox(
                            label="",
                            value=cfg["ollama_system_prompt"],
                            lines=8,
                            max_lines=20,
                        )
                    ocr_ollama_model = gr.Textbox(
                        label="OCR 模型",
                        value=cfg.get("ocr_ollama_model", OCR_LLM_DEFAULT_MODEL),
                    )
                    with gr.Accordion("OCR 提示词", open=False):
                        ocr_ollama_prompt = gr.Textbox(
                            label="",
                            value=cfg.get("ocr_ollama_prompt", OCR_LLM_DEFAULT_PROMPT),
                            lines=4,
                            max_lines=10,
                        )

                with gr.Group():
                    gr.Markdown("**LibreTranslate**")
                    libre_url = gr.Textbox(label="URL", value=cfg["libre_url"])
                    libre_key = gr.Textbox(
                        label="API 密钥（可选）",
                        value=cfg["libre_key"],
                        type="password",
                    )
                    libre_test_btn    = gr.Button("测试连接", size="sm")
                    libre_test_result = gr.Markdown("")

                with gr.Row():
                    save_btn    = gr.Button("保存配置", size="sm")
                    save_status = gr.Markdown("")

        # ------------------------------------------------------------------
        # Preview panel — images side by side, navigation in one shared row
        # ------------------------------------------------------------------
        with gr.Column(scale=2, min_width=640):
            with gr.Row(equal_height=True):
                with gr.Column(scale=1, min_width=300):
                    orig_preview = gr.Image(
                        label="原文",
                        type="filepath",
                        interactive=False,
                    )
                with gr.Column(scale=1, min_width=300):
                    trans_preview = gr.Image(
                        label="译文",
                        type="filepath",
                        interactive=False,
                    )
            with gr.Row(equal_height=True):
                prev_btn = gr.Button("◀ 上一页", size="sm", visible=False)
                next_btn = gr.Button("下一页 ▶", size="sm", visible=False)

    # Keep the indicator as state for the navigation callbacks, but do not
    # render a page-number control in the web UI.
    page_indicator = gr.State("—")

    # --- Event wiring -----------------------------------------------------

    # Re-read config.json on every page load so saved settings survive a reload
    def _refresh_config():
        c = load_config()
        ocr_mode = c["ocr_mode"]
        return (
            c["source"],
            c["target"],
            c["backend"],
            c["allow_wrap"],
            # Text-layer-only options: disable only when OCR bypasses the text layer.
            gr.update(value=c["filter_icons"],           interactive=ocr_mode != "force"),
            gr.update(value=c.get("merge_blocks", False), interactive=ocr_mode != "force"),
            gr.update(value=c.get("detect_tables", True), interactive=ocr_mode != "force"),
            ocr_mode,
            c["ollama_url"],
            c["ollama_model"],
            c["ollama_system_prompt"],
            c["ollama_key"],
            c["libre_url"],
            c["libre_key"],
            c.get("ocr_service", OCR_DEFAULT_SERVICE),
            c.get("ocr_ollama_model", OCR_LLM_DEFAULT_MODEL),
            c.get("ocr_ollama_prompt", OCR_LLM_DEFAULT_PROMPT),
            c["translation_threads"],
            c["cjk_font_family"],
        )

    gradio_app.load(
        _refresh_config,
        outputs=[source_lang, target_lang, backend, allow_wrap_cb, filter_icons_cb,
                 merge_blocks_cb, detect_tables_cb, ocr_mode_dropdown,
                 ollama_url, ollama_model, ollama_system_prompt, ollama_key, libre_url, libre_key,
                 ocr_service, ocr_ollama_model, ocr_ollama_prompt, translation_threads, cjk_font_family],
    )

    # Disable text-layer-only options when forced OCR is selected — they have no
    # effect when the text layer is bypassed entirely.
    # allow_wrap_cb is kept interactive because line-break collapsing applies to
    # OCR paragraphs too.
    def _toggle_text_layer_opts(ocr_mode: str):
        return (
            gr.update(interactive=ocr_mode != "force"),  # filter_icons_cb
            gr.update(interactive=ocr_mode != "force"),  # merge_blocks_cb
            gr.update(interactive=ocr_mode != "force"),  # detect_tables_cb
        )

    ocr_mode_dropdown.change(
        _toggle_text_layer_opts,
        inputs=[ocr_mode_dropdown],
        outputs=[filter_icons_cb, merge_blocks_cb, detect_tables_cb],
    )

    batch_outputs = [
        download_translated, download_sbs, download_html, orig_preview, trans_preview,
        status_box, page_state, page_indicator, translated_path_state, prev_btn, next_btn,
        download_batch, batch_results_state, preview_file, original_path_state,
    ]

    # Upload changes reset all batch results and stale preview paths.
    pdf_input.change(
        on_files_upload,
        inputs=[pdf_input],
        outputs=batch_outputs,
        concurrency_id="pdf-batch",
    )
    preview_file.input(
        select_preview,
        inputs=[preview_file, batch_results_state],
        outputs=[orig_preview, trans_preview, page_state, page_indicator,
                 translated_path_state, prev_btn, next_btn, original_path_state],
        concurrency_id="pdf-batch",
    )

    # Page navigation
    prev_btn.click(
        go_prev,
        inputs=[original_path_state, translated_path_state, page_state],
        outputs=[orig_preview, trans_preview, page_state, page_indicator],
        concurrency_id="pdf-batch",
    )
    next_btn.click(
        go_next,
        inputs=[original_path_state, translated_path_state, page_state],
        outputs=[orig_preview, trans_preview, page_state, page_indicator],
        concurrency_id="pdf-batch",
    )

    # Backend connection tests
    ollama_test_btn.click(
        test_ollama_connection,
        inputs=[ollama_url, ollama_key],
        outputs=[ollama_test_result],
    )
    libre_test_btn.click(
        test_libre_connection,
        inputs=[libre_url],
        outputs=[libre_test_result],
    )
    # Save config
    save_btn.click(
        save_config,
        inputs=[
            backend,
            source_lang,
            target_lang,
            allow_wrap_cb,
            filter_icons_cb,
            ollama_url,
            ollama_model,
            ollama_system_prompt,
            ollama_key,
            libre_url,
            libre_key,
            ocr_service,
            ocr_ollama_model,
            ocr_ollama_prompt,
            merge_blocks_cb,
            detect_tables_cb,
            ocr_mode_dropdown,
            translation_threads,
            cjk_font_family,
        ],
        outputs=[save_status],
    )

    # Batch wrapper adapts the single-PDF pipeline and preserves its previews.
    translate_btn.click(
        translate_files,
        inputs=[
            pdf_input,
            source_lang,
            target_lang,
            backend,
            ollama_url,
            ollama_model,
            ollama_system_prompt,
            ollama_key,
            libre_url,
            libre_key,
            allow_wrap_cb,
            filter_icons_cb,
            ocr_service,
            ocr_ollama_model,
            ocr_ollama_prompt,
            merge_blocks_cb,
            detect_tables_cb,
            ocr_mode_dropdown,
            translation_threads,
        ],
        outputs=[*batch_outputs, pdf_input, translate_btn],
        concurrency_id="pdf-batch",
    )


# ---------------------------------------------------------------------------
# Job state (in-process — single-user PoC)
# ---------------------------------------------------------------------------


@dataclass
class _Job:
    """Tracks the current and last-completed translation job."""
    # Current job
    running: bool = False
    queued: int = 0                             # requests waiting for the semaphore
    started_at: str | None = None
    source: str | None = None
    target: str | None = None
    backend: str | None = None
    cancel_event: threading.Event = field(default_factory=threading.Event)
    last_exc: BaseException | None = None        # last exception (for error_type classification)
    # Last result — persists until the next job starts
    last_status: str | None = None              # "completed" | "partial" | "failed" | "cancelled"
    last_started_at: str | None = None
    last_completed_at: str | None = None
    last_source: str | None = None
    last_target: str | None = None
    last_backend: str | None = None
    last_error: str | None = None


_job = _Job()
_translate_sem: asyncio.Semaphore | None = None   # lazy-init on first request


def _init_sem() -> asyncio.Semaphore:
    global _translate_sem
    if _translate_sem is None:
        _translate_sem = asyncio.Semaphore(1)
    return _translate_sem


# ---------------------------------------------------------------------------
# Config Pydantic model (for PATCH /api/config)
# ---------------------------------------------------------------------------


class ConfigUpdate(BaseModel):
    ocr_mode: Optional[Literal["off", "auto", "force"]] = None
    cjk_font_family: Optional[Literal["auto", "serif", "sans-serif", "wenkai"]] = None
    backend:              Optional[str] = None
    ollama_url:           Optional[str] = None
    ollama_model:         Optional[str] = None
    ollama_system_prompt: Optional[str] = None
    ollama_key:           Optional[str] = None
    libre_url:            Optional[str] = None
    libre_key:            Optional[str] = None
    translation_threads:  Optional[int] = Field(
        default=None, ge=1, le=MAX_TRANSLATION_THREADS, strict=True,
    )


# ---------------------------------------------------------------------------
# REST API
# ---------------------------------------------------------------------------

_MAX_UPLOAD_BYTES = int(os.environ.get("PDF_TRANSLATE_MAX_UPLOAD_MB", "100")) * 1024 * 1024


def _require_api_key(authorization: str = Header(default="")) -> None:
    """Optional API key guard. Pass 'Authorization: Bearer <key>' when set."""
    key = os.environ.get("PDF_TRANSLATE_API_KEY", "")
    if not key:
        return  # no key configured — open access
    if authorization != f"Bearer {key}":
        raise HTTPException(
            status_code=401,
            detail="Invalid or missing API key. Pass 'Authorization: Bearer <key>' header.",
            headers={"WWW-Authenticate": "Bearer"},
        )

_API_DESCRIPTION = """
Self-hosted PDF translation service.

**Quick test with curl:**
```bash
curl -X POST http://localhost:7860/api/translate \\\\
  -F "file=@document.pdf" \\\\
  -F "source=nl" -F "target=en" \\\\
  --output translated.pdf
```
"""

api_app = FastAPI(
    title="TransPDF",
    description=_API_DESCRIPTION,
    version=_VERSION,
)


@api_app.get("/api/health", summary="Health check", tags=["Status"])
async def api_health():
    """Return service status, version, default backend, and current job state.

    `job.running` is true while a translation is in progress.
    `job.last` contains the outcome of the most recent job (including partial
    batch success; persists until the
    next job starts) — use this to check what happened after a client timeout.
    """
    if _job.running:
        job_info: dict = {
            "running": True,
            "queued": _job.queued,
            "started_at": _job.started_at,
            "source": _job.source,
            "target": _job.target,
            "backend": _job.backend,
        }
    else:
        last = None
        if _job.last_status is not None:
            last = {
                "status": _job.last_status,
                "started_at": _job.last_started_at,
                "completed_at": _job.last_completed_at,
                "source": _job.last_source,
                "target": _job.last_target,
                "backend": _job.last_backend,
            }
            if _job.last_error:
                last["error"] = _job.last_error
        job_info = {"running": False, "last": last}
    return {"status": "ok", "version": _VERSION, "backend": DEFAULT_BACKEND, "job": job_info}


@api_app.get("/api/config", summary="Read configuration", tags=["Configuration"],
             dependencies=[Depends(_require_api_key)])
async def api_config_get():
    """Return the current backend configuration. API key fields are masked."""
    cfg = load_config()
    for key in ("ollama_key", "libre_key"):
        if cfg.get(key):
            cfg[key] = "***"
    return cfg


def _classify_error(msg: str, exc: BaseException | None = None) -> str:
    """Map an exception (or its message) to a machine-readable error_type for automation.

    Checks the exception type first (reliable), then falls back to message
    string matching for errors that don't use typed exceptions yet.
    """
    if isinstance(exc, OcrOomError):
        return "ocr_oom"
    if isinstance(exc, OcrModelError):
        return "ocr_model_error"
    if isinstance(exc, RateLimitError):
        return "translation_rate_limit"
    if isinstance(exc, BackendConnectionError):
        return "config_error"
    # Fallback: string matching for untyped errors
    if "GGML_ASSERT" in msg:
        return "ocr_model_error"
    if "CUDA OOM" in msg:
        return "ocr_oom"
    if "429 TOO MANY REQUESTS" in msg or "429 Too Many Requests" in msg:
        return "translation_rate_limit"
    if "Connection refused" in msg or "ConnectError" in msg:
        return "config_error"
    return "translation_error"


@api_app.patch("/api/config", summary="Update configuration", tags=["Configuration"],
               dependencies=[Depends(_require_api_key)])
async def api_config_patch(update: ConfigUpdate):
    """
    Partially update the Gradio UI configuration (config.json).

    Only supplied (non-null) fields are changed; omitted fields keep their
    current values. Changes are persisted to config.json and reflected in the
    UI immediately — no restart required.

    `translation_threads` and `cjk_font_family` set global defaults for UI
    and REST API translations. Other fields only affect the Gradio UI; REST API backend
    settings come from request parameters and env vars.
    """
    updates = {k: v for k, v in update.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(status_code=400, detail="No fields supplied.")
    update_config(updates)
    return {"status": "ok", "updated": list(updates.keys())}


@api_app.delete("/api/translate", summary="Cancel running translation", tags=["Translation"],
                dependencies=[Depends(_require_api_key)])
async def api_translate_cancel():
    """
    Request cancellation of the currently running translation.

    Cancellation is best-effort: in-flight backend calls (Ollama /
    LibreTranslate / Google / Bing) run to completion; no new blocks are
    scheduled. The blocked `POST /api/translate` call returns 409.
    """
    if not _job.running:
        raise HTTPException(status_code=404, detail="No translation is currently running.")
    _job.cancel_event.set()
    return {
        "status": "ok",
        "message": "Cancellation requested. Translation stops after in-flight blocks finish.",
    }


@api_app.post("/api/translate", summary="Translate one or more PDFs", tags=["Translation"],
              dependencies=[Depends(_require_api_key)])
async def api_translate(
    background_tasks: BackgroundTasks,
    file: list[UploadFile] = File(..., description="PDF files to translate; repeat the 'file' field for a batch"),
    source: str = Form("auto", description="Source language code: 'auto', 'en', 'nl', 'de', …"),
    target: str = Form("zh", description="Target language code: 'zh', 'en', 'nl', 'de', 'fr', …"),
    service: Optional[str] = Form(
        None,
        description=f"Backend: Ollama, LibreTranslate, Google, or Bing. Defaults to PDF_TRANSLATE_BACKEND env var (currently: {DEFAULT_BACKEND!r}).",
    ),
    libre_url: Optional[str] = Form(
        None,
        description="LibreTranslate base URL. Defaults to LIBRETRANSLATE_URL env var or http://localhost:5000.",
    ),
    libre_key: Optional[str] = Form(
        None,
        description="LibreTranslate API key (optional).",
    ),
    ollama_url: Optional[str] = Form(
        None,
        description="Ollama base URL. Defaults to http://localhost:11434.",
    ),
    ollama_model: Optional[str] = Form(
        None,
        description="Ollama translation model name.",
    ),
    ollama_key: Optional[str] = Form(
        None,
        description="Ollama API key (optional).",
    ),
    allow_wrap: bool = Form(
        False,
        description="Collapse line breaks before translation (helps with wrapped paragraphs)",
    ),
    filter_icons: bool = Form(
        True,
        description="Strip single-character icon glyphs from mixed text blocks",
    ),
    merge_blocks: bool = Form(
        False,
        description="Merge same-line word fragments before translation. "
                    "Enable for DTP/InDesign PDFs with word-level text objects.",
    ),
    detect_tables: bool = Form(
        True,
        description="Detect table cells and apply shrink-to-fit text fitting. "
                    "Disable if translated text appears abnormally small.",
    ),
    ocr_mode: Literal["off", "auto", "force"] = Form(
        "auto",
        description="OCR mode: 'off' disables OCR, 'auto' OCRs only pages without usable text, "
                    "'force' OCRs every page.",
    ),
    translation_threads: Optional[int] = Form(
        None,
        ge=1,
        le=MAX_TRANSLATION_THREADS,
        description="Concurrent text-block requests per PDF (all backends). "
                    "Defaults to the saved global translation_threads setting, "
                    "or PDF_TRANSLATE_THREADS (default: 3).",
    ),
    outputs: Literal["pdf", "sbs", "reading", "all"] = Form(
        "pdf",
        description=(
            "Output format: "
            "'pdf' — translated PDF (default); "
            "'sbs' — side-by-side landscape PDF, layout-matched (original | translation); "
            "'reading' — HTML reading view, original and translated text side by side, clean and reflowable; "
            "'all' — zip archive containing all three."
        ),
    ),
):
    """
    Upload one or more PDFs and receive the translated output.

    Repeat the `file` form field for a batch. Multiple files are processed in
    order and always return a ZIP with numbered folders and `results.json`.
    `outputs` selects the formats included for each successful document.
    Failed documents are recorded in the report; remaining files continue.
    A batch counts as one queued job and cancellation stops the whole batch.

    **This call is synchronous** — the HTTP connection stays open until
    translation completes. Set your client timeout to >= 300 s for large
    documents. If running behind a reverse proxy, raise its read timeout too
    (nginx: `proxy_read_timeout 300;`).

    **Queue:** one translation runs at a time; one additional request may wait.
    A third concurrent request receives **429** with a `Retry-After: 60` header.

    **Cancellation:** send `DELETE /api/translate` to stop the running job.
    The blocked POST returns **409** when cancelled.

    **Status codes:**
    - 200 — success; body is the requested output
    - 409 — translation was cancelled via `DELETE /api/translate`
    - 422 — no translatable text blocks found in the PDF
    - 429 — queue full (1 running + 1 waiting); retry after 60 s
    - 500 — backend error (connection failure, model error, etc.)

    Check `GET /api/health` after a timeout to see what happened to the job.
    Response headers always include `X-Source-Lang`, `X-Target-Lang`, `X-Backend`.
    """
    backend = service or DEFAULT_BACKEND
    upload_dir = tempfile.mkdtemp()
    out_dirs = set()
    response_ready = False
    queued = False
    results = []
    try:
        # Limit the complete batch as well as each individual file. Keep each
        # upload in its own folder, preserving names without risking collisions.
        total_size = 0
        for index, upload in enumerate(file):
            filename = safe_name(upload.filename or "document.pdf")
            folder = Path(upload_dir) / str(index)
            folder.mkdir()
            tmp_pdf = str(folder / filename)
            with open(tmp_pdf, "wb") as stream:
                while chunk := await upload.read(1024 * 1024):
                    total_size += len(chunk)
                    if total_size > _MAX_UPLOAD_BYTES:
                        raise HTTPException(
                            status_code=413,
                            detail=f"Upload too large. Maximum total upload size is "
                                   f"{_MAX_UPLOAD_BYTES // (1024 * 1024)} MB. "
                                   "Override with PDF_TRANSLATE_MAX_UPLOAD_MB env var.",
                        )
                    stream.write(chunk)
            results.append({"filename": filename, "original_path": tmp_pdf, "status": "pending"})

        if _job.running and _job.queued >= 1:
            raise HTTPException(
                status_code=429,
                detail="Translation queue is full (1 running, 1 waiting). Retry later.",
                headers={"Retry-After": "60"},
            )
        if _job.running:
            _job.queued += 1
            queued = True

        job_status = "completed"
        error_msg = None
        last_exc = None
        async with _init_sem():
            if queued:
                _job.queued -= 1
                queued = False
            started_at = datetime.now(timezone.utc).isoformat()
            _job.running = True
            _job.started_at = started_at
            _job.source = source
            _job.target = target
            _job.backend = backend
            _job.cancel_event.clear()
            _job.last_exc = None
            try:
                for result in results:
                    if _job.cancel_event.is_set():
                        job_status = "cancelled"
                        break
                    try:
                        translated, sbs, reading = await asyncio.to_thread(
                            translate_sync,
                            result["original_path"], source, target, backend,
                            libre_url=libre_url, libre_key=libre_key,
                            ollama_url=ollama_url, ollama_model=ollama_model,
                            ollama_key=ollama_key, allow_wrap=allow_wrap,
                            filter_icons=filter_icons, cancel_event=_job.cancel_event,
                            merge_blocks=merge_blocks, detect_tables=detect_tables,
                            ocr_mode=ocr_mode,
                            translation_threads=translation_threads,
                        )
                        result.update(status="completed", translated_path=translated,
                                      sbs_path=sbs, html_path=reading)
                        out_dirs.update(str(Path(path).parent) for path in (translated, sbs, reading))
                    except InterruptedError:
                        job_status = "cancelled"
                        break
                    except Exception as exc:
                        last_exc = exc
                        message = str(exc)
                        result.update(
                            status="failed", error=message,
                            error_type="no_text" if "No translatable" in message
                            else _classify_error(message, exc),
                        )
                if job_status == "cancelled" or _job.cancel_event.is_set():
                    job_status = "cancelled"
                elif any(r["status"] == "failed" for r in results):
                    job_status = "partial" if any(r["status"] == "completed" for r in results) else "failed"
                    error_msg = "\n".join(
                        f"{r['filename']}: {r['error']}" for r in results if r["status"] == "failed"
                    )
            finally:
                _job.running = False
                _job.started_at = None
                _job.last_status = job_status
                _job.last_started_at = started_at
                _job.last_completed_at = datetime.now(timezone.utc).isoformat()
                _job.last_source = source
                _job.last_target = target
                _job.last_backend = backend
                _job.last_error = error_msg
                _job.last_exc = last_exc

        if job_status == "cancelled":
            return JSONResponse(status_code=409, content={
                "detail": "Translation was cancelled.", "error_type": "cancelled",
            })
        if len(results) == 1 and job_status == "failed":
            result = results[0]
            no_text = result["error_type"] == "no_text"
            return JSONResponse(status_code=422 if no_text else 500, content={
                "detail": result["error"] if no_text else f"Translation failed: {result['error']}",
                "error_type": result["error_type"],
            })

        extra = {"X-Source-Lang": source, "X-Target-Lang": target, "X-Backend": backend}
        if len(results) > 1:
            zip_dir = tempfile.mkdtemp()
            out_dirs.add(zip_dir)
            output_path = create_batch_archive(
                results, source, target, outputs, str(Path(zip_dir) / "translations.zip"),
            )
            filename, media_type = "translations.zip", "application/zip"
        else:
            result = results[0]
            stem = Path(result["filename"]).stem
            if outputs == "all":
                # Write ZIPs to disk and use the same response cleanup as PDFs.
                output_path = str(Path(result["translated_path"]).parent / "outputs.zip")
                with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as archive:
                    archive.write(result["translated_path"], f"{stem}_{target}.pdf")
                    archive.write(result["sbs_path"], f"{stem}_{source}_{target}_sbs.pdf")
                    archive.write(result["html_path"], f"{stem}_{source}_{target}_reading.html")
                filename, media_type = f"{stem}_{target}.zip", "application/zip"
            else:
                key, filename, media_type = {
                    "pdf": ("translated_path", f"{stem}_{target}.pdf", "application/pdf"),
                    "sbs": ("sbs_path", f"{stem}_{source}_{target}_sbs.pdf", "application/pdf"),
                    "reading": ("html_path", f"{stem}_{source}_{target}_reading.html", "text/html"),
                }[outputs]
                output_path = result[key]
        for directory in out_dirs:
            background_tasks.add_task(shutil.rmtree, directory, True)
        response = FileResponse(output_path, filename=filename, media_type=media_type,
                                headers=extra, background=background_tasks)
        response_ready = True
        return response
    finally:
        if queued:
            _job.queued -= 1
        for upload in file:
            await upload.close()
        shutil.rmtree(upload_dir, ignore_errors=True)
        if not response_ready:
            for directory in out_dirs:
                shutil.rmtree(directory, ignore_errors=True)


# ---------------------------------------------------------------------------
# Mount Gradio at root, then launch
# ---------------------------------------------------------------------------

app = gr.mount_gradio_app(
    api_app,
    gradio_app,
    path="/",
    favicon_path=str(Path(__file__).parent / "icon.svg"),
    css="""
    label.disabled {
        opacity: 0.4 !important;
        color: var(--body-text-color-subdued, #9ca3af) !important;
    }
    """,
    head="""
<script>
  // Force Gradio's built-in locale selection to Simplified Chinese for this app.
  document.documentElement.lang = "zh-CN";
  try {
    Object.defineProperty(window.navigator, "language", { configurable: true, get: () => "zh-CN" });
    Object.defineProperty(window.navigator, "languages", { configurable: true, get: () => ["zh-CN", "zh"] });
  } catch (_) {}
</script>
""",
)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=7860)
