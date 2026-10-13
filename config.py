"""
TransPDF — configuration constants and persistence.
"""

import json
import os
from pathlib import Path

# ---------------------------------------------------------------------------
# HTTP timeouts
# ---------------------------------------------------------------------------

CONNECTION_TIMEOUT = 5.0   # seconds — for connection-test calls
TRANSLATE_TIMEOUT  = 30.0  # seconds — for translation calls

# ---------------------------------------------------------------------------
# Language definitions
# ---------------------------------------------------------------------------

SOURCE_LANGUAGES = [
    ("Auto-detect", "auto"),
    ("English",     "en"),
    ("Nederlands",  "nl"),
    ("Deutsch",     "de"),
    ("Français",    "fr"),
    ("Español",     "es"),
    ("Italiano",    "it"),
    ("Português",   "pt"),
    ("Русский",     "ru"),
    ("日本語",       "ja"),
    ("中文",         "zh"),
    ("한국어",        "ko"),
    ("العربية",      "ar"),
    ("Türkçe",      "tr"),
    ("Polski",      "pl"),
    ("Filipino",    "tl"),
]

TARGET_LANGUAGES = [
    ("中文（简体）" if code == "zh" else label, code)
    for label, code in SOURCE_LANGUAGES[1:]
] + [
    ("中文（繁體，台灣）", "zh-tw"),
    ("中文（繁體，香港）", "zh-hk"),
]

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

OLLAMA_DEFAULT_URL   = "http://localhost:11434"
OLLAMA_DEFAULT_MODEL = "translategemma:latest"
# Can be overridden by LIBRETRANSLATE_URL env var (set in docker-compose when
# the bundled LibreTranslate service is used).
LIBRE_DEFAULT_URL    = os.environ.get("LIBRETRANSLATE_URL", "http://localhost:5000")
# Can be overridden by PDF_TRANSLATE_BACKEND env var.
# Bing is available without an API key, but sends document text externally.
DEFAULT_BACKEND      = os.environ.get("PDF_TRANSLATE_BACKEND", "Bing")

MAX_TRANSLATION_THREADS = 128


def validate_translation_threads(value) -> int:
    """Accept only whole thread counts within the supported range."""
    message = f"Translation threads must be an integer between 1 and {MAX_TRANSLATION_THREADS}."
    if isinstance(value, bool):
        raise ValueError(message)
    try:
        threads = int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(message) from exc
    if isinstance(value, float) and value != threads:
        raise ValueError(message)
    if not 1 <= threads <= MAX_TRANSLATION_THREADS:
        raise ValueError(message)
    return threads


DEFAULT_TRANSLATION_THREADS = validate_translation_threads(
    os.environ.get("PDF_TRANSLATE_THREADS", "3")
)

CJK_FONT_FAMILIES = ("auto", "serif", "sans-serif", "wenkai")
DEFAULT_CJK_FONT_FAMILY = os.environ.get("PDF_TRANSLATE_CJK_FONT_FAMILY", "auto")
if DEFAULT_CJK_FONT_FAMILY not in CJK_FONT_FAMILIES:
    DEFAULT_CJK_FONT_FAMILY = "auto"


def validate_cjk_font_family(value: str) -> str:
    if value not in CJK_FONT_FAMILIES:
        raise ValueError("Chinese font family must be auto, serif, sans-serif, or wenkai.")
    return value

# OCR defaults — kept in sync with ocr_utils constants (duplicated here to
# avoid importing ocr_utils at config load time, which would pull in fitz).
OCR_DEFAULT_SERVICE      = "Tesseract"
OCR_MODES = ("off", "auto", "force")


def validate_ocr_mode(value: str) -> str:
    if value not in OCR_MODES:
        raise ValueError("OCR mode must be off, auto, or force.")
    return value


OCR_LLM_DEFAULT_MODEL    = "glm-ocr"
OCR_LLM_DEFAULT_PROMPT   = (
    "Extract all text from this document image. "
    "Return only the text content, preserving paragraph structure with blank lines "
    "between paragraphs. Do not add explanations, formatting, or markdown."
)

OLLAMA_DEFAULT_SYSTEM_PROMPT = (
    "You are a professional {source_lang} to {target_lang} translator. "
    "Your goal is to accurately convey the meaning and nuances of the original "
    "{source_lang} text while adhering to {target_lang} grammar, vocabulary, "
    "and cultural sensitivities.\n"
    "Produce only the {target_lang} translation, without any additional "
    "explanations or commentary. Please translate the following {source_lang} "
    "text into {target_lang}:\n\n"
    "<text>\n{text}\n</text>"
)

CONFIG_PATH = Path(__file__).parent / "data" / "config.json"

