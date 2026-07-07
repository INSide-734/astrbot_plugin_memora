export const ROUTE_LOADING_TEXT = ["加载中...", "Loading...", "Загрузка..."];

export function isRouteTextSettled(text, expected, loadingText = ROUTE_LOADING_TEXT) {
  const value = String(text ?? "");
  const expectedItems = Array.isArray(expected) ? expected : [expected];
  return (
    expectedItems.every((item) => value.includes(item))
    && loadingText.every((item) => !value.includes(item))
  );
}
