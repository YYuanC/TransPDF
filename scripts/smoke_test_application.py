"""Start the frozen application and verify its API and Gradio UI before upload."""

import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "executable", nargs="?",
        default="dist/TransPDF/TransPDF.exe" if os.name == "nt" else "dist/TransPDF/TransPDF",
    )
    parser.add_argument("--timeout", type=float, default=90)
    args = parser.parse_args()
    executable = Path(args.executable).resolve(strict=True)
    env = {**os.environ, "GRADIO_ANALYTICS_ENABLED": "False"}
    # Ignore proxy settings for the local health check.
    opener = build_opener(ProxyHandler({}))

    # Launch away from the checkout so missing packaged resources cannot be
    # accidentally supplied by the source tree.
    with tempfile.TemporaryDirectory(prefix="transpdf-smoke-") as workdir:
        log_path = Path(workdir) / "startup.log"
        with log_path.open("wb") as log:
            process = subprocess.Popen(
                [str(executable)], cwd=workdir, env=env,
                stdout=log, stderr=subprocess.STDOUT,
            )
            try:
                deadline = time.monotonic() + args.timeout
                last_error = "No response received"
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        raise RuntimeError(f"Application exited with code {process.returncode}")
                    try:
                        with opener.open("http://127.0.0.1:7860/api/health", timeout=2) as response:
                            health = json.load(response)
                        if health.get("status") != "ok":
                            raise RuntimeError(f"Unexpected health response: {health!r}")
                        with opener.open("http://127.0.0.1:7860/", timeout=2) as response:
                            html = response.read()
                        if b"gradio" not in html.lower():
                            raise RuntimeError("Gradio UI was not served")
                        if process.poll() is not None:
                            raise RuntimeError("Application exited during the health check")
                        print("Packaged application started; API and Gradio UI responded successfully.")
                        return
                    except (URLError, TimeoutError, ConnectionError) as exc:
                        last_error = str(exc)
                    time.sleep(0.5)
                raise RuntimeError(f"Application did not become ready in {args.timeout}s: {last_error}")
            except Exception:
                log.flush()
                print(log_path.read_text(encoding="utf-8", errors="replace"), flush=True)
                raise
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=10)


if __name__ == "__main__":
    main()
