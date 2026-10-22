import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// `npm run dev` proxies the API to the Python server (serve.py --port 5174).
// `npm run build:static` makes the self-contained site: relative base so it works under any
// sub-path (GitHub Pages project sites, Hugging Face static Spaces, a folder on a website).
export default defineConfig(() => ({
  plugins: [react()],
  base: process.env.VITE_STATIC === "1" ? "./" : "/",
  server: { port: 5173, proxy: { "/api": "http://127.0.0.1:5174" } },
}));
