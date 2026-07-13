import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "path";
import fs from "fs";

import { normalizeHtmlLineEndings } from "./buildUtils";

const ROOT_DIR = __dirname;
const TEMP_BUILD_DIR = path.resolve(ROOT_DIR, ".vite-build");
const ROOT_ASSETS_DIR = path.resolve(ROOT_DIR, "assets");

// Clean stale build artifacts from assets/ before each build.
// Hashed filenames change every build, so orphaned chunks accumulate.
function cleanOldAssets(): void {
  if (!fs.existsSync(ROOT_ASSETS_DIR)) return;

  const HASHED_RE = /^[\w-]+-[A-Za-z0-9_-]{6,}\.(js|css|woff2?)$/;
  for (const file of fs.readdirSync(ROOT_ASSETS_DIR)) {
    if (HASHED_RE.test(file)) {
      fs.unlinkSync(path.join(ROOT_ASSETS_DIR, file));
    }
  }
}

function syncBuiltDashboard(): void {
  const builtIndexPath = path.join(TEMP_BUILD_DIR, "index.html");
  const builtAssetsDir = path.join(TEMP_BUILD_DIR, "assets");
  const targetIndexPath = path.join(ROOT_DIR, "index.html");

  if (!fs.existsSync(builtIndexPath)) {
    throw new Error(`Built dashboard index not found: ${builtIndexPath}`);
  }

  fs.mkdirSync(ROOT_ASSETS_DIR, { recursive: true });
  if (fs.existsSync(builtAssetsDir)) {
    for (const entry of fs.readdirSync(builtAssetsDir)) {
      fs.cpSync(
        path.join(builtAssetsDir, entry),
        path.join(ROOT_ASSETS_DIR, entry),
        { recursive: true }
      );
    }
  }

  const builtIndex = fs.readFileSync(builtIndexPath, "utf8");
  fs.writeFileSync(targetIndexPath, normalizeHtmlLineEndings(builtIndex), "utf8");
  fs.rmSync(TEMP_BUILD_DIR, { recursive: true, force: true });
}

// https://vite.dev/config/
export default defineConfig({
  plugins: [
    react(),
    {
      name: "clean-old-assets",
      apply: "build",
      buildStart() {
        cleanOldAssets();
      },
    },
    {
      name: "astrbot-compat",
      apply: "build",
      // AstrBot's regex-based JS rewriting fails on Vite's minified
      // `from"./chunk.js"` (no space after `from`), so imports land
      // as unrouted 404s.  IIFE + inlineDynamicImports eliminates all
      // import/export statements.  We also strip `type="module"` and
      // `crossorigin` so the browser loads the single bundle as a
      // classic script — no CORS checks, no module graph.
      transformIndexHtml(html) {
        return html
          .replace(/\s+type="module"/g, " defer")
          .replace(/\s+crossorigin(?:="[^"]*")?/g, "");
      },
    },
    {
      name: "preserve-index-source",
      apply: "build",
      buildStart() {
        const indexPath = path.resolve(ROOT_DIR, "index.html");
        const backupPath = path.resolve(ROOT_DIR, "index.src.bak");
        if (!fs.existsSync(indexPath)) return;
        const html = fs.readFileSync(indexPath, "utf-8");
        if (/\.\/assets\/index-[A-Za-z0-9_-]+\.js/.test(html)) {
          if (fs.existsSync(backupPath)) {
            fs.copyFileSync(backupPath, indexPath);
          }
        } else {
          fs.writeFileSync(backupPath, html, "utf-8");
        }
      },
    },
    {
      name: "sync-dashboard-build-output",
      apply: "build",
      closeBundle() {
        syncBuiltDashboard();
      },
    },
  ],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  build: {
    outDir: TEMP_BUILD_DIR,
    emptyOutDir: true,
    // IIFE single-file bundle eliminates ES module import/export
    // statements, which AstrBot's regex-based JS rewriting cannot
    // handle in minified output (no space after `from` keyword).
    // Without this, the browser receives unreplaced relative import
    // paths → 404 → black screen.
    target: "es2017",
    modulePreload: false,
    cssCodeSplit: false,
    chunkSizeWarningLimit: 3000,
    rollupOptions: {
      output: {
        format: "iife",
        inlineDynamicImports: true,
        entryFileNames: "assets/[name]-[hash].js",
        assetFileNames: "assets/[name]-[hash][extname]",
      },
    },
  },
  base: "./",
  server: {
    port: 5173,
    open: false,
    fs: {
      allow: [path.resolve(ROOT_DIR, "../..")],
    },
  },
});
