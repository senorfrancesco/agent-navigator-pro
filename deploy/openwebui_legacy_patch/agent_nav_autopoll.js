(() => {
  const PATCH_MARKER = "agent-nav-openwebui-autopoll-v2";
  if (window[PATCH_MARKER]) {
    return;
  }
  window[PATCH_MARKER] = true;

  let reconcileScheduled = false;
  let reconcileAttempts = 0;
  let observedLog = null;
  let logObserver = null;
  const MAX_RECONCILE_ATTEMPTS = 60;
  const RECONCILE_DELAY_MS = 250;
  const PERIODIC_RECONCILE_MS = 2000;

  const currentChatId = () => {
    const match = window.location.pathname.match(/^\/c\/([^/?#]+)/);
    return match ? decodeURIComponent(match[1]) : null;
  };

  const authHeaders = () => {
    const headers = {
      Accept: "application/json",
    };
    const token = window.localStorage.getItem("token");
    if (token) {
      headers.Authorization = `Bearer ${token}`;
    }
    return headers;
  };

  const hideTerminalButtons = () => {
    for (const button of document.querySelectorAll("button")) {
      const label = (button.textContent || "").trim();
      if (label === "Обновить deep-job" || label === "Отменить deep-job") {
        button.style.display = "none";
      }
    }
  };

  const upsertResultBubble = (log, resultId, content) => {
    if (!log || !content) {
      return;
    }

    let bubble = log.querySelector(`[data-agent-nav-result-id="${resultId}"]`);
    if (!bubble) {
      bubble = document.createElement("div");
      bubble.setAttribute("data-agent-nav-result-id", resultId);
      bubble.style.margin = "12px 0 0";
      bubble.style.padding = "14px 16px";
      bubble.style.border = "1px solid rgba(255,255,255,0.12)";
      bubble.style.borderRadius = "8px";
      bubble.style.background = "rgba(255,255,255,0.04)";
      bubble.style.whiteSpace = "pre-wrap";
      bubble.style.lineHeight = "1.5";
      bubble.style.fontSize = "14px";
      log.appendChild(bubble);
    }

    bubble.textContent = content;
  };

  const bindLogObserver = (log) => {
    if (!log) {
      return;
    }
    if (observedLog === log) {
      return;
    }
    observedLog = log;
    if (logObserver) {
      logObserver.disconnect();
    }
    logObserver = new MutationObserver(() => {
      scheduleReconcile({ resetAttempts: false });
    });
    logObserver.observe(log, { childList: true, subtree: true });
  };

  const collectTerminalDeepJobs = (messages) =>
    Object.values(messages)
      .filter(
        (message) =>
          message &&
          message.role === "assistant" &&
          typeof message.job_status === "string" &&
          ["completed", "failed", "cancelled"].includes(message.job_status) &&
          message.result_message_id
      )
      .map((message) => {
        const resultId = String(message.result_message_id || "").trim();
        const resultMessage = resultId ? messages[resultId] : null;
        const resultContent = String(resultMessage?.content || "").trim();
        return {
          resultId,
          resultContent,
        };
      })
      .filter((entry) => entry.resultId && entry.resultContent);

  const reconcileTerminalDeepJobs = async () => {
    const chatId = currentChatId();
    if (!chatId) {
      return false;
    }

    const response = await fetch(`/api/v1/chats/${encodeURIComponent(chatId)}`, {
      credentials: "same-origin",
      headers: authHeaders(),
    });
    if (!response.ok) {
      return false;
    }

    const payload = await response.json();
    const messages = payload?.chat?.history?.messages || {};
    const log = document.querySelector('[role="log"]');
    if (!log) {
      return false;
    }
    bindLogObserver(log);

    const terminalEntries = collectTerminalDeepJobs(messages);
    if (!terminalEntries.length) {
      return false;
    }

    hideTerminalButtons();
    for (const entry of terminalEntries) {
      upsertResultBubble(log, entry.resultId, entry.resultContent);
    }
    return true;
  };

  const scheduleReconcile = ({ resetAttempts = true } = {}) => {
    if (reconcileScheduled) {
      return;
    }
    if (resetAttempts) {
      reconcileAttempts = 0;
    }
    reconcileScheduled = true;
    window.setTimeout(() => {
      reconcileTerminalDeepJobs().finally(() => {
        reconcileScheduled = false;
      }).then((reconciled) => {
        if (!reconciled && reconcileAttempts < MAX_RECONCILE_ATTEMPTS) {
          reconcileAttempts += 1;
          scheduleReconcile({ resetAttempts: false });
          return;
        }
        reconcileAttempts = 0;
      });
    }, RECONCILE_DELAY_MS);
  };

  const bindSocket = () => {
    if (typeof window.io !== "function") {
      return;
    }

    const token = window.localStorage.getItem("token");
    if (!token) {
      return;
    }

    const socket = window.io("/", {
      auth: {
        token,
      },
    });

    socket.on("events", (payload) => {
      const chatId = currentChatId();
      if (!chatId || !payload || payload.chat_id !== chatId) {
        return;
      }

      const eventData = payload.data || {};
      const data = eventData.data || {};

      if (eventData.type === "chat:message:new" && data.reload === true) {
        scheduleReconcile();
        return;
      }

      if (eventData.type === "chat:message:meta" && data.reload === true) {
        scheduleReconcile();
      }
    });
  };

  if (document.readyState === "loading") {
    document.addEventListener(
      "DOMContentLoaded",
      () => {
        bindSocket();
        scheduleReconcile();
      },
      { once: true }
    );
  } else {
    bindSocket();
    scheduleReconcile();
  }

  window.addEventListener("load", () => {
    scheduleReconcile();
  });

  window.setInterval(() => {
    scheduleReconcile({ resetAttempts: false });
  }, PERIODIC_RECONCILE_MS);
})();
