"""Capture only paper-mode screenshot with sanitized paths."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
FIGURE = ROOT / "figure"
DEMO_ROOT = Path("C:/Reimple")
PAPER_PRACTICE = DEMO_ROOT / "practice" / "paper"
VENV_PY = BACKEND / ".venv" / "Scripts" / "python.exe"
NPM = shutil.which("npm.cmd") or shutil.which("npm")
BASE = "http://127.0.0.1:5173"
API = "http://127.0.0.1:8000"
ARXIV_URL = "https://arxiv.org/abs/1412.6980"


def wait_http(url: str, timeout: float = 90.0) -> None:
    import urllib.request

    start = time.time()
    last_err = None
    while time.time() - start < timeout:
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                if resp.status < 500:
                    return
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            time.sleep(1)
    raise RuntimeError(f"Timed out waiting for {url}: {last_err}")


def start_servers() -> list[subprocess.Popen]:
    env = os.environ.copy()
    env["FRONTEND_ORIGIN"] = "http://127.0.0.1:5173"
    FIGURE.mkdir(parents=True, exist_ok=True)
    procs = [
        subprocess.Popen(
            [str(VENV_PY), "-m", "uvicorn", "app.main:app", "--port", "8000", "--host", "127.0.0.1"],
            cwd=str(BACKEND),
            env=env,
            stdout=open(FIGURE / "backend.log", "w", encoding="utf-8"),
            stderr=subprocess.STDOUT,
        ),
        subprocess.Popen(
            [NPM, "run", "dev", "--", "--host", "127.0.0.1", "--port", "5173"],
            cwd=str(FRONTEND),
            env=env,
            stdout=open(FIGURE / "frontend.log", "w", encoding="utf-8"),
            stderr=subprocess.STDOUT,
        ),
    ]
    wait_http(f"{API}/docs")
    wait_http(BASE)
    return procs


def stop_servers(procs: list[subprocess.Popen]) -> None:
    for proc in procs:
        if proc.poll() is None:
            proc.terminate()
    for proc in procs:
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def main() -> None:
    if DEMO_ROOT.exists():
        # Keep samples from previous capture if present; only ensure paper practice dir.
        PAPER_PRACTICE.mkdir(parents=True, exist_ok=True)
    else:
        DEMO_ROOT.mkdir(parents=True, exist_ok=True)
        PAPER_PRACTICE.mkdir(parents=True, exist_ok=True)

    procs = start_servers()
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_context(viewport={"width": 1440, "height": 900}, device_scale_factor=1.25).new_page()
            page.set_default_timeout(180000)

            page.goto(BASE)
            page.get_by_role("button", name="논문").click()
            page.locator('input[placeholder*="arxiv"]').fill(ARXIV_URL)
            page.locator('label.field:has-text("연습 폴더") input').fill(str(PAPER_PRACTICE))
            page.locator("button.setup-submit").click()
            page.wait_for_url("**/projects/*/analyze", timeout=300000)
            page.wait_for_selector(".paper-stepper", timeout=120000)
            print("paper project ready", flush=True)

            # Start structure planning (long Gemini call).
            page.locator("button.paper-step-action").first.click()
            # Wait via CSS class — avoids Korean encoding issues in selectors.
            page.wait_for_selector("details.paper-step-details", timeout=600000)
            print("plan completed", flush=True)
            page.locator("details.paper-step-details summary").first.click()
            page.wait_for_timeout(800)

            # Start code generation and wait for progress UI.
            codegen = page.locator("button.paper-step-action").filter(has_not_text="연습")
            # Prefer the generate button in step 2.
            buttons = page.locator("button.paper-step-action")
            for i in range(buttons.count()):
                text = buttons.nth(i).inner_text()
                if "생성" in text and "연습" not in text and "구조" not in text:
                    buttons.nth(i).click()
                    print(f"clicked codegen: {text!r}", flush=True)
                    break

            deadline = time.time() + 240
            while time.time() < deadline:
                if page.locator(".codegen-status, .codegen-file-list, .progress-bar").count() > 0:
                    page.wait_for_timeout(5000)
                    break
                # Also accept completed state quickly.
                if page.locator(".paper-step-card.completed").count() >= 2:
                    break
                page.wait_for_timeout(2000)

            page.wait_for_timeout(1500)
            page.screenshot(path=str(FIGURE / "paper.png"), full_page=True)
            page.screenshot(path=str(FIGURE / "paper_view.png"), full_page=False)
            print("saved paper.png", flush=True)
            browser.close()
    finally:
        stop_servers(procs)


if __name__ == "__main__":
    main()
