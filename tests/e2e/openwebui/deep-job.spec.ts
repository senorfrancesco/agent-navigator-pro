import { test, expect, APIRequestContext, Page, Locator } from '@playwright/test';
import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';

type ScenarioManifest = {
  scenario_id: string;
  pair_key: string;
  prompt_variant: string;
  document_path: string;
  prompt: string;
  expected_focus_fragments: string[];
  expected_mode: 'positive' | 'negative';
  architecture_probe: boolean;
};

type OpenWebUIFileRecord = {
  id: string;
  filename: string;
  path: string;
  data?: Record<string, unknown>;
  meta?: {
    name?: string;
    content_type?: string;
    size?: number;
    data?: Record<string, unknown>;
  };
};

type UploadedFileArtifact = {
  upload: OpenWebUIFileRecord;
  chatFile: Record<string, unknown>;
  content: string;
  processStatus: string;
};

type ToolJobTerminalRecord = {
  statusPayload: Record<string, unknown> | null;
  resultPayload: Record<string, unknown> | null;
  timeoutReached: boolean;
};

type RawRunResult = {
  kind: 'ui-controls' | 'matrix';
  scenario_id: string;
  pair_key: string;
  prompt_variant: string;
  document_name: string | null;
  prompt: string;
  expected_mode: string;
  architecture_probe: boolean;
  chat_id: string | null;
  user_message_id: string | null;
  assistant_message_id: string | null;
  uploaded_files: Array<Record<string, unknown>>;
  action_request_summary: Record<string, unknown> | null;
  action_response_http_status: number | null;
  action_response_ok: boolean;
  action_response_payload: Record<string, unknown> | null;
  job_id: string | null;
  status_url: string | null;
  backend_terminal: ToolJobTerminalRecord | null;
  refresh_response_http_status: number | null;
  refresh_response_payload: Record<string, unknown> | null;
  ui: Record<string, unknown>;
  final_chat: Record<string, unknown> | null;
  error: string | null;
};

const REPO_ROOT = path.resolve(__dirname, '../../..');
const OUTPUT_DIR = process.env.OPENWEBUI_EVAL_OUTPUT_DIR?.trim() || path.join(REPO_ROOT, 'output', 'openwebui-deep-job-eval');
const MANIFEST_PATH = process.env.OPENWEBUI_DEEP_JOB_MANIFEST?.trim() || path.join(OUTPUT_DIR, 'manifest.json');
const BACKEND_ENV_PATH = path.join(REPO_ROOT, 'backend', '.env');
const ENV_VALUES = parseEnvFile(BACKEND_ENV_PATH);
const OPENWEBUI_BASE_URL = normalizeBaseUrl(
  process.env.OPENWEBUI_BASE_URL || process.env.BASE_URL || 'http://127.0.0.1:3001'
);
const BACKEND_BASE_URL = normalizeBaseUrl(
  process.env.OPENWEBUI_BACKEND_BASE_URL || ENV_VALUES.AGENT_API_BASE_URL || 'http://127.0.0.1:8000'
);
const OPENWEBUI_MODEL = process.env.OPENWEBUI_MODEL || ENV_VALUES.DEFAULT_OPENWEBUI_MODEL || 'raw.qwen-14b-llm';
const TOOL_SERVER_TOKEN =
  process.env.OPENAPI_TOOL_SERVER_TOKEN || ENV_VALUES.OPENAPI_TOOL_SERVER_TOKEN || 'llm-tools-platform-tool-server-dev-token';
const ADMIN_EMAIL = process.env.WEBUI_ADMIN_EMAIL || ENV_VALUES.WEBUI_ADMIN_EMAIL || 'admin@example.com';
const ADMIN_PASSWORD = process.env.WEBUI_ADMIN_PASSWORD || ENV_VALUES.WEBUI_ADMIN_PASSWORD || 'change-me-now';
const MATRIX_TIMEOUT_MS = parseInteger(process.env.OPENWEBUI_MATRIX_TIMEOUT_MS, 420_000);
const MATRIX_POLL_INTERVAL_MS = parseInteger(process.env.OPENWEBUI_MATRIX_POLL_INTERVAL_MS, 2_500);
const CONTROL_TIMEOUT_MS = parseInteger(process.env.OPENWEBUI_CONTROL_TIMEOUT_MS, 45_000);
const LIVE_MODE = process.env.OPENWEBUI_LIVE_MODE?.trim() === '1';
const LIVE_PAUSE_MS = parseInteger(process.env.OPENWEBUI_LIVE_PAUSE_MS, 900);
const LIVE_TYPING_DELAY_MS = parseInteger(process.env.OPENWEBUI_LIVE_TYPING_DELAY_MS, 85);
const MANIFEST = loadManifestForExecution(MANIFEST_PATH);
const RAW_RESULTS_FILE = path.join(OUTPUT_DIR, 'results.raw.json');

let authToken = '';
const collectedResults: RawRunResult[] = [];

test.describe.configure({ mode: 'serial' });

test.beforeAll(async ({ request }) => {
  fs.mkdirSync(OUTPUT_DIR, { recursive: true });
  authToken = await signIn(request);
});

test.afterAll(async () => {
  writeJson(RAW_RESULTS_FILE, collectedResults);
});

test('openwebui ui-controls refresh deep-job materializes terminal result', async ({ page, request }) => {
  const result: RawRunResult = {
    kind: 'ui-controls',
    scenario_id: 'ui-controls-refresh',
    pair_key: 'ui-controls',
    prompt_variant: 'refresh',
    document_name: null,
    prompt: 'Сфокусируйся на стойке и блоке питания.',
    expected_mode: 'control',
    architecture_probe: false,
    chat_id: null,
    user_message_id: null,
    assistant_message_id: null,
    uploaded_files: [],
    action_request_summary: null,
    action_response_http_status: null,
    action_response_ok: false,
    action_response_payload: null,
    job_id: null,
    status_url: null,
    backend_terminal: null,
    refresh_response_http_status: null,
    refresh_response_payload: null,
    ui: {},
    final_chat: null,
    error: null,
  };
  const outputPath = path.join(OUTPUT_DIR, 'ui-controls-refresh.raw.json');

  try {
    const helperEnabled = await testHelperRouteEnabled(request);
    test.skip(!helperEnabled, 'LLM_TOOLS_PLATFORM_TEST_MODE=1 обязателен для детерминированного UI-control кейса.');

    const createdChat = await createChat(request, authToken, 'Codex UI Controls Deep Job');
    const chatId = String(createdChat.id);
    const userMessageId = crypto.randomUUID();
    const assistantMessageId = crypto.randomUUID();
    result.chat_id = chatId;
    result.user_message_id = userMessageId;
    result.assistant_message_id = assistantMessageId;

    const userMessage = buildUserMessage({
      messageId: userMessageId,
      prompt: result.prompt,
      files: [],
      childrenIds: [assistantMessageId],
      timestamp: unixTimestamp(),
    });
    const assistantPlaceholder = buildAssistantPlaceholder({
      messageId: assistantMessageId,
      parentId: userMessageId,
      timestamp: unixTimestamp(),
    });

    await updateChatHistory(request, authToken, createdChat, {
      [userMessageId]: userMessage,
      [assistantMessageId]: assistantPlaceholder,
    }, assistantMessageId);

    const actionBody = {
      chat_id: chatId,
      id: assistantMessageId,
      model: OPENWEBUI_MODEL,
      session_id: `pw-${assistantMessageId}`,
      messages: [userMessage],
    };
    result.action_request_summary = summarizeActionBody(actionBody);

    const actionResponse = await apiJson(request, authToken, 'POST', `${OPENWEBUI_BASE_URL}/api/chat/actions/equipment_deep_action`, actionBody);
    result.action_response_http_status = actionResponse.status;
    result.action_response_ok = actionResponse.ok;
    result.action_response_payload = summarizePlainObject(actionResponse.payload);
    result.job_id = asOptionalString(actionResponse.payload?.job_id);
    result.status_url = asOptionalString(actionResponse.payload?.status_url);

    const actionAcceptedPayload = summarizeAcceptedActionPayload(actionResponse.payload, []);
    await patchAssistantMessage(request, authToken, chatId, assistantMessageId, actionAcceptedPayload);

    await openChatPage(page, chatId);
    await livePause(page);
    const refreshButton = page.getByRole('button', { name: 'Обновить deep-job' }).first();
    const cancelButton = page.getByRole('button', { name: 'Отменить deep-job' }).first();
    await expect(refreshButton).toBeVisible({ timeout: CONTROL_TIMEOUT_MS });
    await expect(cancelButton).toBeVisible({ timeout: CONTROL_TIMEOUT_MS });
    result.ui.refresh_visible_before = true;
    result.ui.cancel_visible_before = true;

    const debugResponse = {
      assistant_message: 'Synthetic completed result for refresh button.',
      structured_result: {
        summary: 'Synthetic structured payload.',
        source_count: 1,
      },
      sources: [{ name: 'synthetic-source.txt' }],
      artifacts: [{ type: 'markdown', label: 'Synthetic Artifact' }],
      output: [{ type: 'note', text: 'Synthetic output item' }],
    };
    await debugTransitionToolJob(request, result.job_id || '', {
      status: 'completed',
      response: debugResponse,
    });

    const refreshNetwork = page.waitForResponse((response) => {
      return response.request().method() === 'POST' && response.url().includes('/api/chat/actions/tool_job_refresh_action');
    });
    await livePause(page);
    await refreshButton.click();
    await livePause(page);
    const refreshActionResponse = await refreshNetwork;
    await page.reload({ waitUntil: 'domcontentloaded' });
    await livePause(page);

    const chatAfterRefresh = await getChat(request, authToken, chatId);
    const acceptedMessage = getChatMessage(chatAfterRefresh, assistantMessageId);
    const resultMessage = getChatMessage(chatAfterRefresh, asOptionalString(acceptedMessage?.result_message_id));
    result.refresh_response_http_status = refreshActionResponse.status();
    result.refresh_response_payload = summarizeMessage(resultMessage);
    result.final_chat = summarizeChatState(chatAfterRefresh, assistantMessageId);

    const resultSnippet = excerpt(asOptionalString(resultMessage?.content), 96);
    if (resultSnippet) {
      await expect(page.getByText(resultSnippet, { exact: false }).first()).toBeVisible({ timeout: CONTROL_TIMEOUT_MS });
      result.ui.result_visible_after_refresh = true;
    }

    const refreshHidden = await isHidden(refreshButton);
    const cancelHidden = await isHidden(cancelButton);
    result.ui.refresh_hidden_after = refreshHidden;
    result.ui.cancel_hidden_after = cancelHidden;

    expect.soft(asOptionalString(acceptedMessage?.job_status)).toBe('completed');
    expect.soft(asOptionalString(resultMessage?.content) || '').toContain('Synthetic completed result for refresh button.');
    expect.soft(refreshHidden).toBeTruthy();
    expect.soft(cancelHidden).toBeTruthy();
  } catch (error) {
    result.error = formatError(error);
    throw error;
  } finally {
    collectedResults.push(result);
    writeJson(outputPath, result);
  }
});

test('openwebui ui-controls auto deep-job materializes terminal result without manual refresh', async ({ page, request }) => {
  const result: RawRunResult = {
    kind: 'ui-controls',
    scenario_id: 'ui-controls-auto-materialize',
    pair_key: 'ui-controls',
    prompt_variant: 'auto-materialize',
    document_name: null,
    prompt: 'Сфокусируйся на резервировании питания и рисках отказа.',
    expected_mode: 'control',
    architecture_probe: false,
    chat_id: null,
    user_message_id: null,
    assistant_message_id: null,
    uploaded_files: [],
    action_request_summary: null,
    action_response_http_status: null,
    action_response_ok: false,
    action_response_payload: null,
    job_id: null,
    status_url: null,
    backend_terminal: null,
    refresh_response_http_status: null,
    refresh_response_payload: null,
    ui: {},
    final_chat: null,
    error: null,
  };
  const outputPath = path.join(OUTPUT_DIR, 'ui-controls-auto-materialize.raw.json');

  try {
    const helperEnabled = await testHelperRouteEnabled(request);
    test.skip(!helperEnabled, 'LLM_TOOLS_PLATFORM_TEST_MODE=1 обязателен для детерминированного автообновления deep-job.');

    const createdChat = await createChat(request, authToken, 'Codex UI Controls Auto Deep Job');
    const chatId = String(createdChat.id);
    const userMessageId = crypto.randomUUID();
    const assistantMessageId = crypto.randomUUID();
    result.chat_id = chatId;
    result.user_message_id = userMessageId;
    result.assistant_message_id = assistantMessageId;

    const userMessage = buildUserMessage({
      messageId: userMessageId,
      prompt: result.prompt,
      files: [],
      childrenIds: [assistantMessageId],
      timestamp: unixTimestamp(),
    });
    const assistantPlaceholder = buildAssistantPlaceholder({
      messageId: assistantMessageId,
      parentId: userMessageId,
      timestamp: unixTimestamp(),
    });

    await updateChatHistory(request, authToken, createdChat, {
      [userMessageId]: userMessage,
      [assistantMessageId]: assistantPlaceholder,
    }, assistantMessageId);

    const actionBody = {
      chat_id: chatId,
      id: assistantMessageId,
      model: OPENWEBUI_MODEL,
      session_id: `pw-${assistantMessageId}`,
      messages: [userMessage],
    };
    result.action_request_summary = summarizeActionBody(actionBody);

    const actionResponse = await apiJson(request, authToken, 'POST', `${OPENWEBUI_BASE_URL}/api/chat/actions/equipment_deep_action`, actionBody);
    result.action_response_http_status = actionResponse.status;
    result.action_response_ok = actionResponse.ok;
    result.action_response_payload = summarizePlainObject(actionResponse.payload);
    result.job_id = asOptionalString(actionResponse.payload?.job_id);
    result.status_url = asOptionalString(actionResponse.payload?.status_url);

    const actionAcceptedPayload = summarizeAcceptedActionPayload(actionResponse.payload, []);
    await patchAssistantMessage(request, authToken, chatId, assistantMessageId, actionAcceptedPayload);

    await openChatPage(page, chatId);
    await livePause(page);
    const refreshButton = page.getByRole('button', { name: 'Обновить deep-job' }).first();
    const cancelButton = page.getByRole('button', { name: 'Отменить deep-job' }).first();
    await expect(refreshButton).toBeVisible({ timeout: CONTROL_TIMEOUT_MS });
    await expect(cancelButton).toBeVisible({ timeout: CONTROL_TIMEOUT_MS });
    result.ui.refresh_visible_before = true;
    result.ui.cancel_visible_before = true;

    const debugResponse = {
      assistant_message: 'Synthetic completed result for auto materialization.',
      structured_result: {
        summary: 'Synthetic structured payload for automatic materialization.',
        source_count: 1,
      },
      sources: [{ name: 'synthetic-auto-source.txt' }],
      artifacts: [{ type: 'markdown', label: 'Synthetic Auto Artifact' }],
      output: [{ type: 'note', text: 'Synthetic auto output item' }],
    };
    await debugTransitionToolJob(request, result.job_id || '', {
      status: 'completed',
      response: debugResponse,
    });

    const chatAfterAuto = await waitForResultMessageMaterialized(
      request,
      authToken,
      chatId,
      assistantMessageId,
      CONTROL_TIMEOUT_MS
    );
    const acceptedMessage = getChatMessage(chatAfterAuto, assistantMessageId);
    const resultMessage = getChatMessage(chatAfterAuto, asOptionalString(acceptedMessage?.result_message_id));
    result.final_chat = summarizeChatState(chatAfterAuto, assistantMessageId);

    const resultSnippet = excerpt(asOptionalString(resultMessage?.content), 96);
    if (!resultSnippet) {
      throw new Error('Автоматическое завершение deep-job не создало итоговое сообщение.');
    }

    await expect(page.getByText(resultSnippet, { exact: false }).first()).toBeVisible({ timeout: CONTROL_TIMEOUT_MS });
    result.ui.result_visible_after_auto = true;

    const refreshHidden = await isHidden(refreshButton);
    const cancelHidden = await isHidden(cancelButton);
    result.ui.refresh_hidden_after = refreshHidden;
    result.ui.cancel_hidden_after = cancelHidden;

    await page.reload({ waitUntil: 'domcontentloaded' });
    await livePause(page, LIVE_PAUSE_MS * 2);
    await expect(page.getByText(resultSnippet, { exact: false }).first()).toBeVisible({ timeout: CONTROL_TIMEOUT_MS });
    result.ui.result_visible_after_reload = true;

    expect.soft(asOptionalString(acceptedMessage?.job_status)).toBe('completed');
    expect.soft(asOptionalString(resultMessage?.content) || '').toContain('Synthetic completed result for auto materialization.');
    expect.soft(refreshHidden).toBeTruthy();
    expect.soft(cancelHidden).toBeTruthy();
  } catch (error) {
    result.error = formatError(error);
    throw error;
  } finally {
    collectedResults.push(result);
    writeJson(outputPath, result);
  }
});

for (const scenario of MANIFEST) {
  test(`openwebui deep-job matrix ${scenario.scenario_id}`, async ({ page, request }) => {
    test.slow();
    test.setTimeout(MATRIX_TIMEOUT_MS + 120_000);

    const result: RawRunResult = {
      kind: 'matrix',
      scenario_id: scenario.scenario_id,
      pair_key: scenario.pair_key,
      prompt_variant: scenario.prompt_variant,
      document_name: path.basename(scenario.document_path),
      prompt: scenario.prompt,
      expected_mode: scenario.expected_mode,
      architecture_probe: scenario.architecture_probe,
      chat_id: null,
      user_message_id: null,
      assistant_message_id: null,
      uploaded_files: [],
      action_request_summary: null,
      action_response_http_status: null,
      action_response_ok: false,
      action_response_payload: null,
      job_id: null,
      status_url: null,
      backend_terminal: null,
      refresh_response_http_status: null,
      refresh_response_payload: null,
      ui: {},
      final_chat: null,
      error: null,
    };
    const outputPath = path.join(OUTPUT_DIR, `${scenario.scenario_id}.raw.json`);

    try {
      const createdChat = await createChat(request, authToken, `Codex Eval ${scenario.scenario_id}`);
      const chatId = String(createdChat.id);
      result.chat_id = chatId;

      await openChatPage(page, chatId);
      await livePause(page);
      const uploaded = await uploadFileThroughUi(page, request, authToken, scenario.document_path);
      await livePause(page);
      await previewPromptInComposer(page, scenario.prompt);
      await livePause(page);
      result.uploaded_files = [summarizeUploadedFile(uploaded)];

      const userMessageId = crypto.randomUUID();
      const assistantMessageId = crypto.randomUUID();
      result.user_message_id = userMessageId;
      result.assistant_message_id = assistantMessageId;

      const userMessage = buildUserMessage({
        messageId: userMessageId,
        prompt: scenario.prompt,
        files: [uploaded.chatFile],
        childrenIds: [assistantMessageId],
        timestamp: unixTimestamp(),
      });
      const assistantPlaceholder = buildAssistantPlaceholder({
        messageId: assistantMessageId,
        parentId: userMessageId,
        timestamp: unixTimestamp(),
      });

      await updateChatHistory(request, authToken, createdChat, {
        [userMessageId]: userMessage,
        [assistantMessageId]: assistantPlaceholder,
      }, assistantMessageId);

      const actionSources = [
        {
          role: 'assistant',
          sources: [buildSourceItem(uploaded)],
        },
      ];
      const actionBody = {
        chat_id: chatId,
        id: assistantMessageId,
        model: OPENWEBUI_MODEL,
        session_id: `pw-${assistantMessageId}`,
        messages: [userMessage, ...actionSources],
      };
      result.action_request_summary = summarizeActionBody(actionBody);

      const actionResponse = await apiJson(request, authToken, 'POST', `${OPENWEBUI_BASE_URL}/api/chat/actions/equipment_deep_action`, actionBody);
      result.action_response_http_status = actionResponse.status;
      result.action_response_ok = actionResponse.ok;
      result.action_response_payload = summarizePlainObject(actionResponse.payload);
      result.job_id = asOptionalString(actionResponse.payload?.job_id);
      result.status_url = asOptionalString(actionResponse.payload?.status_url);

      const actionAcceptedPayload = summarizeAcceptedActionPayload(actionResponse.payload, [buildSourceItem(uploaded)]);
      await patchAssistantMessage(request, authToken, chatId, assistantMessageId, actionAcceptedPayload);
      await page.reload({ waitUntil: 'domcontentloaded' });
      await livePause(page, LIVE_PAUSE_MS * 2);

      const acceptedSnippet = excerpt(asOptionalString(actionResponse.payload?.content), 90);
      if (acceptedSnippet) {
        try {
          await expect(page.getByText(acceptedSnippet, { exact: false }).first()).toBeVisible({ timeout: CONTROL_TIMEOUT_MS });
          result.ui.accepted_visible = true;
        } catch {
          result.ui.accepted_visible = false;
        }
      }

      if (result.job_id && result.status_url) {
        result.backend_terminal = await waitForToolJobTerminal(request, result.status_url, MATRIX_TIMEOUT_MS, MATRIX_POLL_INTERVAL_MS);
      }

      const needsRefresh =
        Boolean(result.job_id) &&
        Boolean(result.status_url) &&
        Boolean(result.backend_terminal?.statusPayload) &&
        asOptionalString(result.backend_terminal?.statusPayload?.status) !== null;

      if (needsRefresh) {
        if (LIVE_MODE) {
          const refreshButton = page.getByRole('button', { name: 'Обновить deep-job' }).first();
          const refreshNetwork = page.waitForResponse((response) => {
            return response.request().method() === 'POST' && response.url().includes('/api/chat/actions/tool_job_refresh_action');
          });
          await livePause(page);
          await refreshButton.click();
          await livePause(page);
          const refreshResponse = await refreshNetwork;
          const refreshText = await refreshResponse.text();
          result.refresh_response_http_status = refreshResponse.status();
          try {
            result.refresh_response_payload = summarizePlainObject(JSON.parse(refreshText) as Record<string, unknown>);
          } catch {
            result.refresh_response_payload = null;
          }
        } else {
          const refreshBody = {
            chat_id: chatId,
            id: assistantMessageId,
            model: OPENWEBUI_MODEL,
            session_id: `refresh-${assistantMessageId}`,
            messages: listChatMessages(await getChat(request, authToken, chatId)),
          };
          const refreshResponse = await apiJson(request, authToken, 'POST', `${OPENWEBUI_BASE_URL}/api/chat/actions/tool_job_refresh_action`, refreshBody);
          result.refresh_response_http_status = refreshResponse.status;
          result.refresh_response_payload = summarizePlainObject(refreshResponse.payload);
        }
      }

      await page.reload({ waitUntil: 'domcontentloaded' });
      await livePause(page, LIVE_PAUSE_MS * 2);
      const finalChat = await getChat(request, authToken, chatId);
      result.final_chat = summarizeChatState(finalChat, assistantMessageId);
      const acceptedMessage = getChatMessage(finalChat, assistantMessageId);
      const resultMessage = getChatMessage(finalChat, asOptionalString(acceptedMessage?.result_message_id));

      const resultSnippet = excerpt(asOptionalString(resultMessage?.content), 96);
      if (resultSnippet) {
        try {
          await expect(page.getByText(resultSnippet, { exact: false }).first()).toBeVisible({ timeout: CONTROL_TIMEOUT_MS });
          result.ui.result_visible = true;
        } catch {
          result.ui.result_visible = false;
        }
      } else {
        result.ui.result_visible = false;
      }

      const refreshButton = page.getByRole('button', { name: 'Обновить deep-job' }).first();
      const cancelButton = page.getByRole('button', { name: 'Отменить deep-job' }).first();
      result.ui.refresh_hidden_after = await isHidden(refreshButton);
      result.ui.cancel_hidden_after = await isHidden(cancelButton);

      expect.soft(result.action_response_ok).toBeTruthy();
      expect.soft(Boolean(result.job_id)).toBeTruthy();
    } catch (error) {
      result.error = formatError(error);
      throw error;
    } finally {
      collectedResults.push(result);
      writeJson(outputPath, result);
    }
  });
}

