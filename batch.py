"""Batch output packaging shared by the web UI and REST API."""

import json
import re
import tempfile
import zipfile
from pathlib import Path


def safe_name(name: str) -> str:
    """Keep client filenames usable on Windows and inside ZIP archives."""
    name = name.replace("\\", "/").rsplit("/", 1)[-1]
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    if not name:
        return "document"
    if name.split(".", 1)[0].upper() in {
        "CON", "PRN", "AUX", "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }:
        name = "_" + name
    return name


def create_batch_archive(
    results: list[dict], source: str, target: str, outputs: str = "all",
    archive_path: str | None = None,
) -> str:
    """Package selected outputs and a report; numbered folders avoid collisions."""
    if archive_path is None:
        archive_path = str(Path(tempfile.mkdtemp()) / "translations.zip")
    source, target = safe_name(source), safe_name(target)
    report = []
    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for index, result in enumerate(results, 1):
            stem = Path(safe_name(result["filename"])).stem
            entry = {"file": result["filename"], "status": result["status"]}
            if result["status"] == "completed":
                folder = f"{index:03d}_{stem}"
                candidates = (
                    ("pdf", "translated_path", f"{stem}_{target}.pdf"),
                    ("sbs", "sbs_path", f"{stem}_{source}_{target}_sbs.pdf"),
                    ("reading", "html_path", f"{stem}_{source}_{target}_reading.html"),
                )
                entry["outputs"] = []
                for kind, key, filename in candidates:
                    if outputs in ("all", kind):
                        member = f"{folder}/{filename}"
                        archive.write(result[key], member)
                        entry["outputs"].append(member)
            else:
                entry["error"] = result.get("error", "Translation failed.")
                if result.get("error_type"):
                    entry["error_type"] = result["error_type"]
            report.append(entry)
        archive.writestr("results.json", json.dumps(report, ensure_ascii=False, indent=2))
    return archive_path
