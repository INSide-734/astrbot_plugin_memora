export const ROUTE_LOADING_TEXT = ["加载中...", "Loading...", "Загрузка..."];

export const BROWSER_LAUNCH_CANDIDATES = [
  { channel: "chrome", label: "Google Chrome" },
  { channel: "msedge", label: "Microsoft Edge" },
  { channel: undefined, label: "Playwright Chromium" },
];

export function assertEditorViewport(
  { scrollViewport, lastField },
  { tolerance = 1 } = {},
) {
  const fieldIsVisible = (
    Number.isFinite(scrollViewport?.top)
    && Number.isFinite(scrollViewport?.bottom)
    && Number.isFinite(lastField?.top)
    && Number.isFinite(lastField?.bottom)
    && lastField.top >= scrollViewport.top - tolerance
    && lastField.bottom <= scrollViewport.bottom + tolerance
  );
  if (!fieldIsVisible) {
    throw new Error("The last form field is outside the scrollable viewport");
  }
}

export function assertNoHorizontalOverflow(
  measurements,
  { tolerance = 1 } = {},
) {
  const failures = measurements.flatMap(({ label, clientWidth, scrollWidth }) => {
    const overflow = scrollWidth - clientWidth;
    if (
      Number.isFinite(clientWidth)
      && clientWidth > 0
      && Number.isFinite(scrollWidth)
      && overflow <= tolerance
    ) {
      return [];
    }
    return [`${label} overflows by ${Number.isFinite(overflow) ? overflow : "unknown"}px`];
  });
  if (failures.length > 0) {
    throw new Error(`Horizontal overflow detected: ${failures.join("; ")}`);
  }
}

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

export function instrumentBrowserBridge(
  sourceBridge,
  { calls = [], postCalls = [], afterPost } = {},
) {
  if (
    !sourceBridge
    || typeof sourceBridge.apiGet !== "function"
    || typeof sourceBridge.apiPost !== "function"
  ) {
    throw new TypeError("Browser smoke requires apiGet and apiPost bridge methods");
  }

  const cloneJson = (value) => (
    value === undefined ? undefined : JSON.parse(JSON.stringify(value))
  );
  const errorMessage = (error) => (
    error instanceof Error ? error.message : String(error)
  );
  const raw = {
    apiGet: sourceBridge.apiGet.bind(sourceBridge),
    apiPost: sourceBridge.apiPost.bind(sourceBridge),
  };

  const recordedGet = async (endpoint, params = {}) => {
    const call = {
      method: "GET",
      endpoint: String(endpoint),
      params: cloneJson(params),
    };
    calls.push(call);
    try {
      const response = await raw.apiGet(endpoint, params);
      call.response = cloneJson(response);
      return response;
    } catch (error) {
      call.error = errorMessage(error);
      throw error;
    }
  };

  const recordedPost = async (endpoint, body = {}) => {
    const call = {
      method: "POST",
      endpoint: String(endpoint),
      body: cloneJson(body),
    };
    calls.push(call);
    postCalls.push(String(endpoint || "").replace(/^page\/?/, ""));
    try {
      const response = await raw.apiPost(endpoint, body);
      call.response = cloneJson(response);
      if (typeof afterPost === "function") {
        await afterPost({
          endpoint: String(endpoint),
          body: cloneJson(body),
          response: cloneJson(response),
        });
      }
      return response;
    } catch (error) {
      call.error = errorMessage(error);
      throw error;
    }
  };

  const bridge = new Proxy(sourceBridge, {
    get(target, property, receiver) {
      if (property === "apiGet") return recordedGet;
      if (property === "apiPost") return recordedPost;
      return Reflect.get(target, property, receiver);
    },
  });

  return { bridge, calls, postCalls, raw };
}

export function installBundledMockBridgeHarness(
  target,
  instrument = instrumentBrowserBridge,
) {
  const calls = [];
  const postCalls = [];
  let installedBridge;

  target.__memoraBridgeCalls = calls;
  target.__memoraPostCalls = postCalls;
  Object.defineProperty(target, "AstrBotPluginPage", {
    configurable: true,
    enumerable: true,
    get() {
      return installedBridge;
    },
    set(sourceBridge) {
      if (!sourceBridge) {
        installedBridge = sourceBridge;
        target.__memoraRawBridge = undefined;
        return;
      }
      const instrumentation = instrument(sourceBridge, { calls, postCalls });
      installedBridge = instrumentation.bridge;
      target.__memoraRawBridge = instrumentation.raw;
    },
  });

  return { calls, postCalls };
}
