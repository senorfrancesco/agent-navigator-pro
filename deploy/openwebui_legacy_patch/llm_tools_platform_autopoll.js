(() => {
  const PATCH_MARKER = "llm-tools-platform-openwebui-autopoll-v2";
  const RUNTIME_MARKER = "__llm_tools_platform_openwebui_autopoll_v2_loaded__";
  const DEBUG_MARKER = "__llm_tools_platform_openwebui_autopoll_v2_debug__";
  if (window[RUNTIME_MARKER]) {
    return;
  }
  window[RUNTIME_MARKER] = true;

  const ACTIVE_STATUSES = new Set(["accepted", "queued", "running", "cancelling"]);
  const TERMINAL_STATUSES = new Set(["completed", "failed", "cancelled"]);
  const ACTION_PATHS = new Set([
    "/api/chat/actions/equipment_deep_action",
    "/api/chat/actions/tool_job_refresh_action",
    "/api/chat/actions/tool_job_cancel_action",
  ]);
  const CHAT_PERSIST_PATH_RE = /^\/api\/v1\/chats\/([^/]+)$/;
  const RECONCILE_DELAY_MS = 250;
  const PERIODIC_RECONCILE_MS = 2000;
  const STARTUP_RECONCILE_DELAYS_MS = [0, 500, 1500, 3000, 6000];

  let reconcileInFlight = false;
  let reconcileTimer = null;
  let pollingEnabled = true;
  let lastChatId = null;
  let observedLog = null;
  const terminalSnapshotByChat = new Map();
  const debugState = {
    reconcileRuns: 0,
    reconcileSuccesses: 0,
    startupBursts: 0,
    lastChatId: null,
    lastError: null,
  };
  window[DEBUG_MARKER] = debugState;

  const currentChatId = () => {
    const match = window.location.pathname.match(/^\/c\/([^/?#]+)/);
    return match ? decodeURIComponent(match[1]) : null;
  };

  const authHeaders = () => {
    const headers = { Accept: "application/json" };
    const token = window.localStorage.getItem("token");
    if (token) {
      headers.Authorization = `Bearer ${token}`;
    }
    return headers;
  };

  const isTerminalStatus = (status) => TERMINAL_STATUSES.has(String(status || "").trim());
  const isActiveStatus = (status) => ACTIVE_STATUSES.has(String(status || "").trim());

  const actionPathFromArgs = (input) => {
    try {
      const rawUrl = typeof input === "string" ? input : input?.url || "";
      if (!rawUrl) {
        return "";
      }
      return new URL(rawUrl, window.location.origin).pathname;
    } catch {
      return "";
    }
  };

  const chatIdFromPersistPath = (pathname) => {
    const match = String(pathname || "").match(CHAT_PERSIST_PATH_RE);
    return match ? decodeURIComponent(match[1]) : null;
  };

  const requestMethodFromArgs = (input, init) => {
    if (init?.method) {
      return String(init.method).toUpperCase();
    }
    if (typeof Request !== "undefined" && input instanceof Request && input.method) {
      return String(input.method).toUpperCase();
    }
    return "GET";
  };

  const safeJsonParse = (raw) => {
    try {
      return JSON.parse(raw);
    } catch {
      return null;
    }
  };

  const safeResponseJson = async (response) => {
    try {
      return await response.clone().json();
    } catch {
      return null;
    }
  };

  const deepJobMessages = (messages) =>
    Object.entries(messages || {}).filter(([, message]) => {
      if (!message || message.role !== "assistant") {
        return false;
      }
      return Boolean(message.tool_job || message.result_message_id || message.job_status);
    });

  const findMessageListItem = (messageId) => {
    const node = document.getElementById(`message-${messageId}`);
    return node ? node.closest('[role="listitem"]') : null;
  };

  const bindLogObserver = (log) => {
    if (!log || observedLog === log) {
      return;
    }
    observedLog = log;
  };

  const buttonLabel = (button) =>
    String(
      button?.getAttribute?.("aria-label")
        || button?.getAttribute?.("title")
        || button?.textContent
        || button?.innerText
        || ""
    ).trim();

  const findActionButtons = (container) => {
    if (!container) {
      return [];
    }
    return Array.from(container.querySelectorAll("button")).filter((button) => {
      const label = buttonLabel(button);
      return label === "Обновить deep-job" || label === "Отменить deep-job";
    });
  };

  const syncTerminalButtons = (container, message) => {
    const shouldHide = Boolean(message?.actions_disabled) || isTerminalStatus(message?.job_status);
    for (const button of findActionButtons(container)) {
      button.style.display = shouldHide ? "none" : "";
    }
  };

  const syncGlobalActionButtons = (shouldHide) => {
    for (const button of findActionButtons(document)) {
      button.style.display = shouldHide ? "none" : "";
    }
  };

  const syncStatusSummary = (container, message) => {
    if (!container || !message?.job_status) {
      return;
    }
    const summary = container.querySelector(".status-description .text-base");
    if (summary) {
      const statusText = String(message?.status_text || "").trim();
      const statusHistory = Array.isArray(message?.statusHistory)
        ? message.statusHistory
        : Array.isArray(message?.status_history)
          ? message.status_history
          : [];
      const latestStatus = statusHistory.length ? statusHistory[statusHistory.length - 1] : null;
      const historyText = String(
        latestStatus?.description || latestStatus?.content || latestStatus?.title || ""
      ).trim();
      summary.textContent = statusText || historyText || `Текущий статус deep-job: ${message.job_status}`;
    }
  };

  const buildResultBubble = (parentListItem, resultId) => {
    const wrapper = document.createElement("div");
    wrapper.setAttribute("role", "listitem");
    wrapper.setAttribute("data-llm-tools-platform-result-id", resultId);
    wrapper.className = parentListItem?.className || "flex flex-col justify-between px-5 mb-3 w-full max-w-5xl mx-auto rounded-lg group";

    const row = document.createElement("div");
    row.className = "flex w-full";

    const gutter = document.createElement("div");
    gutter.className = "shrink-0 ltr:mr-3 rtl:ml-3 hidden @lg:flex mt-1";
    gutter.style.width = "32px";

    const body = document.createElement("div");
    body.className = "flex-auto w-0 pl-1 relative";

    const surface = document.createElement("div");
    surface.className = "chat-assistant w-full min-w-full markdown-prose";

    const result = document.createElement("div");
    result.setAttribute("data-llm-tools-platform-result-content", "true");
    result.style.whiteSpace = "pre-wrap";
    result.style.lineHeight = "1.5";
    result.style.fontSize = "14px";
    result.style.padding = "14px 16px";
    result.style.border = "1px solid rgba(255,255,255,0.12)";
    result.style.borderRadius = "8px";
    result.style.background = "rgba(255,255,255,0.04)";

    surface.appendChild(result);
    body.appendChild(surface);
    row.appendChild(gutter);
    row.appendChild(body);
    wrapper.appendChild(row);
    return wrapper;
  };

  const ensureResultBubble = (parentId, parentListItem, resultId, resultContent) => {
    if (!resultId || !resultContent) {
      return false;
    }

    const nativeMessage = document.getElementById(`message-${resultId}`);
    const existing = document.querySelector(`[data-llm-tools-platform-result-id="${resultId}"]`);
    if (nativeMessage) {
      if (existing) {
        existing.remove();
      }
      return true;
    }

    if (!parentListItem) {
      return false;
    }

    const bubble = existing || buildResultBubble(parentListItem, resultId);
    bubble.setAttribute("data-llm-tools-platform-parent-id", parentId);
    const contentNode = bubble.querySelector('[data-llm-tools-platform-result-content="true"]');
    if (contentNode) {
      contentNode.textContent = resultContent;
    }

    if (bubble.parentElement !== parentListItem.parentElement || bubble.previousElementSibling !== parentListItem) {
      parentListItem.insertAdjacentElement("afterend", bubble);
    }
    return true;
  };

  const cleanupStaleResultBubbles = (visibleResultIds) => {
    for (const bubble of document.querySelectorAll("[data-llm-tools-platform-result-id]")) {
      const resultId = bubble.getAttribute("data-llm-tools-platform-result-id") || "";
      if (!visibleResultIds.has(resultId)) {
        bubble.remove();
      }
    }
  };

  const fetchChatPayload = async (chatId, fetchImpl = window.fetch.bind(window)) => {
    const response = await fetchImpl(`/api/v1/chats/${encodeURIComponent(chatId)}`, {
      credentials: "same-origin",
      headers: authHeaders(),
    });
    if (!response.ok) {
      return null;
    }
    return response.json();
  };

  const upsertMessageArray = (messages, authoritativeMessage) => {
    if (!Array.isArray(messages) || !authoritativeMessage?.id) {
      return;
    }
    const targetId = String(authoritativeMessage.id);
    const index = messages.findIndex((item) => String(item?.id || "") === targetId);
    if (index >= 0) {
      messages[index] = {
        ...(messages[index] || {}),
        ...authoritativeMessage,
      };
      return;
    }
    messages.push(authoritativeMessage);
  };

  const mergePersistedChatPayload = (body, authoritativeChat) => {
    if (!body?.chat || !authoritativeChat) {
      return body;
    }

    const history = body.chat.history;
    const authoritativeHistory = authoritativeChat.history;
    if (!history || typeof history !== "object" || !authoritativeHistory || typeof authoritativeHistory !== "object") {
      return body;
    }

    const messages = history.messages;
    const authoritativeMessages = authoritativeHistory.messages;
    if (!messages || typeof messages !== "object" || !authoritativeMessages || typeof authoritativeMessages !== "object") {
      return body;
    }

    const mergedBody = structuredClone(body);
    const mergedHistory = mergedBody.chat.history || {};
    const mergedMessages = mergedHistory.messages || {};
    const idsToMerge = new Set();

    for (const [messageId, message] of Object.entries(authoritativeMessages)) {
      if (!message || typeof message !== "object") {
        continue;
      }
      const resultId = String(message.result_message_id || "").trim();
      if (resultId || isTerminalStatus(message.job_status) || message.actions_disabled) {
        idsToMerge.add(messageId);
        if (resultId && authoritativeMessages[resultId]) {
          idsToMerge.add(resultId);
        }
      }
      if (String(message.tool_job_result_for || "").trim()) {
        idsToMerge.add(messageId);
      }
    }

    if (!idsToMerge.size) {
      return body;
    }

    for (const messageId of idsToMerge) {
      const authoritativeMessage = authoritativeMessages[messageId];
      if (!authoritativeMessage || typeof authoritativeMessage !== "object") {
        continue;
      }
      mergedMessages[messageId] = {
        ...(mergedMessages[messageId] || {}),
        ...authoritativeMessage,
      };
      upsertMessageArray(mergedBody.chat.messages, authoritativeMessage);
    }

    if (authoritativeHistory.currentId && !mergedHistory.currentId) {
      mergedHistory.currentId = authoritativeHistory.currentId;
    }

    mergedBody.chat.history = mergedHistory;
    return mergedBody;
  };

  const reconcileChat = async () => {
    const chatId = currentChatId();
    if (!chatId) {
      cleanupStaleResultBubbles(new Set());
      pollingEnabled = false;
      return false;
    }

    const payload = await fetchChatPayload(chatId);
    if (!payload) {
      pollingEnabled = false;
      return false;
    }

    const messages = payload?.chat?.history?.messages || {};
    bindLogObserver(document.querySelector('[role="log"]'));
    const parents = deepJobMessages(messages);
    const visibleResultIds = new Set();
    let shouldPoll = false;
    let reconciled = false;
    let hasActiveDeepJob = false;

    for (const [messageId, message] of parents) {
      const parentListItem = findMessageListItem(messageId);
      if (parentListItem) {
        syncTerminalButtons(parentListItem, message);
        syncStatusSummary(parentListItem, message);
      }

      if (isActiveStatus(message?.job_status)) {
        shouldPoll = true;
        hasActiveDeepJob = true;
      }

      const resultId = String(message?.result_message_id || "").trim();
      if (!resultId) {
        continue;
      }

      const resultMessage = messages[resultId];
      const resultContent = String(resultMessage?.content || "").trim();
      if (!resultContent) {
        shouldPoll = true;
        continue;
      }

      visibleResultIds.add(resultId);
      const resultVisible = ensureResultBubble(messageId, parentListItem, resultId, resultContent);
      if (!resultVisible) {
        shouldPoll = true;
        continue;
      }
      reconciled = true;
    }

    cleanupStaleResultBubbles(visibleResultIds);
    syncGlobalActionButtons(!hasActiveDeepJob);
    pollingEnabled = shouldPoll;
    return reconciled;
  };

  const scheduleReconcile = ({ resetAttempts = true } = {}) => {
    if (resetAttempts && reconcileTimer !== null) {
      window.clearTimeout(reconcileTimer);
      reconcileTimer = null;
    }
    if (reconcileTimer !== null) {
      return;
    }
    reconcileTimer = window.setTimeout(() => {
      reconcileTimer = null;
      void runReconcile();
    }, RECONCILE_DELAY_MS);
  };

  const runReconcile = async () => {
    if (reconcileInFlight) {
      return;
    }
    reconcileInFlight = true;
    debugState.reconcileRuns += 1;
    debugState.lastChatId = currentChatId();
    try {
      const reconciled = await reconcileChat();
      if (reconciled) {
        debugState.reconcileSuccesses += 1;
      }
      debugState.lastError = null;
    } catch (error) {
      debugState.lastError = String(error?.stack || error || "unknown-error");
      throw error;
    } finally {
      reconcileInFlight = false;
    }
  };

  const primeReconcile = (delays = STARTUP_RECONCILE_DELAYS_MS) => {
    debugState.startupBursts += 1;
    for (const delay of delays) {
      window.setTimeout(() => {
        handleChatChange();
        void runReconcile();
      }, delay);
    }
  };

  const wrapFetch = () => {
    if (window[`${PATCH_MARKER}-fetch`]) {
      return;
    }
    window[`${RUNTIME_MARKER}-fetch`] = true;

    const originalFetch = window.fetch.bind(window);
    window.fetch = async (input, init) => {
      const pathname = actionPathFromArgs(input);
      const method = requestMethodFromArgs(input, init);
      let nextInput = input;
      let nextInit = init;

      if (method === "POST") {
        const persistChatId = chatIdFromPersistPath(pathname);
        if (persistChatId && terminalSnapshotByChat.has(persistChatId)) {
          const rawBody =
            typeof init?.body === "string"
              ? init.body
              : typeof Request !== "undefined" && input instanceof Request
                ? null
                : null;
          const parsedBody = rawBody ? safeJsonParse(rawBody) : null;
          const authoritativeChat = terminalSnapshotByChat.get(persistChatId) || null;
          const mergedBody = parsedBody ? mergePersistedChatPayload(parsedBody, authoritativeChat) : null;
          if (mergedBody) {
            nextInit = {
              ...(init || {}),
              body: JSON.stringify(mergedBody),
            };
          }
        }
      }

      const response = await originalFetch(nextInput, nextInit);
      if (ACTION_PATHS.has(pathname)) {
        pollingEnabled = true;
        primeReconcile([0, 500, 1500, 3000]);
        const actionPayload = await safeResponseJson(response);
        if (actionPayload?.result_message_id || isTerminalStatus(actionPayload?.job_status)) {
          const activeChatId = currentChatId();
          if (activeChatId) {
            const latestChatPayload = await fetchChatPayload(activeChatId, originalFetch);
            const latestChat = latestChatPayload?.chat;
            if (latestChat?.history?.messages) {
              terminalSnapshotByChat.set(activeChatId, latestChat);
            }
          }
        }
      }
      return response;
    };
  };

  const handleChatChange = () => {
    const chatId = currentChatId();
    if (chatId === lastChatId) {
      return;
    }
    lastChatId = chatId;
    pollingEnabled = true;
    cleanupStaleResultBubbles(new Set());
    primeReconcile([0, 500, 1500, 3000]);
  };

  wrapFetch();

  if (document.readyState === "loading") {
    document.addEventListener(
      "DOMContentLoaded",
      () => {
        lastChatId = currentChatId();
        primeReconcile();
      },
      { once: true }
    );
  } else {
    lastChatId = currentChatId();
    primeReconcile();
  }

  window.addEventListener("load", () => {
    handleChatChange();
  });
  window.addEventListener("popstate", handleChatChange);
  window.addEventListener("hashchange", handleChatChange);

  window.setInterval(() => {
    handleChatChange();
    void runReconcile();
  }, PERIODIC_RECONCILE_MS);
})();
