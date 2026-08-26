"""Capture README_updated.md screenshots with sanitized demo paths."""
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
DEMO_ROOT = Path("C:/Reimple")
SAMPLE = DEMO_ROOT / "samples" / "python_basic"
PRACTICE = DEMO_ROOT / "practice"
PAPER_PRACTICE = DEMO_ROOT / "practice" / "paper"
SAMPLE_TARGET = ROOT / "samples" / "python_basic"
VENV_PY = BACKEND / ".venv" / "Scripts" / "python.exe"
NPM = shutil.which("npm.cmd") or shutil.which("npm")

BASE = "http://127.0.0.1:5173"
API = "http://127.0.0.1:8000"
# Short-ish public paper for Paper2Code demo screenshots.
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
            shell=False,
        ),
    ]
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
    for frame in frames:
        img = Image.open(frame).convert("RGB").resize((1280, 800), Image.Resampling.LANCZOS)
        images.append(img)
    images[0].save(out, save_all=True, append_images=images[1:], duration=1500, loop=0, optimize=False)
    print(f"saved {out.name}", flush=True)


def ensure_demo_paths() -> None:
    if sys.platform != "win32":
        raise RuntimeError("This capture script expects Windows demo paths under C:/Reimple")
    if DEMO_ROOT.exists():
        shutil.rmtree(DEMO_ROOT, ignore_errors=True)
    DEMO_ROOT.mkdir(parents=True, exist_ok=True)
    shutil.copytree(SAMPLE_TARGET, SAMPLE)
    PRACTICE.mkdir(parents=True, exist_ok=True)
    PAPER_PRACTICE.mkdir(parents=True, exist_ok=True)


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
    deadline = time.time() + 240
    while time.time() < deadline:
        if page.locator("text=분석 완료").count() > 0:
            page.wait_for_timeout(800)
            return
        if page.locator(".problem-node.unlocked, .problem-node.not-generated").count() > 0:
            page.wait_for_timeout(800)
            return
        page.wait_for_timeout(1500)
    raise RuntimeError("Assess did not complete in time")


def open_first_unlocked(page) -> None:
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
    page.wait_for_selector(".monaco-editor", timeout=180000)
    page.evaluate(
        """() => {
          const editors = window.monaco?.editor?.getEditors?.() || [];
          if (!editors.length) return;
          const editor = editors[0];
          const model = editor.getModel();
          if (!model) return;
          const matches = model.findMatches('NotImplementedError', false, false, false, null, true);
          if (matches.length) {
            editor.revealLineInCenter(matches[0].range.startLineNumber);
            editor.setSelection(matches[0].range);
          }
        }"""
    )
    page.wait_for_timeout(800)


