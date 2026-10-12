"""Multi-document UI and API behavior, including partial failures and cleanup."""

import asyncio
import io
import json
import zipfile
from pathlib import Path
from unittest.mock import patch

import fitz
import gradio as gr
import pytest
from starlette.testclient import TestClient

import app
from batch import safe_name


client = TestClient(app.api_app, raise_server_exceptions=False)
UI_ARGS = ("en", "nl", "Google", "", "", "", "", "", "")


def make_outputs(folder, pdf=None):
    folder.mkdir(parents=True, exist_ok=True)
    translated, sbs, html = folder / "translated.pdf", folder / "sbs.pdf", folder / "reading.html"
    translated.write_bytes(pdf or b"%PDF fake translated")
    sbs.write_bytes(pdf or b"%PDF fake comparison")
    html.write_text("<html>translated</html>", encoding="utf-8")
    return tuple(map(str, (translated, sbs, html)))


def uploads(names, content):
    return [("file", (name, content, "application/pdf")) for name in names]


def read_zip(response):
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/zip"
    archive = zipfile.ZipFile(io.BytesIO(response.content))
    return archive, json.loads(archive.read("results.json"))


@pytest.mark.parametrize("outputs, count, suffix", [
    ("pdf", 1, "_nl.pdf"), ("sbs", 1, "_en_nl_sbs.pdf"),
    ("reading", 1, "_en_nl_reading.html"), ("all", 3, None),
])
def test_api_batch_formats_and_duplicate_names(reset_job, minimal_pdf, tmp_path, outputs, count, suffix):
    output_files = [make_outputs(tmp_path / str(i)) for i in range(2)]
    input_paths = []

    def translate(path, *args, **kwargs):
        input_paths.append(path)
        assert Path(path).read_bytes() == minimal_pdf.read_bytes()
        return output_files[len(input_paths) - 1]

    with patch("app.translate_sync", side_effect=translate):
        response = client.post("/api/translate", files=uploads(["doc.pdf", "doc.pdf"], minimal_pdf.read_bytes()),
                               data={"source": "en", "target": "nl", "outputs": outputs})
    archive, report = read_zip(response)
    assert [r["status"] for r in report] == ["completed", "completed"]
    assert len(archive.namelist()) == 2 * count + 1
    assert len(set(archive.namelist())) == len(archive.namelist())
    assert report[0]["outputs"][0].startswith("001_doc/")
    assert report[1]["outputs"][0].startswith("002_doc/")
    for result in report:
        assert len(result["outputs"]) == count
        assert all(archive.read(member) for member in result["outputs"])
        if suffix:
            assert result["outputs"][0].endswith(suffix)
    assert len(set(input_paths)) == 2
    assert all(Path(path).name == "doc.pdf" for path in input_paths)
    assert all(not Path(path).exists() for path in input_paths)
    assert all(not Path(paths[0]).parent.exists() for paths in output_files)
    assert client.get("/api/health").json()["job"]["last"]["status"] == "completed"


def test_api_batch_continues_after_failure(reset_job, minimal_pdf, tmp_path):
    outputs = make_outputs(tmp_path / "output")
    with patch("app.translate_sync", side_effect=[ValueError("No translatable text blocks found"), outputs]) as translate:
        response = client.post("/api/translate", files=uploads(["empty.pdf", "good.pdf"], minimal_pdf.read_bytes()))
    archive, report = read_zip(response)
    assert translate.call_count == 2
    assert report[0]["status"] == "failed"
    assert report[0]["error_type"] == "no_text"
    assert report[1]["status"] == "completed"
    assert "002_good/good_zh.pdf" in archive.namelist()
    assert client.get("/api/health").json()["job"]["last"]["status"] == "partial"


def test_api_batch_all_fail_returns_report(reset_job, minimal_pdf):
    with patch("app.translate_sync", side_effect=RuntimeError("Backend unavailable")):
        response = client.post("/api/translate", files=uploads(["one.pdf", "two.pdf"], minimal_pdf.read_bytes()))
    archive, report = read_zip(response)
    assert archive.namelist() == ["results.json"]
    assert all(r["status"] == "failed" for r in report)
    assert client.get("/api/health").json()["job"]["last"]["status"] == "failed"


def test_api_batch_cancel_stops_before_next_file_and_cleans_outputs(reset_job, minimal_pdf, tmp_path):
    outputs = make_outputs(tmp_path / "output")

    def translate(*args, **kwargs):
        kwargs["cancel_event"].set()
        return outputs

    with patch("app.translate_sync", side_effect=translate) as mock:
        response = client.post("/api/translate", files=uploads(["one.pdf", "two.pdf"], minimal_pdf.read_bytes()))
    assert response.status_code == 409
    assert mock.call_count == 1
    assert not Path(outputs[0]).exists()
    assert client.get("/api/health").json()["job"]["last"]["status"] == "cancelled"


def test_api_batch_total_upload_limit(reset_job, monkeypatch):
    monkeypatch.setattr(app, "_MAX_UPLOAD_BYTES", 10)
    with patch("app.translate_sync") as translate:
        response = client.post("/api/translate", files=uploads(["one.pdf", "two.pdf"], b"123456"))
    assert response.status_code == 413
    translate.assert_not_called()


def test_api_batch_queue_full(reset_job, minimal_pdf):
    app._job.running, app._job.queued = True, 1
    with patch("app.translate_sync") as translate:
        response = client.post("/api/translate", files=uploads(["one.pdf", "two.pdf"], minimal_pdf.read_bytes()))
    assert response.status_code == 429
    translate.assert_not_called()


def test_api_batch_auth_required(reset_job, monkeypatch, minimal_pdf):
    monkeypatch.setenv("PDF_TRANSLATE_API_KEY", "secret")
    response = client.post("/api/translate", files=uploads(["one.pdf", "two.pdf"], minimal_pdf.read_bytes()))
    assert response.status_code == 401


def test_single_file_zip_cleanup(reset_job, minimal_pdf, tmp_path):
    outputs = make_outputs(tmp_path / "output")
    with patch("app.translate_sync", return_value=outputs):
        response = client.post("/api/translate", files=uploads(["doc.pdf"], minimal_pdf.read_bytes()),
                               data={"source": "en", "target": "nl", "outputs": "all"})
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert set(archive.namelist()) == {"doc_nl.pdf", "doc_en_nl_sbs.pdf", "doc_en_nl_reading.html"}
    assert not Path(outputs[0]).parent.exists()


def test_api_batch_real_pipeline(reset_job, minimal_pdf, isolated_config):
    # Mock only the external backend; read, render and package real PDFs.
    with patch("backends.google.call", return_value="Hallo wereld."):
        response = client.post("/api/translate", files=uploads(["one.pdf", "two.pdf"], minimal_pdf.read_bytes()),
                               data={"source": "en", "target": "nl", "service": "Google", "outputs": "all"})
    archive, report = read_zip(response)
    for result in report:
        translated, sbs, html = result["outputs"]
        with fitz.open(stream=archive.read(translated), filetype="pdf") as document:
            assert "Hallo wereld." in document[0].get_text()
        with fitz.open(stream=archive.read(sbs), filetype="pdf") as document:
            assert len(document) == 1
        assert "Hallo wereld." in archive.read(html).decode("utf-8")


def test_ui_upload_and_clear_reset_all_results(minimal_pdf):
    uploaded = app.on_files_upload([str(minimal_pdf), str(minimal_pdf)])
    assert uploaded[13]["value"] == "0"
    assert len(uploaded[12]) == 2
    assert uploaded[8] is None
    assert uploaded[14] == str(minimal_pdf)
    cleared = app.on_files_upload(None)
    assert cleared[12] == []
    assert cleared[13]["choices"] == []
    assert cleared[8] is None and cleared[14] is None
    assert all(cleared[index]["value"] is None for index in (0, 1, 2, 11))


