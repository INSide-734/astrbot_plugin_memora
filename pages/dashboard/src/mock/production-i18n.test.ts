import fs from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

import { LANG_MAPS } from "./index";

interface NestedDictionary {
  [key: string]: string | NestedDictionary;
}

const PRODUCTION_I18N_DIR = path.resolve(process.cwd(), "../../.astrbot-plugin/i18n");

function flattenDictionary(
  source: NestedDictionary,
  prefix = "",
): Record<string, string> {
  const flattened: Record<string, string> = {};
  for (const [key, value] of Object.entries(source)) {
    const path = prefix ? `${prefix}.${key}` : key;
    if (typeof value === "string") {
      flattened[path] = value;
    } else {
      Object.assign(flattened, flattenDictionary(value, path));
    }
  }
  return flattened;
}

describe("生产 Page i18n 契约", () => {
  const locales = [
    ["zh", "zh-CN"],
    ["en", "en-US"],
    ["ru", "ru-RU"],
  ] as const;

  for (const [dictionaryLocale, fileLocale] of locales) {
    it(`${fileLocale} 完整覆盖 Dashboard 字典`, () => {
      const production = JSON.parse(
        fs.readFileSync(`${PRODUCTION_I18N_DIR}/${fileLocale}.json`, "utf8"),
      ) as { dashboard?: NestedDictionary };

      expect(production.dashboard).toBeDefined();
      expect(flattenDictionary(production.dashboard ?? {})).toEqual(
        LANG_MAPS[dictionaryLocale],
      );
    });
  }
});
