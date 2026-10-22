// Where the data comes from: the Python server (/api/...) or, in the static build
// (VITE_STATIC=1, see export_static.py), files next to index.html under data/.
// Static paths are relative so the site works under any sub-path (GitHub Pages, HF Spaces).

export const STATIC = import.meta.env.VITE_STATIC === "1";

const pad = (n: number) => String(n).padStart(3, "0");

export const urls = {
  catalog: () => (STATIC ? "data/runs.json" : "/api/runs"),
  run: (id: string) => (STATIC ? `data/${id}/run.json` : `/api/runs/${id}/run.json`),
  link: (id: string, n: number) => (STATIC ? `data/${id}/links/${pad(n)}.json` : `/api/runs/${id}/links/${pad(n)}.json`),
  img: (id: string, file: string, v: unknown) =>
    STATIC ? `data/${id}/img/${file}?v=${v ?? 1}` : `/api/runs/${id}/img/${file}?v=${v ?? 1}`,
};
