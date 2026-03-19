(() => {
  const HOOK_ATTR = "data-agent-nav-hook";
  const CURRENT_THREAD_ATTR = "data-agent-nav-current-thread";

  const setHook = (element, hookName) => {
    if (!element || !(element instanceof HTMLElement)) return;
    if (!element.getAttribute(HOOK_ATTR)) {
      element.setAttribute(HOOK_ATTR, hookName);
    }
  };

  const queryFirst = (selectors) => {
    for (const selector of selectors) {
      const node = document.querySelector(selector);
      if (node) return node;
    }
    return null;
  };

  const annotateLogin = () => {
    const passwordInput = queryFirst([
      'input[type="password"]',
      'input[name="password"]',
      'input[autocomplete="current-password"]',
    ]);
    const loginForm = passwordInput?.closest("form") || queryFirst(["form"]);
    setHook(loginForm, "login-form");
    const submit = loginForm?.querySelector('button[type="submit"], button') || null;
    setHook(submit, "login-submit");
  };

  const annotateMainChat = () => {
    const chatInput = queryFirst([
      'textarea',
      '[contenteditable="true"]',
      'input[type="text"]',
    ]);
    setHook(chatInput, "main-chat-input");

    const fileInput = queryFirst(['input[type="file"]']);
    const uploadTrigger =
      fileInput?.closest("label, button") ||
      queryFirst(['button[aria-label*="upload" i]', 'button[title*="upload" i]', 'label[for]']);
    setHook(uploadTrigger || fileInput, "upload-trigger");
  };

  const annotateMessages = () => {
    const containers = document.querySelectorAll('div, article, li');
    containers.forEach((node) => {
      if (!(node instanceof HTMLElement)) return;
      const text = (node.textContent || "").trim();
      if (!text) return;
      const hasAssistantHint =
        /assistant|ассистент/i.test(node.getAttribute("data-author") || "") ||
        /assistant/i.test(node.className || "");
      const hasMessageShape =
        node.querySelector("pre, code, p, a") ||
        node.getAttribute("role") === "article" ||
        node.getAttribute("data-message-author") === "assistant";
      if (hasAssistantHint || hasMessageShape) {
        setHook(node, "assistant-message-container");
      }
    });
  };

  const annotateThreads = () => {
    const threadList = queryFirst([
      '[data-testid*="thread" i]',
      'nav',
      '[role="navigation"]',
      'aside',
    ]);
    setHook(threadList, "thread-list");

    const currentThread = queryFirst([
      '[aria-current="page"]',
      '[data-state="active"]',
      '[aria-selected="true"]',
      '.active',
    ]);
    if (currentThread instanceof HTMLElement) {
      currentThread.setAttribute(CURRENT_THREAD_ATTR, "true");
      setHook(currentThread, "current-thread-marker");
    }
  };

  const annotateReportLinks = () => {
    document.querySelectorAll("a[href]").forEach((node) => {
      if (!(node instanceof HTMLAnchorElement)) return;
      const href = node.getAttribute("href") || "";
      if (/\.(pdf|md|docx?|xlsx?)($|\?)/i.test(href) || node.hasAttribute("download")) {
        setHook(node, "report-download-link");
      }
    });
  };

  const annotateDom = () => {
    annotateLogin();
    annotateMainChat();
    annotateMessages();
    annotateThreads();
    annotateReportLinks();
  };

  const observer = new MutationObserver(() => annotateDom());
  const start = () => {
    annotateDom();
    observer.observe(document.body, {
      childList: true,
      subtree: true,
      attributes: true,
    });
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start, { once: true });
  } else {
    start();
  }
})();