def capture_repo_flow(page) -> list[Path]:
    flow_frames: list[Path] = []
    page.goto(BASE)
    page.wait_for_selector("#setup-title")
    page.get_by_role("button", name="로컬 경로").click()
    page.locator('label.field:has-text("로컬 경로") input').fill(str(SAMPLE))
    page.locator('label.field:has-text("연습 폴더 경로") input').fill(str(PRACTICE))
    page.wait_for_timeout(400)
    setup_shot = FIGURE / "_flow_1_setup.png"
    save_shot(page, setup_shot)
    flow_frames.append(setup_shot)

    page.get_by_role("button", name="분석").click()
    page.wait_for_url("**/projects/*/analyze", timeout=120000)
    page.wait_for_selector(".project-analysis-summary, .file-tree", timeout=120000)
    page.wait_for_timeout(1200)
    save_shot(page, FIGURE / "analyze.png", full_page=True)
    analyze_view = FIGURE / "_flow_2_analyze.png"
    save_shot(page, analyze_view)
    flow_frames.append(analyze_view)

    page.get_by_role("button", name="연습 시작").click()
    page.wait_for_url("**/projects/*/practice", timeout=180000)
    page.wait_for_selector(".practice-page")

    # Warmup while analysis may still be running / before selecting a problem.
    page.wait_for_selector(".warmup-panel", timeout=120000)
    for _ in range(60):
        if page.locator(".warmup-card").count() > 0:
            break
        page.wait_for_timeout(1000)
    if page.locator(".warmup-card").count() > 0:
        first_option = page.locator(".warmup-card").first.locator("label, input[type='radio']").first
        if first_option.count():
            first_option.click()
        reveal = page.locator(".warmup-card").first.get_by_role("button", name="정답 확인")
        if reveal.count():
            reveal.click()
            page.wait_for_timeout(400)
    save_shot(page, FIGURE / "warmup.png")

    wait_assess_done(page)
    page.wait_for_selector(".problem-node", timeout=120000)
    page.wait_for_timeout(1000)

    # Prefer showing unlocked/locked mix in the sidebar after assess.
    # Keep warmup visible only for warmup.png (already saved above).
    open_first_unlocked(page)
    save_shot(page, FIGURE / "tree.png")
    save_shot(page, FIGURE / "problem.png")
    flow_frames.append(FIGURE / "problem.png")

    # If a locked node exists, also capture locked state overlay for tree figure.
    locked = page.locator(".problem-node.locked")
    if locked.count() > 0:
        locked.first.click()
        page.wait_for_timeout(700)
        save_shot(page, FIGURE / "tree.png")
        # Return to unlocked problem for the rest of the flow.
        open_first_unlocked(page)
        save_shot(page, FIGURE / "problem.png")

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
          if (matches.length) editor.revealLineInCenter(matches[0].range.startLineNumber);
        }""",
        symbol,
    )
    page.wait_for_timeout(300)
    page.get_by_role("button", name="제출").click()
    page.locator(".result-sidebar .result-status").wait_for(state="visible", timeout=120000)
    page.wait_for_timeout(1200)
    save_shot(page, FIGURE / "practice.png")
    flow_frames.append(FIGURE / "practice.png")
    make_flow_gif(flow_frames, FIGURE / "flow.gif")
    return flow_frames


def capture_paper_flow(page) -> None:
    page.goto(BASE)
    page.wait_for_selector("#setup-title")
    page.get_by_role("button", name="논문").click()
    page.locator('label.field:has-text("arXiv URL") input').fill(ARXIV_URL)
    page.locator('label.field:has-text("연습 폴더 경로") input').fill(str(PAPER_PRACTICE))
    page.get_by_role("button", name="논문 분석").click()
    page.wait_for_url("**/projects/*/analyze", timeout=300000)
    page.wait_for_selector(".paper-analysis-summary, .paper-stepper", timeout=120000)
    page.wait_for_timeout(1000)

    plan_btn = page.get_by_role("button", name="구조 설계 시작")
    plan_btn.click()
    # Wait until plan result is available.
    page.wait_for_selector("text=설계 결과 보기, text=코드 생성 시작", timeout=600000)
    details = page.locator("details.paper-step-details")
    if details.count():
        details.first.locator("summary").click()
        page.wait_for_timeout(600)

    codegen_btn = page.get_by_role("button", name="코드 생성 시작")
    if codegen_btn.count():
        codegen_btn.click()
        # Wait for progress UI with at least one file counted.
        deadline = time.time() + 300
        while time.time() < deadline:
            if page.locator(".codegen-status, .progress-bar, text=코드 생성 중").count() > 0:
                page.wait_for_timeout(4000)
                break
            page.wait_for_timeout(1500)

    page.wait_for_timeout(1500)
    save_shot(page, FIGURE / "paper.png", full_page=True)
    # Also a viewport crop for README readability.
    save_shot(page, FIGURE / "paper_view.png")


def capture() -> None:
    FIGURE.mkdir(parents=True, exist_ok=True)
    ensure_demo_paths()
    procs = start_servers()
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport={"width": 1440, "height": 900}, device_scale_factor=1.25)
            page = context.new_page()
            page.set_default_timeout(120000)

            print("=== repo flow ===", flush=True)
            capture_repo_flow(page)

            print("=== paper flow ===", flush=True)
            capture_paper_flow(page)
            browser.close()
    finally:
        stop_servers(procs)


if __name__ == "__main__":
    try:
        capture()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise
