export const ROUTE_LOADING_TEXT = ["加载中...", "Loading...", "Загрузка..."];

export const BROWSER_LAUNCH_CANDIDATES = [
  { channel: "chrome", label: "Google Chrome" },
  { channel: "msedge", label: "Microsoft Edge" },
  { channel: undefined, label: "Playwright Chromium" },
];

export function createBrowserLaunchOptions(
  channel,
  {
    platform = process.platform,
    ci = ["1", "true"].includes(String(process.env.CI ?? "").toLowerCase()),
  } = {},
) {
  const browser = channel ? { channel } : {};
  if (ci || platform !== "win32") {
    return { ...browser, headless: true };
  }
  return {
    ...browser,
    headless: false,
    slowMo: 50,
  };
}

export function isRouteTextSettled(text, expected, loadingText = ROUTE_LOADING_TEXT) {
  const value = String(text ?? "");
  const expectedItems = Array.isArray(expected) ? expected : [expected];
  return (
    expectedItems.every((item) => value.includes(item))
    && loadingText.every((item) => !value.includes(item))
  );
}