def test_ui_batch_failures_keep_success_downloads_and_selectable_preview(minimal_pdf, tmp_path):
    outputs = make_outputs(tmp_path / "output", minimal_pdf.read_bytes())

    def translate(path, *args):
        if Path(path).name == "broken.pdf":
            raise RuntimeError("Invalid PDF")
        yield (gr.update(), gr.update(), gr.update(), "orig.png", None, "Reading", 0, "Page 1 / 1", None,
               gr.update(), gr.update())
        yield (gr.update(value=outputs[0]), gr.update(value=outputs[1]), gr.update(value=outputs[2]),
               "orig.png", "trans.png", "Done", 0, "Page 1 / 1", outputs[0],
               gr.update(visible=True), gr.update(visible=True))

    with patch("app.translate", side_effect=translate):
        steps = list(app.translate_files([str(tmp_path / "broken.pdf"), str(minimal_pdf)], *UI_ARGS))
    final = steps[-1]
    assert all(len(step) == 17 for step in steps)
    assert steps[0][15]["interactive"] is False
    assert final[15]["interactive"] is True and final[16]["interactive"] is True
    assert "成功 1，失败 1" in final[5]
    assert final[0]["value"] == [outputs[0]]
    assert final[13]["value"] == "1"
    assert final[8] == outputs[0]
    with zipfile.ZipFile(final[11]["value"]) as archive:
        report = json.loads(archive.read("results.json"))
    assert [r["status"] for r in report] == ["failed", "completed"]
    preview = app.select_preview("1", final[12])
    assert preview[4] == outputs[0]
    assert preview[7] == str(minimal_pdf)
    failed_preview = app.select_preview("0", final[12])
    assert failed_preview[4] is None
    assert failed_preview[5]["visible"] is False


def test_ui_batch_real_pipeline_and_navigation(minimal_pdf, isolated_config, tmp_path):
    second_pdf = tmp_path / "two-pages.pdf"
    with fitz.open() as document:
        for _ in range(2):
            document.new_page().insert_text((72, 72), "Second document.")
        document.save(second_pdf)
    with patch("backends.google.call", return_value="Hallo wereld."):
        steps = list(app.translate_files([str(minimal_pdf), str(second_pdf)], *UI_ARGS))
    final = steps[-1]
    assert len(final[0]["value"]) == 2
    assert all(r["status"] == "completed" for r in final[12])
    for index, pages in (("0", 1), ("1", 2)):
        preview = app.select_preview(index, final[12])
        assert preview[0] and preview[1]
        assert preview[3] == f"Page 1 / {pages}"
        original, translated = preview[7], preview[4]
        next_page = app.go_next(original, translated, 0)
        assert next_page[2] == 1 % pages
        assert next_page[3] == f"Page {1 % pages + 1} / {pages}"

    # Exercise Gradio's actual File/Image/state serialization, as used by the
    # browser, so list downloads and all 17 wired outputs are checked together.
    from gradio.state_holder import SessionState
    block_fn = next(fn for fn in app.gradio_app.fns.values() if fn.fn is app.translate_files)

    async def postprocess():
        state = SessionState(app.gradio_app)
        for step in steps:
            rendered = await app.gradio_app.postprocess_data(block_fn, step, state)
        return rendered, state

    rendered, state = asyncio.run(postprocess())
    assert len(rendered[0]["value"]) == 2
    assert rendered[11]["value"]["orig_name"] == "translations.zip"
    assert state[app.batch_results_state._id][0]["status"] == "completed"


def test_ui_empty_upload_message():
    final = list(app.translate_files(None, *UI_ARGS))[-1]
    assert "请先上传" in final[5]
    assert final[15]["interactive"] is True


@pytest.mark.parametrize("name, expected", [
    ("../../doc.pdf", "doc.pdf"), ("C:\\files\\doc.pdf", "doc.pdf"),
    ("CON.pdf", "_CON.pdf"), ("bad?.pdf", "bad_.pdf"), ("...", "document"),
])
def test_client_names_are_safe(name, expected):
    assert safe_name(name) == expected
