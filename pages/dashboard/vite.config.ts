import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react";
import path from "path";
import fs from "fs";

import { normalizeHtmlLineEndings } from "./buildUtils";

const ROOT_DIR = __dirname;
const TEMP_BUILD_DIR = path.resolve(ROOT_DIR, ".vite-build");
const ROOT_ASSETS_DIR = path.resolve(ROOT_DIR, "assets");
const CONFIG_SCHEMA_PATH = path.resolve(ROOT_DIR, "../../_conf_schema.json");
const CONFIG_SCHEMA_VIRTUAL_ID = "virtual:memora-config-schema";
const RESOLVED_CONFIG_SCHEMA_VIRTUAL_ID = `\0${CONFIG_SCHEMA_VIRTUAL_ID}`;

/** 创建仅暴露根配置 schema 的 Vite 虚拟模块插件。 */
export function memoraConfigSchemaPlugin(): Plugin {
  return {
    name: "memora-config-schema",
    /** 将精确匹配的公开模块 ID 映射为内部虚拟模块 ID。 */
    resolveId(id) {
      return id === CONFIG_SCHEMA_VIRTUAL_ID
        ? RESOLVED_CONFIG_SCHEMA_VIRTUAL_ID
        : null;
    },
    /** 读取并校验根配置 schema，再返回可导入的模块源码。 */
    load(id) {
      if (id !== RESOLVED_CONFIG_SCHEMA_VIRTUAL_ID) return null;
      const schemaSource = fs.readFileSync(CONFIG_SCHEMA_PATH, "utf-8");
      JSON.parse(schemaSource);
      return `export default ${JSON.stringify(schemaSource)};`;
    },
  };
}

/** 在构建前清理 assets 目录内带哈希的旧构建产物。 */
function cleanOldAssets(): void {
  if (!fs.existsSync(ROOT_ASSETS_DIR)) return;

  const HASHED_RE = /^[\w-]+-[A-Za-z0-9_-]{6,}\.(js|css|woff2?)$/;
  for (const file of fs.readdirSync(ROOT_ASSETS_DIR)) {
    if (HASHED_RE.test(file)) {
      fs.unlinkSync(path.join(ROOT_ASSETS_DIR, file));
    }
  }
}

/** 将临时目录中的 Dashboard 产物同步到插件页面目录。 */
function syncBuiltDashboard(): void {
  const builtIndexPath = path.join(TEMP_BUILD_DIR, "index.html");
  const builtAssetsDir = path.join(TEMP_BUILD_DIR, "assets");
  const targetIndexPath = path.join(ROOT_DIR, "index.html");

  if (!fs.existsSync(builtIndexPath)) {
    throw new Error(`未找到 Dashboard 构建入口：${builtIndexPath}`);
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
    memoraConfigSchemaPlugin(),
    {
      name: "clean-old-assets",
      apply: "build",
      /** 在 Rollup 开始构建时移除旧哈希产物。 */
      buildStart() {
        cleanOldAssets();
      },
    },
    {
      name: "astrbot-compat",
      apply: "build",
      // AstrBot 基于正则的 JS 重写无法识别 Vite 压缩后的
      // `from"./chunk.js"`（`from` 后没有空格），导致导入请求返回 404。
      // IIFE 与 inlineDynamicImports 会消除 import/export 语句；同时移除
      // `type="module"` 和 `crossorigin`，让浏览器以 classic script 加载单包，
      // 避免触发 CORS 检查和模块图加载。
      /** 将入口中的模块脚本属性改写为 AstrBot 可加载的 classic script。 */
      transformIndexHtml(html) {
        return html
          .replace(/\s+type="module"/g, " defer")
          .replace(/\s+crossorigin(?:="[^"]*")?/g, "");
      },
    },
    {
      name: "preserve-index-source",
      apply: "build",
      /** 保存开发入口，避免连续构建把上次生成入口当成源文件。 */
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
      /** 构建完成后同步产物，并清理临时构建目录。 */
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
    // IIFE 单文件包会消除 ES 模块 import/export 语句，避免 AstrBot 的
    // 正则重写遗漏压缩后 `from` 关键字后的无空格相对路径；否则浏览器会
    // 请求未重写的相对路径并得到 404，最终出现黑屏。
    target: "es2017",
    modulePreload: false,
    cssCodeSplit: false,
    // AstrBot Page 要求单个 classic-script 包；当前约 3.25 MB，超过 3.5 MB 时继续告警。
    chunkSizeWarningLimit: 3500,
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
  },
});