DEFAULT_CONFIG: dict = {
    "backend":              DEFAULT_BACKEND,
    "source":               "auto",
    "target":               "zh",
    "allow_wrap":           False,
    "filter_icons":         True,
    "merge_blocks":         False,
    "detect_tables":        True,
    "ocr_mode":             "auto",
    "translation_threads":  DEFAULT_TRANSLATION_THREADS,
    "cjk_font_family":      DEFAULT_CJK_FONT_FAMILY,
    "ollama_url":           OLLAMA_DEFAULT_URL,
    "ollama_model":         OLLAMA_DEFAULT_MODEL,
    "ollama_system_prompt": OLLAMA_DEFAULT_SYSTEM_PROMPT,
    "ollama_key":           "",
    "libre_url":            LIBRE_DEFAULT_URL,
    "libre_key":            "",
    # OCR settings
    "ocr_service":          OCR_DEFAULT_SERVICE,
    "ocr_ollama_model":     OCR_LLM_DEFAULT_MODEL,
    "ocr_ollama_prompt":    OCR_LLM_DEFAULT_PROMPT,
}

# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def load_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            with CONFIG_PATH.open() as f:
                raw = json.load(f)
                cfg = {**DEFAULT_CONFIG, **raw}
                # Migrate configurations written before the three-state OCR mode.
                if "ocr_mode" not in raw and "force_ocr" in raw:
                    cfg["ocr_mode"] = "force" if raw["force_ocr"] else "auto"
                cfg.pop("force_ocr", None)
                if cfg["ocr_mode"] not in OCR_MODES:
                    cfg["ocr_mode"] = "auto"
            try:
                cfg["translation_threads"] = validate_translation_threads(cfg["translation_threads"])
            except ValueError as exc:
                print(f"[TransPDF] WARNING: {exc} — using default thread count", flush=True)
                cfg["translation_threads"] = DEFAULT_TRANSLATION_THREADS
            try:
                cfg["cjk_font_family"] = validate_cjk_font_family(cfg["cjk_font_family"])
            except ValueError as exc:
                print(f"[TransPDF] WARNING: {exc} — using automatic font selection", flush=True)
                cfg["cjk_font_family"] = "auto"
            return cfg
        except Exception as exc:
            print(f"[TransPDF] WARNING: failed to load {CONFIG_PATH}: {exc} — using defaults", flush=True)
    return dict(DEFAULT_CONFIG)


def save_config(
    backend: str,
    source: str,
    target: str,
    allow_wrap: bool,
    filter_icons: bool,
    ollama_url: str,
    ollama_model: str,
    ollama_system_prompt: str,
    ollama_key: str,
    libre_url: str,
    libre_key: str,
    ocr_service: str = OCR_DEFAULT_SERVICE,
    ocr_ollama_model: str = OCR_LLM_DEFAULT_MODEL,
    ocr_ollama_prompt: str = OCR_LLM_DEFAULT_PROMPT,
    merge_blocks: bool = False,
    detect_tables: bool = True,
    ocr_mode: str = "auto",
    translation_threads: int = DEFAULT_TRANSLATION_THREADS,
    cjk_font_family: str = DEFAULT_CJK_FONT_FAMILY,
) -> str:
    cfg = {
        "backend":              backend,
        "source":               source,
        "target":               target,
        "allow_wrap":           allow_wrap,
        "filter_icons":         filter_icons,
        "merge_blocks":         merge_blocks,
        "detect_tables":        detect_tables,
        "ocr_mode":             validate_ocr_mode(ocr_mode),
        "translation_threads":  validate_translation_threads(translation_threads),
        "cjk_font_family":      validate_cjk_font_family(cjk_font_family),
        "ollama_url":           ollama_url,
        "ollama_model":         ollama_model,
        "ollama_system_prompt": ollama_system_prompt,
        "ollama_key":           ollama_key,
        "libre_url":            libre_url,
        "libre_key":            libre_key,
        "ocr_service":          ocr_service,
        "ocr_ollama_model":     ocr_ollama_model,
        "ocr_ollama_prompt":    ocr_ollama_prompt,
    }
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with CONFIG_PATH.open("w") as f:
        json.dump(cfg, f, indent=2)
    return "✓ Saved."


def update_config(updates: dict) -> None:
    """Merge *updates* into the persisted config and write back to config.json.

    Only keys present in *updates* are changed; all other values are preserved.
    """
    merged = {**load_config(), **updates}
    merged["ocr_mode"] = validate_ocr_mode(merged["ocr_mode"])
    merged["translation_threads"] = validate_translation_threads(merged["translation_threads"])
    merged["cjk_font_family"] = validate_cjk_font_family(merged["cjk_font_family"])
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with CONFIG_PATH.open("w") as f:
        json.dump(merged, f, indent=2)
