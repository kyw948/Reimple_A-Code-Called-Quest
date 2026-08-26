"""Start servers, capture README screenshots, then stop servers."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
FIGURE = ROOT / "figure"
# Use generic demo paths so README screenshots do not expose local usernames.
DEMO_ROOT = Path("C:/Reimple")
SAMPLE = DEMO_ROOT / "samples" / "python_basic"
PRACTICE = DEMO_ROOT / "practice"
SAMPLE_TARGET = ROOT / "samples" / "python_basic"
PRACTICE_TARGET = ROOT / "figure" / "_practice_tmp"
VENV_PY = BACKEND / ".venv" / "Scripts" / "python.exe"
NPM = shutil.which("npm.cmd") or shutil.which("npm")

BASE = "http://127.0.0.1:5173"
API = "http://127.0.0.1:8000"


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
    procs = []
    procs.append(
        subprocess.Popen(
            [str(VENV_PY), "-m", "uvicorn", "app.main:app", "--port", "8000", "--host", "127.0.0.1"],
            cwd=str(BACKEND),
            env=env,
            stdout=open(FIGURE / "backend.log", "w", encoding="utf-8"),
            stderr=subprocess.STDOUT,
        )
    )
    procs.append(
        subprocess.Popen(
            [NPM, "run", "dev", "--", "--host", "127.0.0.1", "--port", "5173"],
            cwd=str(FRONTEND),
            env=env,
            stdout=open(FIGURE / "frontend.log", "w", encoding="utf-8"),
            stderr=subprocess.STDOUT,
            shell=False,
        )
    )
    wait_http(f"{API}/docs")
    wait_http(BASE)
    return procs


def stop_servers(procs: list[subprocess.Popen]) -> None:
    for proc in procs:
        if proc.poll() is None:
            proc.terminate()
    deadline = time.time() + 8
    for proc in procs:
        remaining = max(0.1, deadline - time.time())
        try:
            proc.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            proc.kill()


def save_shot(page, path: Path, full_page: bool = False) -> None:
    page.screenshot(path=str(path), full_page=full_page)
    print(f"saved {path.name}", flush=True)


def make_flow_gif(frames: list[Path], out: Path) -> None:
    images = []
    target_w, target_h = 1280, 800
    for frame in frames:
        img = Image.open(frame).convert("RGB")
        img = img.resize((target_w, target_h), Image.Resampling.LANCZOS)
        images.append(img)
    images[0].save(
        out,
        save_all=True,
        append_images=images[1:],
        duration=1500,
        loop=0,
        optimize=False,
    )
    print(f"saved {out.name}", flush=True)


def fill_monaco(page, code: str) -> None:
    page.wait_for_selector(".monaco-editor", timeout=60000)
    for _ in range(40):
        ok = page.evaluate(
            """(code) => {
              const editors = window.monaco?.editor?.getEditors?.() || [];
              if (!editors.length) return false;
              editors[0].setValue(code);
              return true;
            }""",
            code,
        )
        if ok:
            return
        page.wait_for_timeout(500)
    raise RuntimeError("Monaco editor was not ready")


def wait_assess_done(page) -> None:
    # Wait until tree has unlocked / unlocked-looking nodes, or assess panel says complete.
    deadline = time.time() + 240
    while time.time() < deadline:
        completed = page.locator("text=분석 완료").count() > 0
        unlocked = page.locator(".problem-node.unlocked, .problem-node.not-generated").count()
        if completed or unlocked > 0:
            # Give store a moment to settle.
            page.wait_for_timeout(1000)
            return
        page.wait_for_timeout(1500)
    raise RuntimeError("Assess did not complete in time")


def open_first_problem(page) -> None:
    # Prefer unlocked candidates / problems; skip locked.
    selectors = [
        ".problem-node.unlocked:not([disabled])",
        ".problem-node.not-generated:not(.locked):not([disabled])",
        ".problem-node.active:not([disabled])",
        ".problem-node:not(.locked):not([disabled])",
    ]
    node = None
    for sel in selectors:
        loc = page.locator(sel)
        if loc.count() > 0:
            node = loc.first
            break
    if node is None:
        raise RuntimeError("No selectable problem nodes found")

    node.click()
    # Generation may take a bit; then detail load + Monaco CDN.
    page.wait_for_selector(".monaco-editor, .problem-prompt", timeout=180000)
    page.wait_for_selector(".monaco-editor", timeout=180000)
    # Scroll editor so the blank (NotImplementedError) is visible for README.
    page.evaluate(
        """() => {
          const editors = window.monaco?.editor?.getEditors?.() || [];
          if (!editors.length) return;
          const editor = editors[0];
          const model = editor.getModel();
          if (!model) return;
          const matches = model.findMatches('NotImplementedError', false, false, false, null, true);
          if (matches.length) {
            const range = matches[0].range;
            editor.revealLineInCenter(range.startLineNumber);
            editor.setSelection(range);
          }
        }"""
    )
    page.wait_for_timeout(800)


def ensure_demo_paths() -> None:
    """Copy sample repo to C:/Reimple/... so stored paths hide local usernames."""
    if sys.platform != "win32":
        global SAMPLE, PRACTICE
        SAMPLE = SAMPLE_TARGET
        PRACTICE = PRACTICE_TARGET
        PRACTICE.mkdir(parents=True, exist_ok=True)
        return

    if DEMO_ROOT.exists():
        shutil.rmtree(DEMO_ROOT, ignore_errors=True)
    DEMO_ROOT.mkdir(parents=True, exist_ok=True)
    shutil.copytree(SAMPLE_TARGET, SAMPLE)
    PRACTICE.mkdir(parents=True, exist_ok=True)


def capture() -> None:
    FIGURE.mkdir(parents=True, exist_ok=True)
    ensure_demo_paths()

    procs = start_servers()
    flow_frames: list[Path] = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport={"width": 1440, "height": 900}, device_scale_factor=1.25)
            page = context.new_page()
            page.set_default_timeout(90000)

            # --- Setup ---
            page.goto(BASE)
            page.wait_for_selector("#setup-title")
            page.get_by_role("button", name="로컬 경로").click()
            page.locator('label.field:has-text("로컬 경로") input').fill(str(SAMPLE))
            page.locator('label.field:has-text("연습 폴더 경로") input').fill(str(PRACTICE))
            page.wait_for_timeout(500)
            setup_shot = FIGURE / "_flow_1_setup.png"
            save_shot(page, setup_shot)
            flow_frames.append(setup_shot)

            page.get_by_role("button", name="분석").click()
            page.wait_for_url("**/projects/*/analyze", timeout=120000)
            page.wait_for_selector(".analyze-page h1")
            page.wait_for_selector(".project-analysis-summary, .file-tree", timeout=120000)
            page.wait_for_timeout(1500)

            analyze_shot = FIGURE / "analyze.png"
            save_shot(page, analyze_shot, full_page=True)
            # Also keep a viewport frame for the gif.
            analyze_view = FIGURE / "_flow_2_analyze.png"
            save_shot(page, analyze_view)
            flow_frames.append(analyze_view)

            # --- Practice ---
            page.get_by_role("button", name="연습 시작").click()
            page.wait_for_url("**/projects/*/practice", timeout=180000)
            page.wait_for_selector(".practice-page")
            wait_assess_done(page)
            open_first_problem(page)

            problem_shot = FIGURE / "problem.png"
            save_shot(page, problem_shot)
            flow_frames.append(problem_shot)

            source = page.locator(".problem-labels span").first.inner_text()
            symbol = page.locator(".problem-labels strong").inner_text()
            print(f"selected problem: {source}::{symbol}", flush=True)

            sample_code = (SAMPLE_TARGET / "src" / "math_utils.py").read_text(encoding="utf-8")
            string_code = (SAMPLE_TARGET / "src" / "string_utils.py").read_text(encoding="utf-8")
            solution = sample_code if "math_utils" in source.replace("\\", "/") else string_code
            fill_monaco(page, solution)
            page.evaluate(
                """(symbol) => {
                  const editors = window.monaco?.editor?.getEditors?.() || [];
                  if (!editors.length) return;
                  const editor = editors[0];
                  const model = editor.getModel();
                  if (!model) return;
                  const matches = model.findMatches('def ' + symbol, false, false, true, null, true);
                  if (matches.length) {
                    editor.revealLineInCenter(matches[0].range.startLineNumber);
                  }
                }""",
                symbol,
            )
            page.wait_for_timeout(400)

            page.get_by_role("button", name="제출").click()
            # Desktop result sidebar (mobile duplicate is hidden and breaks visibility waits).
            page.locator(".result-sidebar .result-status").wait_for(state="visible", timeout=120000)
            page.wait_for_timeout(1500)

            practice_shot = FIGURE / "practice.png"
            save_shot(page, practice_shot)
            flow_frames.append(practice_shot)

            make_flow_gif(flow_frames, FIGURE / "flow.gif")
            browser.close()
    finally:
        stop_servers(procs)


if __name__ == "__main__":
    try:
        capture()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise
