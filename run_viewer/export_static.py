"""Export the viewer as one self-contained static site (no server, no raw run folders).

    python -m run_viewer.export_static --catalog runs.json --bundles bundles \
        --web run_viewer/web/dist-static --out site [--runs v2full__S_SP,v3combos__O_GR]

The site holds the built web app plus the derived data the viewer shows (per-link JSON,
images). Absolute machine paths are reduced to file names, server-only fields are dropped,
and a live run becomes a dated snapshot. The same folder works as:
  * a GitHub Pages site (see .github/workflows/pages.yml written into it),
  * a Hugging Face static Space (README.md front matter),
  * a folder on any web server, or locally: python -m http.server inside it.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import shutil
from datetime import datetime, timezone

ABS_PATH = re.compile(r"^(/home/|/tmp/|/data/|/mnt/|/root/|/Users/|[A-Za-z]:\\)")
# a path inside text (tool arguments, LLM output): shortened to …/<file name>
EMBEDDED_PATH = re.compile(r"/(?:home|tmp|data|mnt|root|Users)/[A-Za-z0-9_.\-/]+")
DROP_KEYS = {"run_dir", "workdir", "config_used"}

README = """---
title: MAS-Aviary Run Viewer
emoji: ✈️
colorFrom: indigo
colorTo: green
sdk: static
app_file: index.html
pinned: false
---

# MAS-Aviary run viewer

Interactive playback of multi-agent MDO runs (DLR-F25 aircraft, Qwen3-32B agents) for each
coordination combination: agent steps and LLM output, tool calls, aircraft / SU2 / mission
images, parameters at link N and N+1, coordination metrics, and the orchestrator / blackboard
communication. Play, pause, step, and move between links.

This is a static snapshot: all data is in `data/`, nothing is fetched from elsewhere.

## Open it

* **GitHub Pages:** enable Pages for this repository (Settings → Pages → Source: GitHub Actions);
  the included workflow publishes it on every push.
* **Locally:** `python -m http.server 8000` in this folder, then open http://localhost:8000
  (opening index.html directly from disk does not work: browsers block file:// fetches).
* **Hugging Face:** push this folder to a Space; the front matter above makes it a static Space.

## What is in it

{runs}

Exported {exported} from MAS-Aviary `run_viewer` ({commit}). Values are shown as the run files
recorded them: a link that did not fly has no fuel value, missing data is marked, and SU2 images
re-solved after the run (when the run did not keep its files) are labelled "re-solved".
"""

PAGES_WORKFLOW = """name: Deploy viewer to GitHub Pages
on:
  push:
    branches: [main]
  workflow_dispatch:
permissions:
  contents: read
  pages: write
  id-token: write
concurrency:
  group: pages
  cancel-in-progress: true
jobs:
  deploy:
    runs-on: ubuntu-latest
    environment:
      name: github-pages
      url: ${{ steps.deployment.outputs.page_url }}
    steps:
      - uses: actions/checkout@v4
      - uses: actions/configure-pages@v5
      - uses: actions/upload-pages-artifact@v3
        with:
          path: .
      - id: deployment
        uses: actions/deploy-pages@v4
"""


def sanitize(obj):
    """Drop server-only keys; reduce absolute paths to their file name."""
    if isinstance(obj, dict):
        return {k: sanitize(v) for k, v in obj.items() if k not in DROP_KEYS}
    if isinstance(obj, list):
        return [sanitize(v) for v in obj]
    if isinstance(obj, str):
        if ABS_PATH.match(obj) and "\n" not in obj and " " not in obj:
            return os.path.basename(obj.rstrip("/\\")) or obj
        return EMBEDDED_PATH.sub(lambda m: "…/" + (os.path.basename(m.group(0).rstrip("/")) or ""), obj)
    return obj


def export_run(entry: dict, bundles: str, out: str, stamp: str) -> dict | None:
    run_id = entry["run_id"]
    src = os.path.join(bundles, run_id)
    run = json.load(open(os.path.join(src, "run.json"))) if os.path.exists(os.path.join(src, "run.json")) else None
    if run is None:
        return None
    dst = os.path.join(out, "data", run_id)
    os.makedirs(os.path.join(dst, "links"), exist_ok=True)
    os.makedirs(os.path.join(dst, "img"), exist_ok=True)
    note = entry.get("note") or ""
    if entry.get("live") or run.get("live"):
        note = (note + " " if note else "") + f"Snapshot of a run in progress, taken {stamp}."
        run["live"] = False
    with open(os.path.join(dst, "run.json"), "w") as f:
        json.dump(sanitize(run), f, separators=(",", ":"))
    for path in sorted(glob.glob(os.path.join(src, "links", "*.json"))):
        with open(path) as f:
            link = json.load(f)
        with open(os.path.join(dst, "links", os.path.basename(path)), "w") as f:
            json.dump(sanitize(link), f, separators=(",", ":"))
    for path in sorted(glob.glob(os.path.join(src, "img", "*.png"))):
        shutil.copy2(path, os.path.join(dst, "img", os.path.basename(path)))
    return {"run_id": run_id, "label": entry.get("label"), "note": note or None, "live": False,
            "combination": entry["combination"], "built": True, "error": None, "title": run.get("title"),
            "inputs": run.get("inputs"), "links_built": len(run.get("links") or []), "built_utc": run.get("built_utc")}


def _size(path: str) -> int:
    return sum(os.path.getsize(os.path.join(d, f)) for d, _, fs in os.walk(path) for f in fs)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--catalog", required=True)
    ap.add_argument("--bundles", required=True)
    ap.add_argument("--web", required=True, help="the static web build (npm run build:static -> dist-static)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--runs", default=None, help="comma-separated run ids (default: every built run)")
    ap.add_argument("--commit", default="unknown commit")
    args = ap.parse_args()
    if os.path.exists(args.out) and os.listdir(args.out):
        raise SystemExit(f"{args.out} is not empty; export into a new folder")
    cat = json.load(open(args.catalog))
    wanted = set(args.runs.split(",")) if args.runs else None
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    shutil.copytree(args.web, args.out, dirs_exist_ok=True)
    listing = []
    for entry in cat["runs"]:
        if wanted and entry["run_id"] not in wanted:
            continue
        row = export_run(entry, args.bundles, args.out, stamp)
        if row:
            listing.append(row)
            print(f"  {row['run_id']}: {row['links_built']} links")
    with open(os.path.join(args.out, "data", "runs.json"), "w") as f:
        json.dump(listing, f, indent=1)
    runs_md = "\n".join(f"* **{r['title']}** ({r['run_id']}): {r['links_built']} links"
                        + (f". {r['note']}" if r["note"] else "") for r in listing)
    with open(os.path.join(args.out, "README.md"), "w") as f:
        f.write(README.format(runs=runs_md, exported=stamp, commit=args.commit))
    os.makedirs(os.path.join(args.out, ".github", "workflows"), exist_ok=True)
    with open(os.path.join(args.out, ".github", "workflows", "pages.yml"), "w") as f:
        f.write(PAGES_WORKFLOW)
    open(os.path.join(args.out, ".nojekyll"), "w").close()
    print(f"site: {args.out}, {len(listing)} runs, {_size(args.out) / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
