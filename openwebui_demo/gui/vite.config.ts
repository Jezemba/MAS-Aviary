import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// `base` is "/" for local dev. For GitHub Pages (a project site served at
// https://<user>.github.io/<repo>/) the deploy workflow sets VITE_BASE=/<repo>/.
// https://vite.dev/config/
export default defineConfig({
  base: process.env.VITE_BASE ?? "/",
  plugins: [react()],
});
