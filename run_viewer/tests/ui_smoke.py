"""Browser smoke test of the running viewer: every run loads, panels render, no console errors.

    <venv with playwright>/bin/python run_viewer/tests/ui_smoke.py http://127.0.0.1:5173 <screenshot dir>

Exits non-zero on any console error, failed request or missing panel.
"""

import json
import os
import sys
import urllib.request

from playwright.sync_api import sync_playwright

PANELS = [".agent", ".panel.images", ".traj-panel", ".metrics", ".params", ".rec", ".strip"]


def _step(page) -> int:
    return int(page.locator(".controls input[type=range]").input_value())


def check_controls(page, base: str, run: dict) -> list[str]:
    """Play advances, pause stops, next / previous link and step buttons move the cursor."""
    bad = []
    page.goto(f"{base}/?run={run['run_id']}&link=2")
    page.wait_for_selector(".agent")
    page.select_option(".controls select >> nth=1", "8")  # speed 8 steps/s
    page.click("button.play")
    page.wait_for_timeout(1500)
    page.click("button.play")
    moved = _step(page)
    page.wait_for_timeout(600)
    if moved < 3:
        bad.append(f"play did not advance (step {moved})")
    if _step(page) != moved:
        bad.append("pause did not stop playback")
    page.click("text=step ▶")
    if _step(page) != moved + 1:
        bad.append("step forward did not advance by one")
    page.click("text=link ⏭")
    page.wait_for_timeout(800)
    if page.locator(".controls select >> nth=0").input_value() != "3" or _step(page) != 0:
        bad.append("next link did not open link 3 at step 1")
    page.click("text=⏮ link")
    page.wait_for_timeout(800)
    if page.locator(".controls select >> nth=0").input_value() != "2":
        bad.append("previous link did not go back to link 2")
    print("controls:", "ok" if not bad else bad, flush=True)
    return [f"controls: {b}" for b in bad]


def main(base: str, shots: str) -> int:
    os.makedirs(shots, exist_ok=True)
    runs = json.load(urllib.request.urlopen(f"{base}/api/runs"))
    failures = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1680, "height": 1300})
        errors = []
        page.on("console", lambda m: errors.append(f"console {m.type}: {m.text}") if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
        page.on("requestfailed", lambda r: errors.append(f"requestfailed: {r.url}"))
        page.on("response", lambda r: errors.append(f"http {r.status}: {r.url}") if r.status >= 400 else None)
        for r in runs:
            if not r["built"]:
                failures.append(f"{r['run_id']}: not built ({r['error']})")
                continue
            link = 2 if r["links_built"] >= 2 else 1
            errors.clear()
            page.goto(f"{base}/?run={r['run_id']}&link={link}")
            page.wait_for_selector(".agent", timeout=20000)
            # step to the middle of the link
            n = int(page.locator(".controls input[type=range]").get_attribute("max") or 0)
            page.locator(".controls input[type=range]").fill(str(n // 2))
            page.wait_for_timeout(400)
            missing = [s for s in PANELS if page.locator(s).count() == 0]
            if r["combination"].split("_")[2] != "sequential" and page.locator(".comm").count() == 0:
                missing.append(".comm")
            page.screenshot(path=os.path.join(shots, f"{r['run_id']}.png"), full_page=True)
            status = "ok" if not (missing or errors) else f"missing={missing} errors={errors[:5]}"
            print(f"{r['run_id']}: {n + 1} steps in link {link}: {status}", flush=True)
            if missing or errors:
                failures.append(f"{r['run_id']}: {status}")
        failures += check_controls(page, base, next(r for r in runs if r["built"] and r["links_built"] >= 3))
        browser.close()
    print("FAIL\n" + "\n".join(failures) if failures else "ALL OK")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