function parseEnvFile(filePath: string): Record<string, string> {
  if (!fs.existsSync(filePath)) {
    return {};
  }
  const values: Record<string, string> = {};
  for (const rawLine of fs.readFileSync(filePath, 'utf-8').split(/\r?\n/u)) {
    const line = rawLine.trim();
    if (!line || line.startsWith('#')) {
      continue;
    }
    const separator = line.indexOf('=');
    if (separator === -1) {
      continue;
    }
    const key = line.slice(0, separator).trim();
    const value = line.slice(separator + 1).trim().replace(/^['"]|['"]$/gu, '');
    values[key] = value;
  }
  return values;
}

function loadManifestForExecution(manifestPath: string): ScenarioManifest[] {
  if (!fs.existsSync(manifestPath)) {
    if (LIVE_MODE) {
      return buildLiveModeManifestFallback();
    }
    throw new Error(`Missing Open WebUI deep-job manifest: ${manifestPath}`);
  }
  const payload = JSON.parse(fs.readFileSync(manifestPath, 'utf-8')) as { scenarios?: ScenarioManifest[] };
  if (!Array.isArray(payload.scenarios) || payload.scenarios.length === 0) {
    throw new Error(`Open WebUI deep-job manifest is empty: ${manifestPath}`);
  }
  return payload.scenarios;
}

function buildLiveModeManifestFallback(): ScenarioManifest[] {
  return [
    {
      scenario_id: 'live-requirements-p1',
      pair_key: 'live-requirements',
      prompt_variant: 'p1',
      document_path: path.join(REPO_ROOT, 'documents', 'Requirements.pdf'),
      prompt: 'Определи тип документа, ключевые категории оборудования и обязательные характеристики.',
      expected_focus_fragments: ['тип документа', 'категории оборудования', 'обязательные характеристики'],
      expected_mode: 'positive',
      architecture_probe: true,
    },
  ];
}

function normalizeBaseUrl(value: string): string {
  return value.replace(/\/+$/u, '');
}

function parseInteger(raw: string | undefined, fallback: number): number {
  const value = Number.parseInt(String(raw || '').trim(), 10);
  return Number.isFinite(value) ? value : fallback;
}

function writeJson(filePath: string, value: unknown): void {
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  fs.writeFileSync(filePath, `${JSON.stringify(value, null, 2)}\n`, 'utf-8');
}

async function signIn(request: APIRequestContext): Promise<string> {
  const response = await request.post(`${OPENWEBUI_BASE_URL}/api/v1/auths/signin`, {
    data: {
      email: ADMIN_EMAIL,
      password: ADMIN_PASSWORD,
    },
  });
  expect(response.ok()).toBeTruthy();
  const payload = (await response.json()) as { token?: string };
  if (!payload.token) {
    throw new Error('Open WebUI sign-in did not return a bearer token.');
  }
  return payload.token;
}

async function apiJson(
  request: APIRequestContext,
  token: string,
  method: 'GET' | 'POST',
  url: string,
  payload?: Record<string, unknown>
): Promise<{ status: number; ok: boolean; payload: Record<string, any> | null; text: string }> {
  const response = await request.fetch(url, {
    method,
    headers: {
      Authorization: `Bearer ${token}`,
      Accept: 'application/json',
      'Content-Type': 'application/json',
    },
    data: payload,
  });
  const text = await response.text();
  let parsed: Record<string, any> | null = null;
  if (text.trim()) {
    try {
      parsed = JSON.parse(text) as Record<string, any>;
    } catch {
      parsed = null;
    }
  }
  return {
    status: response.status(),
    ok: response.ok(),
    payload: parsed,
    text,
  };
}

async function createChat(request: APIRequestContext, token: string, title: string): Promise<Record<string, any>> {
  const response = await apiJson(request, token, 'POST', `${OPENWEBUI_BASE_URL}/api/v1/chats/new`, {
    chat: {
      id: '',
      title,
      models: [OPENWEBUI_MODEL],
      params: {},
      history: {
        messages: {},
        currentId: null,
      },
      messages: [],
    },
  });
  expect(response.ok).toBeTruthy();
  if (!response.payload) {
    throw new Error(`Open WebUI create chat returned no payload for title=${title}`);
  }
  return response.payload;
}

async function getChat(request: APIRequestContext, token: string, chatId: string): Promise<Record<string, any>> {
  const response = await apiJson(request, token, 'GET', `${OPENWEBUI_BASE_URL}/api/v1/chats/${chatId}`);
  expect(response.ok).toBeTruthy();
  if (!response.payload) {
    throw new Error(`Open WebUI get chat returned no payload for chat_id=${chatId}`);
  }
  return response.payload;
}

async function updateChatHistory(
  request: APIRequestContext,
  token: string,
  chatPayload: Record<string, any>,
  messages: Record<string, any>,
  currentId: string
): Promise<Record<string, any>> {
  const nextChat = {
    ...(chatPayload.chat || {}),
    models: Array.isArray(chatPayload.chat?.models) && chatPayload.chat.models.length > 0 ? chatPayload.chat.models : [OPENWEBUI_MODEL],
    params: chatPayload.chat?.params || {},
    history: {
      ...(chatPayload.chat?.history || {}),
      messages,
      currentId,
    },
  };
  const response = await apiJson(request, token, 'POST', `${OPENWEBUI_BASE_URL}/api/v1/chats/${chatPayload.id}`, {
    chat: nextChat,
  });
  expect(response.ok).toBeTruthy();
  if (!response.payload) {
    throw new Error(`Open WebUI update chat returned no payload for chat_id=${chatPayload.id}`);
  }
  return response.payload;
}

async function patchAssistantMessage(
  request: APIRequestContext,
  token: string,
  chatId: string,
  assistantMessageId: string,
  acceptedPayload: Record<string, unknown>
): Promise<Record<string, any>> {
  const chatPayload = await getChat(request, token, chatId);
  const messages = {
    ...(chatPayload.chat?.history?.messages || {}),
  };
  const existing = messages[assistantMessageId] || {};
  messages[assistantMessageId] = {
    ...existing,
    ...acceptedPayload,
    id: assistantMessageId,
  };
  return updateChatHistory(request, token, chatPayload, messages, assistantMessageId);
}

async function openChatPage(page: Page, chatId: string): Promise<void> {
  await page.addInitScript((tokenValue: string) => {
    window.localStorage.setItem('token', tokenValue);
  }, authToken);
  await page.goto(`${OPENWEBUI_BASE_URL}/c/${chatId}`, {
    waitUntil: 'domcontentloaded',
  });
  await page.locator('input[type="file"]').first().waitFor({ state: 'attached', timeout: CONTROL_TIMEOUT_MS });
}

async function previewPromptInComposer(page: Page, prompt: string): Promise<void> {
  if (!LIVE_MODE) {
    return;
  }
  const composer = await locateVisibleComposer(page);
  if (!composer) {
    return;
  }
  try {
    await composer.click();
    await livePause(page, Math.min(LIVE_PAUSE_MS, 500));
    await composer.pressSequentially(prompt, { delay: LIVE_TYPING_DELAY_MS });
  } catch {
    try {
      await composer.click();
      await page.keyboard.type(prompt, { delay: LIVE_TYPING_DELAY_MS });
    } catch {
      // Live preview is best-effort and must not break the actual backend contour.
    }
  }
}

async function uploadFileThroughUi(
  page: Page,
  request: APIRequestContext,
  token: string,
  filePath: string
): Promise<UploadedFileArtifact> {
  const inputs = page.locator('input[type="file"]');
  const inputCount = Math.max(await inputs.count(), 1);
  let uploaded: OpenWebUIFileRecord | null = null;
  let lastError: unknown = null;

  for (let index = 0; index < inputCount; index += 1) {
    try {
      const uploadResponsePromise = page.waitForResponse((response) => {
        return response.request().method() === 'POST' && response.url().includes('/api/v1/files/?process=true');
      }, { timeout: CONTROL_TIMEOUT_MS });
      await inputs.nth(index).setInputFiles(filePath);
      const uploadResponse = await uploadResponsePromise;
      uploaded = (await uploadResponse.json()) as OpenWebUIFileRecord;
      break;
    } catch (error) {
      lastError = error;
    }
  }

  if (!uploaded) {
    throw lastError instanceof Error ? lastError : new Error(`Failed to upload file via UI: ${filePath}`);
  }

  const processStatus = await waitForFileProcessing(request, token, uploaded.id);
  const contentResponse = await apiJson(request, token, 'GET', `${OPENWEBUI_BASE_URL}/api/v1/files/${uploaded.id}/data/content`);
  const content = asOptionalString(contentResponse.payload?.content) || '';
  const fileName = uploaded.meta?.name || uploaded.filename || path.basename(filePath);

  try {
    await expect(page.getByRole('button', { name: new RegExp(escapeRegExp(fileName), 'u') }).first()).toBeVisible({
      timeout: CONTROL_TIMEOUT_MS,
    });
  } catch {
    // UI may update after the message is persisted; do not hard-fail upload capture on chip visibility.
  }

  return {
    upload: uploaded,
    chatFile: {
      type: 'file',
      file: uploaded,
      id: uploaded.id,
      url: uploaded.id,
      name: fileName,
      status: 'uploaded',
      size: uploaded.meta?.size || 0,
      error: '',
      itemId: crypto.randomUUID(),
      content_type: uploaded.meta?.content_type || '',
    },
    content,
    processStatus,
  };
}

async function waitForFileProcessing(request: APIRequestContext, token: string, fileId: string): Promise<string> {
  const deadline = Date.now() + CONTROL_TIMEOUT_MS;
  let lastStatus = 'pending';

  while (Date.now() < deadline) {
    const response = await apiJson(request, token, 'GET', `${OPENWEBUI_BASE_URL}/api/v1/files/${fileId}/process/status`);
    const status = asOptionalString(response.payload?.status) || 'pending';
    lastStatus = status;
    if (status === 'completed' || status === 'failed') {
      return status;
    }
    await new Promise((resolve) => setTimeout(resolve, 1_000));
  }

  return lastStatus;
}

async function locateVisibleComposer(page: Page): Promise<Locator | null> {
  const candidates = page.locator('textarea, [contenteditable="true"], [role="textbox"]');
  const count = await candidates.count();
  for (let index = 0; index < count; index += 1) {
    const candidate = candidates.nth(index);
    try {
      if (await candidate.isVisible()) {
        return candidate;
      }
    } catch {
      // Ignore detached candidates while Open WebUI re-renders.
    }
  }
  return null;
}

function buildUserMessage(input: {
  messageId: string;
  prompt: string;
  files: Record<string, unknown>[];
  childrenIds: string[];
  timestamp: number;
}): Record<string, unknown> {
  return {
    id: input.messageId,
    parentId: null,
    childrenIds: input.childrenIds,
    role: 'user',
    content: input.prompt,
    files: input.files,
    timestamp: input.timestamp,
    models: [OPENWEBUI_MODEL],
  };
}

function buildAssistantPlaceholder(input: {
  messageId: string;
  parentId: string;
  timestamp: number;
}): Record<string, unknown> {
  return {
    id: input.messageId,
    parentId: input.parentId,
    childrenIds: [],
    role: 'assistant',
    content: '',
    model: OPENWEBUI_MODEL,
    modelName: OPENWEBUI_MODEL,
    modelIdx: 0,
    timestamp: input.timestamp,
    done: true,
    output: [],
  };
}

function buildSourceItem(uploaded: UploadedFileArtifact): Record<string, unknown> {
  return {
    source: uploaded.chatFile,
    document: uploaded.content.trim() ? [uploaded.content] : [],
  };
}

function summarizeAcceptedActionPayload(
  payload: Record<string, any> | null,
  sources: Array<Record<string, unknown>>
): Record<string, unknown> {
  const content = asOptionalString(payload?.content) || 'Пустой accepted payload.';
  const jobId = asOptionalString(payload?.job_id);
  const statusUrl = asOptionalString(payload?.status_url);
  const toolJob = payload?.tool_job || {
    job_id: jobId,
    status_url: statusUrl,
    tool_name: 'analyze_equipment_deep',
    status: 'accepted',
  };

  return {
    content,
    done: true,
    output: [],
    sources,
    tool_job: toolJob,
    job_status: asOptionalString(toolJob.status) || 'accepted',
    actions_disabled: false,
    statusHistory: [
      {
        description: content,
        status: asOptionalString(toolJob.status) || 'accepted',
        job_id: jobId,
        status_url: statusUrl,
        tool_name: asOptionalString(toolJob.tool_name) || 'analyze_equipment_deep',
      },
    ],
  };
}

async function waitForToolJobTerminal(
  request: APIRequestContext,
  statusUrl: string,
  timeoutMs: number,
  pollIntervalMs: number
): Promise<ToolJobTerminalRecord> {
  const absoluteStatusUrl = new URL(statusUrl, BACKEND_BASE_URL).toString();
  const deadline = Date.now() + timeoutMs;
  let lastStatusPayload: Record<string, unknown> | null = null;

  while (Date.now() < deadline) {
    const response = await request.get(absoluteStatusUrl, {
      headers: {
        Authorization: `Bearer ${TOOL_SERVER_TOKEN}`,
        Accept: 'application/json',
      },
    });
    if (!response.ok()) {
      await new Promise((resolve) => setTimeout(resolve, pollIntervalMs));
      continue;
    }
    lastStatusPayload = (await response.json()) as Record<string, unknown>;
    const status = asOptionalString(lastStatusPayload.status) || 'unknown';
    if (['completed', 'failed', 'cancelled'].includes(status)) {
      let resultPayload: Record<string, unknown> | null = null;
      if (status === 'completed') {
        const resultResponse = await request.get(`${absoluteStatusUrl}/result`, {
          headers: {
            Authorization: `Bearer ${TOOL_SERVER_TOKEN}`,
            Accept: 'application/json',
          },
        });
        if (resultResponse.ok()) {
          resultPayload = (await resultResponse.json()) as Record<string, unknown>;
        }
      }
      return {
        statusPayload: summarizePlainObject(lastStatusPayload),
        resultPayload: summarizePlainObject(resultPayload),
        timeoutReached: false,
      };
    }
    await new Promise((resolve) => setTimeout(resolve, pollIntervalMs));
  }

  return {
    statusPayload: summarizePlainObject(lastStatusPayload),
    resultPayload: null,
    timeoutReached: true,
  };
}

async function testHelperRouteEnabled(request: APIRequestContext): Promise<boolean> {
  const response = await request.post(`${BACKEND_BASE_URL}/debug/test/tool-jobs/nonexistent/transition`, {
    headers: {
      Accept: 'application/json',
      'Content-Type': 'application/json',
    },
    data: {
      status: 'running',
    },
  });
  const text = await response.text();
  if (response.status() !== 404) {
    return response.ok();
  }
  try {
    const payload = JSON.parse(text) as { detail?: string };
    return String(payload.detail || '').startsWith('unknown-tool-job:');
  } catch {
    return false;
  }
}

async function debugTransitionToolJob(
  request: APIRequestContext,
  jobId: string,
  payload: Record<string, unknown>
): Promise<void> {
  const response = await request.post(`${BACKEND_BASE_URL}/debug/test/tool-jobs/${jobId}/transition`, {
    headers: {
      Accept: 'application/json',
      'Content-Type': 'application/json',
    },
    data: payload,
  });
  expect(response.ok()).toBeTruthy();
}

async function waitForResultMessageMaterialized(
  request: APIRequestContext,
  token: string,
  chatId: string,
  acceptedMessageId: string,
  timeoutMs: number
): Promise<Record<string, any>> {
  const deadline = Date.now() + timeoutMs;

  while (Date.now() < deadline) {
    const chatPayload = await getChat(request, token, chatId);
    const acceptedMessage = getChatMessage(chatPayload, acceptedMessageId);
    const resultMessageId = asOptionalString(acceptedMessage?.result_message_id);
    const resultMessage = getChatMessage(chatPayload, resultMessageId);
    const actionsDisabled = Boolean(acceptedMessage?.actions_disabled);
    const jobStatus = asOptionalString(acceptedMessage?.job_status);
    if (resultMessage && actionsDisabled && jobStatus === 'completed') {
      return chatPayload;
    }
    await new Promise((resolve) => setTimeout(resolve, 500));
  }

  throw new Error(`Timed out waiting for automatic deep-job materialization for chat_id=${chatId}`);
}

function getChatMessage(chatPayload: Record<string, any>, messageId: string | null): Record<string, any> | null {
  if (!messageId) {
    return null;
  }
  return (chatPayload.chat?.history?.messages || {})[messageId] || null;
}

function listChatMessages(chatPayload: Record<string, any>): Record<string, unknown>[] {
  const messages = Object.values(chatPayload.chat?.history?.messages || {}) as Record<string, unknown>[];
  return messages.sort((left, right) => {
    const leftTs = Number((left as Record<string, unknown>).timestamp || 0);
    const rightTs = Number((right as Record<string, unknown>).timestamp || 0);
    return leftTs - rightTs;
  });
}

function summarizeUploadedFile(uploaded: UploadedFileArtifact): Record<string, unknown> {
  return {
    id: uploaded.upload.id,
    name: uploaded.upload.meta?.name || uploaded.upload.filename || null,
    size: uploaded.upload.meta?.size || 0,
    content_type: uploaded.upload.meta?.content_type || null,
    path: uploaded.upload.path,
    process_status: uploaded.processStatus,
    content_length: uploaded.content.length,
  };
}

function summarizeActionBody(payload: Record<string, unknown>): Record<string, unknown> {
  const messages = Array.isArray(payload.messages) ? (payload.messages as Array<Record<string, unknown>>) : [];
  return {
    chat_id: asOptionalString(payload.chat_id),
    id: asOptionalString(payload.id),
    model: asOptionalString(payload.model),
    session_id: asOptionalString(payload.session_id),
    message_roles: messages.map((message) => asOptionalString(message.role)),
    last_user_prompt_excerpt: excerpt(
      asOptionalString(
        messages
          .filter((message) => message.role === 'user')
          .map((message) => asOptionalString(message.content))
          .filter(Boolean)
          .pop()
      ),
      160
    ),
    source_file_names: messages.flatMap((message) => {
      const sources = Array.isArray(message.sources) ? (message.sources as Array<Record<string, any>>) : [];
      return sources.map((sourceItem) => {
        return (
          asOptionalString(sourceItem.source?.name) ||
          asOptionalString(sourceItem.source?.file?.filename) ||
          'unknown'
        );
      });
    }),
  };
}

function summarizeChatState(chatPayload: Record<string, any>, acceptedMessageId: string): Record<string, unknown> {
  const acceptedMessage = getChatMessage(chatPayload, acceptedMessageId);
  const resultMessage = getChatMessage(chatPayload, asOptionalString(acceptedMessage?.result_message_id));
  return {
    accepted_message: summarizeMessage(acceptedMessage),
    result_message: summarizeMessage(resultMessage),
    history_current_id: asOptionalString(chatPayload.chat?.history?.currentId),
  };
}

function summarizeMessage(message: Record<string, any> | null): Record<string, unknown> | null {
  if (!message) {
    return null;
  }
  const statusHistory = Array.isArray(message.statusHistory)
    ? message.statusHistory.map((item: Record<string, unknown>) => {
        return {
          description_excerpt: excerpt(asOptionalString(item.description), 160),
          status: asOptionalString(item.status),
          job_id: asOptionalString(item.job_id),
          status_url: asOptionalString(item.status_url),
          tool_name: asOptionalString(item.tool_name),
          action: asOptionalString(item.action),
          count: typeof item.count === 'number' ? item.count : null,
        };
      })
    : [];
  const sources = Array.isArray(message.sources)
    ? message.sources.map((item: Record<string, any>) => {
        return {
          name:
            asOptionalString(item.source?.name) ||
            asOptionalString(item.source?.file?.filename) ||
            asOptionalString(item.source?.file?.meta?.name),
          document_chunks: Array.isArray(item.document) ? item.document.length : 0,
          metadata_count: Array.isArray(item.metadata) ? item.metadata.length : 0,
        };
      })
    : [];
  return {
    id: asOptionalString(message.id),
    parentId: asOptionalString(message.parentId),
    role: asOptionalString(message.role),
    content_excerpt: excerpt(asOptionalString(message.content), 240),
    job_status: asOptionalString(message.job_status),
    result_message_id: asOptionalString(message.result_message_id),
    actions_disabled: Boolean(message.actions_disabled),
    tool_job: summarizePlainObject(message.tool_job),
    childrenIds: Array.isArray(message.childrenIds) ? message.childrenIds : [],
    statusHistory: statusHistory,
    sources: sources,
    output_count: Array.isArray(message.output) ? message.output.length : 0,
  };
}

function summarizePlainObject(value: unknown): Record<string, any> | null {
  if (!value || typeof value !== 'object') {
    return null;
  }
  return trimValue(value) as Record<string, any>;
}

function trimValue(value: unknown, depth = 0): unknown {
  if (depth >= 4) {
    return '[max-depth]';
  }
  if (typeof value === 'string') {
    return value.length > 500 ? `${value.slice(0, 500)}…` : value;
  }
  if (Array.isArray(value)) {
    return value.slice(0, 8).map((item) => trimValue(item, depth + 1));
  }
  if (!value || typeof value !== 'object') {
    return value;
  }
  return Object.fromEntries(
    Object.entries(value as Record<string, unknown>)
      .slice(0, 20)
      .map(([key, child]) => [key, trimValue(child, depth + 1)])
  );
}

async function isHidden(locator: Locator): Promise<boolean> {
  try {
    return await locator.isHidden();
  } catch {
    return true;
  }
}

async function livePause(page: Page, durationMs = LIVE_PAUSE_MS): Promise<void> {
  if (!LIVE_MODE || durationMs <= 0) {
    return;
  }
  await page.waitForTimeout(durationMs);
}

function excerpt(value: string | null | undefined, length: number): string {
  const text = normalizeWhitespace(value);
  if (!text) {
    return '';
  }
  return text.length > length ? `${text.slice(0, length)}…` : text;
}

function normalizeWhitespace(value: string | null | undefined): string {
  return String(value || '').replace(/\s+/gu, ' ').trim();
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/gu, '\\$&');
}

function formatError(error: unknown): string {
  if (error instanceof Error) {
    return error.stack || error.message;
  }
  return String(error);
}

function asOptionalString(value: unknown): string | null {
  const text = String(value ?? '').trim();
  return text ? text : null;
}

function unixTimestamp(): number {
  return Math.floor(Date.now() / 1000);
}
