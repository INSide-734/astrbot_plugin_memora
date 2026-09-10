export type Translate = (key: string, ...args: string[]) => string;

export function translateEnum(
  t: Translate,
  prefix: string,
  value: unknown,
  fallback = String(value ?? ""),
): string {
  const rawValue = String(value ?? "").trim();
  if (!rawValue) return fallback;

  const normalizedValue = rawValue.toLowerCase().replace(/[\s-]+/g, "_");
  const key = `${prefix}.${normalizedValue}`;
  const translated = t(key);
  return translated === key ? fallback : translated;
}

export function dashboardLocale(language: string): string {
  const normalizedLanguage = language.slice(0, 2).toLowerCase();
  if (normalizedLanguage === "en") return "en-US";
  if (normalizedLanguage === "ru") return "ru-RU";
  return "zh-CN";
}

function dashboardNumber(value: unknown): number | null {
  if (value === null || value === undefined || value === "") return null;
  const numericValue = typeof value === "number" ? value : Number(value);
  return Number.isFinite(numericValue) ? numericValue : null;
}

export function formatDashboardNumber(
  value: unknown,
  locale: string,
  options: Intl.NumberFormatOptions = {},
  fallback = "--",
): string {
  const numericValue = dashboardNumber(value);
  return numericValue === null
    ? fallback
    : new Intl.NumberFormat(locale, options).format(numericValue);
}

export function formatDashboardPercent(
  value: unknown,
  locale: string,
  options: Intl.NumberFormatOptions = {},
  fallback = "--",
): string {
  return formatDashboardNumber(value, locale, { ...options, style: "percent" }, fallback);
}

function parseDashboardDate(value: unknown): Date | null {
  if (value instanceof Date) {
    return Number.isNaN(value.getTime()) ? null : value;
  }

  const rawValue = String(value ?? "").trim();
  if (!rawValue) return null;

  const numericValue = Number(rawValue);
  const timestamp = Number.isFinite(numericValue)
    ? (Math.abs(numericValue) < 100_000_000_000 ? numericValue * 1000 : numericValue)
    : rawValue;
  const date = new Date(timestamp);
  return Number.isNaN(date.getTime()) ? null : date;
}

export function formatDashboardDate(value: unknown, locale: string): string {
  const rawValue = String(value ?? "");
  const date = parseDashboardDate(value);
  if (!date) return rawValue;
  if (/^\d{4}-\d{2}-\d{2}$/.test(rawValue.trim())) {
    return date.toLocaleDateString(locale, { timeZone: "UTC" });
  }
  return date.toLocaleDateString(locale);
}

export function formatDashboardShortDate(value: unknown, locale: string): string {
  const rawValue = String(value ?? "");
  const date = parseDashboardDate(value);
  if (!date) return rawValue;

  const options: Intl.DateTimeFormatOptions = { month: "numeric", day: "numeric" };
  if (/^\d{4}-\d{2}-\d{2}$/.test(rawValue.trim())) options.timeZone = "UTC";
  return date.toLocaleDateString(locale, options);
}

export function formatDashboardDateTime(value: unknown, locale: string): string {
  const rawValue = String(value ?? "");
  return parseDashboardDate(value)?.toLocaleString(locale) ?? rawValue;
}
