"""Serve the run viewer: the built web app, the run bundles, and live rebuilds.

    python -m run_viewer.server.serve --catalog <runs.json> --bundles <dir> [--build] [--port 5173]

The catalog lists the runs (see runs.example.json). Runs marked "live" are rebuilt in a
background thread every --live-interval seconds; the rebuild is incremental, so only the
link that is being written is re-read. Binds to 127.0.0.1 only: view it through an SSH tunnel.
Source run folders are only read, never written.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import posixpath
import threading
import time
import traceback
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlparse

from run_viewer.extract.build import ImageEnv, RunBuilder
from run_viewer.extract.snapshot import snapshot_link

WEB_DIST = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web", "dist")


class Catalog:
    def __init__(self, path: str, bundles: str):
        with open(path) as f:
            cfg = json.load(f)
        self.env = ImageEnv(cfg.get("aviary_python"), cfg.get("tigl_python"), cfg.get("fixtures_dir"))
        self.runs = {r["run_id"]: r for r in cfg["runs"]}
        self.bundles = bundles
        self.errors: dict[str, str] = {}
        self.lock = threading.Lock()

    def builder(self, run_id: str) -> RunBuilder:
        r = self.runs[run_id]
        return RunBuilder(r["run_dir"], r["combination"], os.path.join(self.bundles, run_id),
                          r.get("snapshot"), self.env, run_id, live=bool(r.get("live")))

    def refresh_snapshot(self, run_id: str) -> None:
        """Live runs: copy new aviary / SU2 files before the framework's cleanup removes them."""
        r = self.runs[run_id]
        if not (r.get("snapshot") and r.get("aviary_dir")):
            return
        b = self.builder(run_id)
        for link_dir in b.link_dirs()[-2:]:  # the link being written, and the one just finished
            repeat = os.path.basename(os.path.dirname(link_dir))
            snapshot_link(link_dir, os.path.join(r["snapshot"], repeat, r["combination"]),
                          r["aviary_dir"], r.get("su2_root"))

    def build(self, run_id: str) -> None:
        try:
            with self.lock:
                if self.runs[run_id].get("live"):
                    self.refresh_snapshot(run_id)
                self.builder(run_id).build()
            self.errors.pop(run_id, None)
        except Exception:  # keep serving the last good bundle, and say why it is stale
            self.errors[run_id] = traceback.format_exc()[-2000:]
            print(f"[build] {run_id} failed:\n{self.errors[run_id]}", flush=True)

    def listing(self) -> list[dict]:
        out = []
        for run_id, r in self.runs.items():
            run_json = os.path.join(self.bundles, run_id, "run.json")
            built = None
            if os.path.exists(run_json):
                with open(run_json) as f:
                    built = json.load(f)
            out.append({"run_id": run_id, "label": r.get("label"), "note": r.get("note"),
                        "live": bool(r.get("live")), "combination": r["combination"],
                        "built": built is not None, "error": self.errors.get(run_id),
                        "inputs": (built or {}).get("inputs"), "title": (built or {}).get("title"),
                        "links_built": len((built or {}).get("links") or []),
                        "built_utc": (built or {}).get("built_utc")})
        return out


def live_loop(catalog: Catalog, interval: float, all_interval: float) -> None:
    """Rebuild live runs every `interval` s, and every run every `all_interval` s (incremental:
    picks up files added later, e.g. by replay.py)."""
    last_all = time.time()
    while True:
        everything = all_interval > 0 and time.time() - last_all >= all_interval
        for run_id, r in catalog.runs.items():
            if r.get("live") or everything:
                catalog.build(run_id)
        if everything:
            last_all = time.time()
        time.sleep(interval)


def make_handler(catalog: Catalog):
    class Handler(SimpleHTTPRequestHandler):
        def log_message(self, fmt, *args):  # quiet: one line per API error only
            pass

        def _json(self, obj, code=200):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _file(self, path: str, cache: bool = False):
            if not os.path.isfile(path):
                self._json({"error": "not found"}, 404)
                return
            with open(path, "rb") as f:
                body = f.read()
            self.send_response(200)
            self.send_header("Content-Type", mimetypes.guess_type(path)[0] or "application/octet-stream")
            self.send_header("Cache-Control", "max-age=3600" if cache else "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = posixpath.normpath(unquote(urlparse(self.path).path))
            parts = [p for p in path.split("/") if p]
            if parts[:2] == ["api", "runs"]:
                if len(parts) == 2:
                    return self._json(catalog.listing())
                run_id = parts[2]
                if run_id not in catalog.runs or ".." in parts:
                    return self._json({"error": "unknown run"}, 404)
                root = os.path.join(catalog.bundles, run_id)
                rest = parts[3:]
                if rest == ["run.json"] or (len(rest) == 2 and rest[0] == "links"):
                    return self._file(os.path.join(root, *rest))
                if len(rest) == 2 and rest[0] == "img":
                    return self._file(os.path.join(root, *rest), cache=not catalog.runs[run_id].get("live"))
                return self._json({"error": "not found"}, 404)
            # the web app (single page)
            target = os.path.join(WEB_DIST, *parts) if parts else os.path.join(WEB_DIST, "index.html")
            if not os.path.isfile(target):
                target = os.path.join(WEB_DIST, "index.html")
            return self._file(target, cache="assets" in parts)

    return Handler


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--catalog", required=True)
    ap.add_argument("--bundles", required=True)
    ap.add_argument("--port", type=int, default=5173)
    ap.add_argument("--build", action="store_true", help="(re)build every run before serving")
    ap.add_argument("--live-interval", type=float, default=15.0)
    ap.add_argument("--all-interval", type=float, default=600.0, help="rebuild every run this often (0 = never)")
    args = ap.parse_args()
    catalog = Catalog(args.catalog, args.bundles)
    if args.build:
        for run_id in catalog.runs:
            print(f"[build] {run_id}", flush=True)
            catalog.build(run_id)
    if args.all_interval > 0 or any(r.get("live") for r in catalog.runs.values()):
        threading.Thread(target=live_loop, args=(catalog, args.live_interval, args.all_interval), daemon=True).start()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(catalog))
    print(f"run viewer on http://127.0.0.1:{args.port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
