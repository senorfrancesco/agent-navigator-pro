import { test, expect, APIRequestContext, Page, Locator } from '@playwright/test';
import { spawnSync } from 'node:child_process';
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
  kind: 'ui-controls' | 'matrix' | 'native';
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
  native_request_summary?: Record<string, unknown> | null;
  native_tool_name?: string | null;
  native_route_patched?: boolean;
  launch_classification?: 'confirmed_launch' | 'blocked_unconfirmed_launch' | null;
  persisted_function_call_output?: string | null;
  persisted_output_summary?: Record<string, unknown> | null;
  ui: Record<string, unknown>;
  final_chat: Record<string, unknown> | null;
  error: string | null;
};

const REPO_ROOT = path.resolve(__dirname, '../../..');
const BOOTSTRAP_SCRIPT = path.join(REPO_ROOT, 'scripts', 'bootstrap_openwebui.py');
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
const DOCUMENT_SERVER_BASE_URL = normalizeBaseUrl(process.env.OPENWEBUI_DOCUMENT_SERVER_BASE_URL || 'http://127.0.0.1:8001');
const LEGAL_SERVER_BASE_URL = normalizeBaseUrl(process.env.OPENWEBUI_LEGAL_SERVER_BASE_URL || 'http://127.0.0.1:8002');
const UMS_BASE_URL = normalizeBaseUrl(process.env.OPENWEBUI_UMS_BASE_URL || ENV_VALUES.AGENT_API_UMS_URL || 'http://127.0.0.1:8090');
const OPENWEBUI_MODEL = process.env.OPENWEBUI_MODEL || ENV_VALUES.DEFAULT_OPENWEBUI_MODEL || 'raw.qwen-14b-llm';
const TOOL_SERVER_TOKEN =
  process.env.OPENAPI_TOOL_SERVER_TOKEN || ENV_VALUES.OPENAPI_TOOL_SERVER_TOKEN || 'llm-tools-platform-tool-server-dev-token';
const ADMIN_EMAIL = process.env.WEBUI_ADMIN_EMAIL || ENV_VALUES.WEBUI_ADMIN_EMAIL || 'admin@example.com';
const ADMIN_PASSWORD = process.env.WEBUI_ADMIN_PASSWORD || ENV_VALUES.WEBUI_ADMIN_PASSWORD || 'change-me-now';
const MATRIX_TIMEOUT_MS = parseInteger(process.env.OPENWEBUI_MATRIX_TIMEOUT_MS, 420_000);
const MATRIX_POLL_INTERVAL_MS = parseInteger(process.env.OPENWEBUI_MATRIX_POLL_INTERVAL_MS, 2_500);
const CONTROL_TIMEOUT_MS = parseInteger(process.env.OPENWEBUI_CONTROL_TIMEOUT_MS, 45_000);
const LIVE_MODE = process.env.OPENWEBUI_LIVE_MODE?.trim() === '1';
const AUTO_SYNC_ENABLED = process.env.OPENWEBUI_SKIP_AUTOSYNC?.trim() !== '1';
const LIVE_PAUSE_MS = parseInteger(process.env.OPENWEBUI_LIVE_PAUSE_MS, 900);
const LIVE_TYPING_DELAY_MS = parseInteger(process.env.OPENWEBUI_LIVE_TYPING_DELAY_MS, 85);
const MANIFEST = loadManifestForExecution(MANIFEST_PATH);
const RAW_RESULTS_FILE = path.join(OUTPUT_DIR, 'results.raw.json');
const LEGACY_DEEP_JOB_ACTION_FUNCTION_IDS = [
  'equipment_deep_action',
  'tool_job_refresh_action',
  'tool_job_cancel_action',
] as const;
const NATIVE_TOOL_FUNCTION_NAME_BY_ID: Record<string, string> = {
  equipment_deep_tool: 'analyze_equipment_deep',
  document_deep_tool: 'analyze_document_deep',
  compare_documents_deep_tool: 'compare_documents_deep',
};
const NATIVE_BACKEND_PROCESS_MARKERS: Record<string, string> = {
  'agent-api': 'python agent_api.py',
  'document-server': 'uvicorn mcp_document_server:app',
  'legal-server': 'uvicorn mcp_legal_server:app',
  ums: 'python services/model_manager/unified_model_server.py',
};

let authToken = '';
const collectedResults: RawRunResult[] = [];

test.describe.configure({ mode: 'serial' });

test.beforeAll(async ({ request }) => {
  fs.mkdirSync(OUTPUT_DIR, { recursive: true });
  await runOpenWebUIRuntimePreflight(request);
  if (AUTO_SYNC_ENABLED) {
    runOpenWebUIBootstrapSync();
  }
  authToken = await signIn(request);
  await assertLegacyDeepJobActionFunctionsRemoved(request, authToken);
});

test.afterAll(async () => {
  writeJson(RAW_RESULTS_FILE, collectedResults);
});

test('openwebui native deep-job equipment confirmed launch', async ({ page, request }) => {
  test.slow();
  test.setTimeout(180_000);

  const promptMarker = `NATIVE_DEEP_EQUIPMENT_${crypto.randomUUID()}`;
  const prompt = [
    'Сделай глубокий анализ оборудования: сервер для виртуализации, 2x CPU, 128 ГБ RAM, NVMe.',
    'Кратко укажи назначение и риски.',
    `[${promptMarker}]`,
  ].join(' ');
  const result: RawRunResult = {
    kind: 'native',
    scenario_id: 'native-equipment-deep-confirmed-launch',
    pair_key: 'native-equipment-deep',
    prompt_variant: 'confirmed-launch',
    document_name: null,
    prompt,
    expected_mode: 'native',
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
    native_request_summary: null,
    native_tool_name: null,
    native_route_patched: false,
    launch_classification: null,
    persisted_function_call_output: null,
    persisted_output_summary: null,
    ui: {},
    final_chat: null,
    error: null,
  };
  const outputPath = path.join(OUTPUT_DIR, 'native-equipment-deep-confirmed-launch.raw.json');
  let cleanupRoute: (() => Promise<void>) | null = null;

  try {
    const createdChat = await createChat(request, authToken, 'Codex Native Equipment Deep Job');
    const chatId = String(createdChat.id);
    result.chat_id = chatId;

    await openChatPage(page, chatId);
    await livePause(page);

    const nativeRoute = await installNativeToolRouteInjection(page, promptMarker, ['equipment_deep_tool']);
    cleanupRoute = nativeRoute.cleanup;

    await sendPromptThroughComposer(page, prompt);
    await livePause(page, LIVE_PAUSE_MS * 2);

    const capturedRequest = await waitForNativeRouteCapture(nativeRoute.captured, promptMarker, CONTROL_TIMEOUT_MS);
    result.native_route_patched = Boolean(capturedRequest?.mutated);
    result.native_request_summary = summarizeNativeRequestCapture(capturedRequest);
    result.action_response_ok = Boolean(capturedRequest?.mutated);
    const capturedPayload =
      capturedRequest?.payload && typeof capturedRequest.payload === 'object'
        ? (capturedRequest.payload as Record<string, unknown>)
        : null;
    const effectiveChatId = asOptionalString(capturedPayload?.chat_id) || chatId;
    result.chat_id = effectiveChatId;

    const launchState = await waitForNativeDeepLaunchState(request, authToken, effectiveChatId, CONTROL_TIMEOUT_MS);
    result.assistant_message_id = asOptionalString(launchState.message?.id);
    result.job_id = launchState.job_id;
    result.status_url = launchState.status_url;
    result.native_tool_name = launchState.tool_name;
    result.launch_classification = launchState.launch_classification;
    result.persisted_function_call_output = launchState.function_call_output;
    result.persisted_output_summary = summarizePlainObject(launchState.message?.output);
    result.final_chat = summarizeChatState(launchState.chatPayload, asOptionalString(launchState.message?.id) || '');

    if (launchState.launch_classification === 'confirmed_launch' && launchState.status_url) {
      result.backend_terminal = await waitForToolJobTerminal(
        request,
        launchState.status_url,
        CONTROL_TIMEOUT_MS,
        MATRIX_POLL_INTERVAL_MS
      );
    }

    expect.soft(result.native_route_patched).toBeTruthy();
    expect.soft(
      Array.isArray(result.native_request_summary?.tool_refs) ? result.native_request_summary.tool_refs : []
    ).toContain('equipment_deep_tool');
    expect(result.launch_classification).toBe('confirmed_launch');
    expect(asOptionalString(result.job_id)).toBeTruthy();
    expect(asOptionalString(result.status_url)).toBeTruthy();
    expect(normalizeWhitespace(result.persisted_function_call_output)).toContain('job_id:');
    expect(normalizeWhitespace(result.persisted_function_call_output)).toContain('status_url:');
  } catch (error) {
    result.error = formatError(error);
    throw error;
  } finally {
    if (cleanupRoute) {
      await cleanupRoute();
    }
    collectedResults.push(result);
    writeJson(outputPath, result);
  }
});

test('openwebui native deep-job auto materializes terminal result and survives reload', async ({ page, request }) => {
  test.slow();
  test.setTimeout(180_000);

  const promptMarker = `NATIVE_DEEP_RESULT_${crypto.randomUUID()}`;
  const prompt = [
    'Сделай глубокий анализ оборудования: отказоустойчивый сервер под виртуализацию и резервное питание.',
    'Нужен краткий итог и основные риски.',
    `[${promptMarker}]`,
  ].join(' ');
  const result: RawRunResult = {
    kind: 'native',
    scenario_id: 'native-deep-auto-materialize-reload',
    pair_key: 'native-deep-auto',
    prompt_variant: 'materialize-reload',
    document_name: null,
    prompt,
    expected_mode: 'native',
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
    native_request_summary: null,
    native_tool_name: null,
    native_route_patched: false,
    launch_classification: null,
    persisted_function_call_output: null,
    persisted_output_summary: null,
    ui: {},
    final_chat: null,
    error: null,
  };
  const outputPath = path.join(OUTPUT_DIR, 'native-deep-auto-materialize-reload.raw.json');
  let cleanupRoute: (() => Promise<void>) | null = null;

  try {
    const helperEnabled = await testHelperRouteEnabled(request);
    test.skip(!helperEnabled, 'LLM_TOOLS_PLATFORM_TEST_MODE=1 обязателен для детерминированного native deep-job кейса.');

    const createdChat = await createChat(request, authToken, 'Codex Native Deep Job Materialization');
    const chatId = String(createdChat.id);
    result.chat_id = chatId;

    await openChatPage(page, chatId);
    await livePause(page);

    const nativeRoute = await installNativeToolRouteInjection(page, promptMarker, ['equipment_deep_tool']);
    cleanupRoute = nativeRoute.cleanup;

    await sendPromptThroughComposer(page, prompt);
    await livePause(page, LIVE_PAUSE_MS * 2);

    const capturedRequest = await waitForNativeRouteCapture(nativeRoute.captured, promptMarker, CONTROL_TIMEOUT_MS);
    result.native_route_patched = Boolean(capturedRequest?.mutated);
    result.native_request_summary = summarizeNativeRequestCapture(capturedRequest);
    result.action_response_ok = Boolean(capturedRequest?.mutated);
    const capturedPayload =
      capturedRequest?.payload && typeof capturedRequest.payload === 'object'
        ? (capturedRequest.payload as Record<string, unknown>)
        : null;
    const effectiveChatId = asOptionalString(capturedPayload?.chat_id) || chatId;
    result.chat_id = effectiveChatId;

    const launchState = await waitForNativeDeepLaunchState(
      request,
      authToken,
      effectiveChatId,
      CONTROL_TIMEOUT_MS
    );
    result.assistant_message_id = asOptionalString(launchState.message?.id);
    result.job_id = launchState.job_id;
    result.status_url = launchState.status_url;
    result.native_tool_name = launchState.tool_name;
    result.launch_classification = launchState.launch_classification;
    result.persisted_function_call_output = launchState.function_call_output;
    result.persisted_output_summary = summarizePlainObject(launchState.message?.output);

    await expect(page.getByText('Deep job', { exact: false }).first()).toBeVisible({ timeout: CONTROL_TIMEOUT_MS });
    await expect(page.getByRole('button', { name: 'Обновить deep-job' })).toHaveCount(0);
    result.ui.panel_visible_before_terminal = true;

    const debugResponse = {
      assistant_message: 'Synthetic completed result for native deep-job materialization.',
      structured_result: {
        summary: 'Synthetic structured payload for native panel path.',
        source_count: 1,
      },
      sources: [{ name: 'synthetic-native-source.txt' }],
      artifacts: [{ type: 'markdown', label: 'Synthetic Native Artifact' }],
      output: [{ type: 'note', text: 'Synthetic native output item' }],
    };
    await debugTransitionToolJob(request, result.job_id || '', {
      status: 'completed',
      response: debugResponse,
    });

    const materialized = await waitForNativeResultMessageMaterialized(
      request,
      authToken,
      effectiveChatId,
      result.assistant_message_id || '',
      CONTROL_TIMEOUT_MS
    );
    result.final_chat = summarizeChatState(materialized.chatPayload, result.assistant_message_id || '');

    const resultSnippet = excerpt(asOptionalString(materialized.resultMessage?.content), 120);
    if (!resultSnippet) {
      throw new Error('Нативный deep-job не создал итоговое assistant-message.');
    }

    await expect(page.getByText(resultSnippet, { exact: false }).first()).toBeVisible({ timeout: CONTROL_TIMEOUT_MS });
    result.ui.result_visible_after_terminal = true;

    await page.reload({ waitUntil: 'domcontentloaded' });
    await livePause(page, LIVE_PAUSE_MS * 2);
    await expect(page.getByText(resultSnippet, { exact: false }).first()).toBeVisible({ timeout: CONTROL_TIMEOUT_MS });
    result.ui.result_visible_after_reload = true;

    const acceptedOutput = getDeepJobOutputItem(materialized.acceptedMessage);
    expect.soft(asOptionalString(acceptedOutput?.state)).toBe('completed');
    expect.soft(asOptionalString(materialized.acceptedMessage?.result_message_id)).toBe(
      asOptionalString(materialized.resultMessage?.id)
    );
    expect.soft(asOptionalString(materialized.resultMessage?.tool_job_result_for)).toBe(
      result.assistant_message_id
    );
  } catch (error) {
    result.error = formatError(error);
    throw error;
  } finally {
    if (cleanupRoute) {
      await cleanupRoute().catch(() => {});
    }
    collectedResults.push(result);
    writeJson(outputPath, result);
  }
});

test('openwebui native deep-job main stop cancels active job', async ({ page, request }) => {
  test.slow();
  test.setTimeout(180_000);

  const promptMarker = `NATIVE_DEEP_CANCEL_${crypto.randomUUID()}`;
  const prompt = [
    'Сделай глубокий анализ оборудования: резервирование питания, охлаждение и риски отказа.',
    'После запуска задача должна быть остановлена через основной Stop.',
    `[${promptMarker}]`,
  ].join(' ');
  const result: RawRunResult = {
    kind: 'native',
    scenario_id: 'native-deep-main-stop-cancel',
    pair_key: 'native-deep-stop',
    prompt_variant: 'cancel',
    document_name: null,
    prompt,
    expected_mode: 'native',
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
    native_request_summary: null,
    native_tool_name: null,
    native_route_patched: false,
    launch_classification: null,
    persisted_function_call_output: null,
    persisted_output_summary: null,
    ui: {},
    final_chat: null,
    error: null,
  };
  const outputPath = path.join(OUTPUT_DIR, 'native-deep-main-stop-cancel.raw.json');
  let cleanupRoute: (() => Promise<void>) | null = null;

  try {
    const helperEnabled = await testHelperRouteEnabled(request);
    test.skip(!helperEnabled, 'LLM_TOOLS_PLATFORM_TEST_MODE=1 обязателен для детерминированного native cancel кейса.');

    const createdChat = await createChat(request, authToken, 'Codex Native Deep Job Cancel');
    const chatId = String(createdChat.id);
    result.chat_id = chatId;

    await openChatPage(page, chatId);
    await livePause(page);

    const nativeRoute = await installNativeToolRouteInjection(page, promptMarker, ['equipment_deep_tool']);
    cleanupRoute = nativeRoute.cleanup;

    await sendPromptThroughComposer(page, prompt);
    await livePause(page, LIVE_PAUSE_MS * 2);

    const capturedRequest = await waitForNativeRouteCapture(nativeRoute.captured, promptMarker, CONTROL_TIMEOUT_MS);
    result.native_route_patched = Boolean(capturedRequest?.mutated);
    result.native_request_summary = summarizeNativeRequestCapture(capturedRequest);
    result.action_response_ok = Boolean(capturedRequest?.mutated);
    const capturedPayload =
      capturedRequest?.payload && typeof capturedRequest.payload === 'object'
        ? (capturedRequest.payload as Record<string, unknown>)
        : null;
    const effectiveChatId = asOptionalString(capturedPayload?.chat_id) || chatId;
    result.chat_id = effectiveChatId;

    const launchState = await waitForNativeDeepLaunchState(
      request,
      authToken,
      effectiveChatId,
      CONTROL_TIMEOUT_MS
    );
    result.assistant_message_id = asOptionalString(launchState.message?.id);
    result.job_id = launchState.job_id;
    result.status_url = launchState.status_url;
    result.native_tool_name = launchState.tool_name;
    result.launch_classification = launchState.launch_classification;

    await expect(page.getByText('Deep job', { exact: false }).first()).toBeVisible({ timeout: CONTROL_TIMEOUT_MS });

    const stopButton = page.locator('button[aria-label="Stop"]').first();
    await expect(stopButton).toBeVisible({ timeout: CONTROL_TIMEOUT_MS });
    const cancelResponsePromise = page.waitForResponse((response) => {
      return (
        response.request().method() === 'POST' &&
        response.url().includes(`/api/v1/deep-jobs/${result.job_id}/cancel`)
      );
    });
    await stopButton.click();
    const cancelResponse = await cancelResponsePromise;
    result.refresh_response_http_status = cancelResponse.status();

    await debugTransitionToolJob(request, result.job_id || '', {
      status: 'cancelled',
      error_summary: 'Synthetic cancelled result for native stop flow.',
    });

    const cancelled = await waitForNativeDeepJobCancelled(
      request,
      authToken,
      effectiveChatId,
      result.assistant_message_id || '',
      CONTROL_TIMEOUT_MS
    );
    result.final_chat = summarizeChatState(cancelled.chatPayload, result.assistant_message_id || '');

    await expect(page.getByText('Cancelled', { exact: false }).first()).toBeVisible({ timeout: CONTROL_TIMEOUT_MS });
    result.ui.cancelled_visible_after_stop = true;

    await page.reload({ waitUntil: 'domcontentloaded' });
    await livePause(page, LIVE_PAUSE_MS * 2);
    await expect(page.getByText('Cancelled', { exact: false }).first()).toBeVisible({ timeout: CONTROL_TIMEOUT_MS });
    result.ui.cancelled_visible_after_reload = true;

    expect.soft(asOptionalString(cancelled.deepJobOutput?.state)).toBe('cancelled');
    expect.soft(asOptionalString(cancelled.acceptedMessage?.result_message_id)).toBeNull();
  } catch (error) {
    result.error = formatError(error);
    throw error;
  } finally {
    if (cleanupRoute) {
      await cleanupRoute().catch(() => {});
    }
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
    if (!process.env.OPENWEBUI_DEEP_JOB_MANIFEST?.trim()) {
      return [];
    }
    throw new Error(`Missing Open WebUI deep-job manifest: ${manifestPath}`);
  }
  const payload = JSON.parse(fs.readFileSync(manifestPath, 'utf-8')) as { scenarios?: ScenarioManifest[] };
  if (!Array.isArray(payload.scenarios) || payload.scenarios.length === 0) {
    if (!process.env.OPENWEBUI_DEEP_JOB_MANIFEST?.trim()) {
      return [];
    }
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

async function runOpenWebUIRuntimePreflight(request: APIRequestContext): Promise<void> {
  const endpointTargets = [
    { name: 'openwebui', url: `${OPENWEBUI_BASE_URL}/` },
    { name: 'agent-api', url: `${BACKEND_BASE_URL}/health` },
    { name: 'tool-server-openapi', url: `${BACKEND_BASE_URL}/tool-server/openapi.json` },
    { name: 'document-server', url: `${DOCUMENT_SERVER_BASE_URL}/health` },
    { name: 'legal-server', url: `${LEGAL_SERVER_BASE_URL}/health` },
    { name: 'ums', url: `${UMS_BASE_URL}/health` },
  ];
  const endpointChecks: Array<Record<string, unknown>> = [];
  const failures: string[] = [];

  for (const target of endpointTargets) {
    const response = await request.fetch(target.url, { method: 'GET' });
    endpointChecks.push({
      name: target.name,
      url: target.url,
      ok: response.ok(),
      status: response.status(),
    });
    if (!response.ok()) {
      failures.push(`${target.name}:${response.status()}`);
    }
  }

  const processResult = spawnSync('ps', ['-eo', 'pid,cmd'], {
    cwd: REPO_ROOT,
    encoding: 'utf-8',
    env: process.env,
  });
  const processOutput = `${processResult.stdout || ''}${processResult.stderr || ''}`;
  const present = Object.entries(NATIVE_BACKEND_PROCESS_MARKERS)
    .filter(([, marker]) => processOutput.includes(marker))
    .map(([name]) => name);
  const missing = Object.keys(NATIVE_BACKEND_PROCESS_MARKERS).filter((name) => !present.includes(name));
  if (processResult.status !== 0 || missing.length > 0) {
    failures.push(`native-processes:${missing.join(',') || processResult.status}`);
  }

  const containerProbeScript = [
    'import json, urllib.request',
    `targets = ${JSON.stringify({
      'agent-api': replaceHost(`${BACKEND_BASE_URL}/health`, 'host.docker.internal'),
      ums: replaceHost(`${UMS_BASE_URL}/health`, 'host.docker.internal'),
    })}`,
    'results = {}',
    'for name, url in targets.items():',
    '    try:',
    "        with urllib.request.urlopen(url, timeout=5) as response:",
    "            results[name] = {'ok': True, 'status': getattr(response, 'status', 200), 'url': url}",
    '    except Exception as exc:',
    "        results[name] = {'ok': False, 'error': str(exc), 'url': url}",
    'print(json.dumps(results, ensure_ascii=False))',
  ].join('\n');
  const containerResult = spawnSync(
    'docker',
    ['compose', '--profile', 'legacy', 'exec', '-T', 'open-webui', 'python', '-c', containerProbeScript],
    {
      cwd: REPO_ROOT,
      encoding: 'utf-8',
      env: process.env,
    }
  );
  let containerChecks: Record<string, unknown> = {};
  try {
    containerChecks = JSON.parse((containerResult.stdout || '').trim() || '{}') as Record<string, unknown>;
  } catch {
    containerChecks = {
      output: `${containerResult.stdout || ''}${containerResult.stderr || ''}`.trim(),
    };
  }
  const containerOk =
    containerResult.status === 0 &&
    Object.values(containerChecks).every((value) => typeof value === 'object' && value !== null && Boolean((value as { ok?: boolean }).ok));
  if (!containerOk) {
    failures.push('openwebui-container-probe');
  }

  writeJson(path.join(OUTPUT_DIR, 'runtime-preflight.playwright.json'), {
    status: failures.length === 0 ? 'ok' : 'failed',
    endpoints: endpointChecks,
    native_backend_processes: {
      status: missing.length === 0 && processResult.status === 0 ? 'ok' : 'failed',
      present,
      missing,
    },
    openwebui_container_probe: {
      status: containerOk ? 'ok' : 'failed',
      checks: containerChecks,
    },
  });

  if (failures.length > 0) {
    throw new Error(`Mixed runtime preflight failed: ${failures.join('; ')}`);
  }
}

function runOpenWebUIBootstrapSync(): void {
  const args = [
    BOOTSTRAP_SCRIPT,
    '--backend-base-url',
    BACKEND_BASE_URL,
    '--openwebui-base-url',
    OPENWEBUI_BASE_URL,
    '--env-file',
    BACKEND_ENV_PATH,
  ];
  if (LEGACY_DEEP_JOB_ACTION_TESTS_ENABLED) {
    args.push('--include-legacy-deep-job-actions');
  }
  const result = spawnSync(
    'python',
    args,
    {
      cwd: REPO_ROOT,
      encoding: 'utf-8',
      env: process.env,
    }
  );
  const output = `${result.stdout || ''}${result.stderr || ''}`;
  fs.writeFileSync(path.join(OUTPUT_DIR, 'openwebui.bootstrap.playwright.log'), output, 'utf-8');
  if ((result.stdout || '').trim()) {
    try {
      const payload = JSON.parse(result.stdout) as Record<string, unknown>;
      writeJson(path.join(OUTPUT_DIR, 'openwebui.bootstrap.playwright.json'), payload);
    } catch {
      // Bootstrap already wrote a textual log; JSON is best-effort only.
    }
  }
  if (result.status !== 0) {
    throw new Error(`Open WebUI bootstrap sync failed with exit ${String(result.status)}.`);
  }
}

async function assertInstalledActionFunctionsSynced(request: APIRequestContext, token: string): Promise<void> {
  const exportUrl =
    `${BACKEND_BASE_URL}/operator/tool-bindings/export/openwebui?backend_base_url=` +
    encodeURIComponent(BACKEND_BASE_URL);
  const exportResponse = await request.fetch(exportUrl, { method: 'GET' });
  expect(exportResponse.ok()).toBeTruthy();
  const exportPayload = (await exportResponse.json()) as { actionFunctions?: Array<Record<string, unknown>> };
  const exportedActions: Record<string, string> = {};
  for (const item of exportPayload.actionFunctions || []) {
    const functionId = asOptionalString(item.action_id) || '';
    if (!functionId) {
      continue;
    }
    exportedActions[functionId] = asOptionalString(item.pythonCode) || '';
  }

  const installedActions: Record<string, string> = {};
  for (const functionId of Object.keys(LEGACY_ACTION_FUNCTION_SYNC_RULES)) {
    const response = await apiJson(request, token, 'GET', `${OPENWEBUI_BASE_URL}/api/v1/functions/id/${functionId}`);
    expect(response.ok).toBeTruthy();
    installedActions[functionId] = asOptionalString(response.payload?.content) || '';
  }

  const syncReport = evaluateActionFunctionSync(exportedActions, installedActions);
  writeJson(path.join(OUTPUT_DIR, 'action-function-sync.playwright.json'), syncReport);
  if (syncReport.status !== 'ok') {
    throw new Error(`Open WebUI action function drift detected: ${syncReport.drift_function_ids.join(', ')}`);
  }
}

async function assertLegacyDeepJobActionFunctionsRemoved(
  request: APIRequestContext,
  token: string
): Promise<void> {
  const removalReport: Record<string, number> = {};
  for (const functionId of LEGACY_DEEP_JOB_ACTION_FUNCTION_IDS) {
    const response = await apiJson(
      request,
      token,
      'GET',
      `${OPENWEBUI_BASE_URL}/api/v1/functions/id/${functionId}`
    );
    removalReport[functionId] = response.status;
    expect(response.ok).toBeFalsy();
    expect([401, 404]).toContain(response.status);
  }
  writeJson(path.join(OUTPUT_DIR, 'legacy-deep-job-actions.removal.json'), removalReport);
}

function evaluateActionFunctionSync(
  exportedActions: Record<string, string>,
  installedActions: Record<string, string>
): { status: 'ok' | 'drift'; drift_function_ids: string[]; missing_fragments: Record<string, string[]> } {
  const driftFunctionIds: string[] = [];
  const missingFragments: Record<string, string[]> = {};
  for (const [functionId, requiredFragments] of Object.entries(LEGACY_ACTION_FUNCTION_SYNC_RULES)) {
    const exportedContent = exportedActions[functionId] || '';
    const installedContent = installedActions[functionId] || '';
    const missing = requiredFragments.filter((fragment) => exportedContent.includes(fragment) && !installedContent.includes(fragment));
    if (missing.length > 0) {
      driftFunctionIds.push(functionId);
      missingFragments[functionId] = missing;
    }
  }
  return {
    status: driftFunctionIds.length === 0 ? 'ok' : 'drift',
    drift_function_ids: driftFunctionIds,
    missing_fragments: missingFragments,
  };
}

function replaceHost(url: string, host: string): string {
  const parsed = new URL(url);
  parsed.hostname = host;
  return parsed.toString();
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
  await dismissOpenWebUIReleaseNotes(page);
  await ensureOpenWebUIModelSelected(page);
}

async function dismissOpenWebUIReleaseNotes(page: Page): Promise<void> {
  const dialog = page
    .getByRole('dialog')
    .filter({ has: page.getByRole('heading', { name: /What's New in Open WebUI/iu }) })
    .first();

  let visible = false;
  try {
    visible = await dialog.isVisible();
  } catch {
    visible = false;
  }
  if (!visible) {
    return;
  }

  const closeButton = dialog.getByRole('button', { name: /^Close$/u }).first();
  await closeButton.click();
  await expect(dialog).toBeHidden({ timeout: CONTROL_TIMEOUT_MS });
}

async function ensureOpenWebUIModelSelected(page: Page): Promise<void> {
  const candidateModels = Array.from(new Set([OPENWEBUI_MODEL, 'llm-tools-platform'].filter(Boolean)));
  for (const candidate of candidateModels) {
    const selectedModelButton = page
      .getByRole('button', {
        name: new RegExp(`Selected model: ${escapeRegExp(candidate)}`, 'u'),
      })
      .first();
    try {
      if (await selectedModelButton.isVisible()) {
        return;
      }
    } catch {
      // Ignore detached state while the header is still hydrating.
    }
  }

  const selectModelButton = page
    .getByRole('button', {
      name: /^(Выберите модель|Select a model)$/u,
    })
    .first();
  let selectorVisible = false;
  try {
    selectorVisible = await selectModelButton.isVisible();
  } catch {
    selectorVisible = false;
  }
  if (!selectorVisible) {
    return;
  }

  await selectModelButton.click();
  let selected = false;
  for (const candidate of candidateModels) {
    const option = page
      .getByRole('option', {
        name: new RegExp(`Select ${escapeRegExp(candidate)} model`, 'u'),
      })
      .first();
    try {
      if (await option.isVisible()) {
        await option.click();
        const selectedModelButton = page
          .getByRole('button', {
            name: new RegExp(`Selected model: ${escapeRegExp(candidate)}`, 'u'),
          })
          .first();
        await expect(selectedModelButton).toBeVisible({ timeout: CONTROL_TIMEOUT_MS });
        selected = true;
        break;
      }
    } catch {
      // Try the next candidate.
    }
  }

  if (selected) {
    return;
  }

  const firstOption = page.getByRole('option').first();
  await expect(firstOption).toBeVisible({ timeout: CONTROL_TIMEOUT_MS });
  await firstOption.click();
  await expect(selectModelButton).toHaveCount(0, { timeout: CONTROL_TIMEOUT_MS });
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

async function waitForNativeResultMessageMaterialized(
  request: APIRequestContext,
  token: string,
  chatId: string,
  acceptedMessageId: string,
  timeoutMs: number
): Promise<{
  chatPayload: Record<string, any>;
  acceptedMessage: Record<string, any>;
  resultMessage: Record<string, any>;
  deepJobOutput: Record<string, any>;
}> {
  const deadline = Date.now() + timeoutMs;

  while (Date.now() < deadline) {
    const chatPayload = await getChat(request, token, chatId);
    const acceptedMessage = getChatMessage(chatPayload, acceptedMessageId);
    const deepJobOutput = getDeepJobOutputItem(acceptedMessage);
    const resultMessageId =
      asOptionalString(acceptedMessage?.result_message_id) ||
      asOptionalString(deepJobOutput?.result_message_id);
    const resultMessage = getChatMessage(chatPayload, resultMessageId);
    if (acceptedMessage && deepJobOutput && resultMessage && asOptionalString(deepJobOutput.state) === 'completed') {
      return {
        chatPayload,
        acceptedMessage,
        resultMessage,
        deepJobOutput,
      };
    }
    await new Promise((resolve) => setTimeout(resolve, 500));
  }

  throw new Error(`Timed out waiting for native deep-job materialization for chat_id=${chatId}`);
}

async function waitForNativeDeepJobCancelled(
  request: APIRequestContext,
  token: string,
  chatId: string,
  acceptedMessageId: string,
  timeoutMs: number
): Promise<{
  chatPayload: Record<string, any>;
  acceptedMessage: Record<string, any>;
  deepJobOutput: Record<string, any>;
}> {
  const deadline = Date.now() + timeoutMs;

  while (Date.now() < deadline) {
    const chatPayload = await getChat(request, token, chatId);
    const acceptedMessage = getChatMessage(chatPayload, acceptedMessageId);
    const deepJobOutput = getDeepJobOutputItem(acceptedMessage);
    if (acceptedMessage && deepJobOutput && asOptionalString(deepJobOutput.state) === 'cancelled') {
      return {
        chatPayload,
        acceptedMessage,
        deepJobOutput,
      };
    }
    await new Promise((resolve) => setTimeout(resolve, 500));
  }

  throw new Error(`Timed out waiting for native deep-job cancellation for chat_id=${chatId}`);
}

async function installNativeToolRouteInjection(
  page: Page,
  promptMarker: string,
  toolIds: string[]
): Promise<{
  captured: Array<Record<string, unknown>>;
  cleanup: () => Promise<void>;
}> {
  const captured: Array<Record<string, unknown>> = [];

  const handler = async (route: any, request: any) => {
    if (request.method() !== 'POST' || !request.url().includes('/api/chat/completions')) {
      await route.continue();
      return;
    }

    const rawBody = request.postData() || '';
    let parsedBody: Record<string, unknown> | null = null;
    try {
      parsedBody = JSON.parse(rawBody) as Record<string, unknown>;
    } catch {
      parsedBody = null;
    }

    let outgoingPayload = parsedBody;
    let mutated = false;
    if (parsedBody && rawBody.includes(promptMarker)) {
      outgoingPayload = mergeNativeToolIds(parsedBody, toolIds);
      mutated = true;
    }

    captured.push({
      url: request.url(),
      method: request.method(),
      marker: promptMarker,
      mutated,
      raw_body_excerpt: excerpt(rawBody, 240),
      tool_refs: extractToolRefs(outgoingPayload),
      payload: summarizePlainObject(outgoingPayload),
    });

    if (!mutated || !outgoingPayload) {
      await route.continue();
      return;
    }

    await route.continue({
      headers: {
        ...request.headers(),
        'content-type': 'application/json',
      },
      postData: JSON.stringify(outgoingPayload),
    });
  };

  await page.route('**/api/chat/completions', handler);
  return {
    captured,
    cleanup: async () => {
      await page.unroute('**/api/chat/completions', handler);
    },
  };
}

async function waitForNativeRouteCapture(
  captured: Array<Record<string, unknown>>,
  promptMarker: string,
  timeoutMs: number
): Promise<Record<string, unknown> | null> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const found = captured.find((item) => {
      return (
        normalizeWhitespace(asOptionalString(item.marker)) === normalizeWhitespace(promptMarker) &&
        Boolean(item.mutated)
      );
    });
    if (found) {
      return found;
    }
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  return null;
}

async function waitForNativeDeepLaunchState(
  request: APIRequestContext,
  token: string,
  chatId: string,
  timeoutMs: number
): Promise<{
  chatPayload: Record<string, any>;
  message: Record<string, any>;
  tool_name: string | null;
  function_call_output: string | null;
  job_id: string | null;
  status_url: string | null;
  launch_classification: 'confirmed_launch' | 'blocked_unconfirmed_launch';
}> {
  const deadline = Date.now() + timeoutMs;

  while (Date.now() < deadline) {
    const chatPayload = await getChat(request, token, chatId);
    const nativeMessage = findLatestNativeDeepAssistantMessage(chatPayload);
    if (nativeMessage) {
      return {
        chatPayload,
        ...nativeMessage,
      };
    }
    await new Promise((resolve) => setTimeout(resolve, 500));
  }

  throw new Error(`Timed out waiting for native deep-job launch state for chat_id=${chatId}`);
}

async function sendPromptThroughComposer(page: Page, prompt: string): Promise<void> {
  await dismissOpenWebUIReleaseNotes(page);
  await ensureOpenWebUIModelSelected(page);
  const composer = await locateVisibleComposer(page);
  if (!composer) {
    throw new Error('Не удалось найти видимый composer в Open WebUI.');
  }
  await composer.click();
  try {
    await composer.evaluate((element, value) => {
      const target = element as HTMLDivElement | HTMLTextAreaElement;
      target.focus();
      if ('value' in target) {
        target.value = value;
      } else {
        const paragraph = document.createElement('p');
        paragraph.textContent = value;
        target.replaceChildren(paragraph);
      }
      target.dispatchEvent(
        new InputEvent('input', {
          bubbles: true,
          cancelable: true,
          inputType: 'insertText',
          data: value,
        })
      );
    }, prompt);
    await composer.press('Enter');
  } catch {
    await composer.click();
    await page.keyboard.type(prompt, { delay: LIVE_MODE ? LIVE_TYPING_DELAY_MS : 20 });
    await page.keyboard.press('Enter');
  }
}

function getChatMessage(chatPayload: Record<string, any>, messageId: string | null): Record<string, any> | null {
  if (!messageId) {
    return null;
  }
  return (chatPayload.chat?.history?.messages || {})[messageId] || null;
}

function getDeepJobOutputItem(message: Record<string, any> | null): Record<string, any> | null {
  const output = Array.isArray(message?.output) ? (message.output as Array<Record<string, unknown>>) : [];
  return (
    output.find((item) => {
      return asOptionalString(item.type) === 'open_webui:deep_job';
    }) as Record<string, any> | undefined
  ) || null;
}

function listChatMessages(chatPayload: Record<string, any>): Record<string, unknown>[] {
  const messages = Object.values(chatPayload.chat?.history?.messages || {}) as Record<string, unknown>[];
  return messages.sort((left, right) => {
    const leftTs = Number((left as Record<string, unknown>).timestamp || 0);
    const rightTs = Number((right as Record<string, unknown>).timestamp || 0);
    return leftTs - rightTs;
  });
}

function findLatestNativeDeepAssistantMessage(chatPayload: Record<string, any>): {
  message: Record<string, any>;
  tool_name: string | null;
  function_call_output: string | null;
  job_id: string | null;
  status_url: string | null;
  launch_classification: 'confirmed_launch' | 'blocked_unconfirmed_launch';
} | null {
  const messages = listChatMessages(chatPayload);
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index] as Record<string, any>;
    if (asOptionalString(message.role) !== 'assistant') {
      continue;
    }
    const nativeState = extractNativeDeepStateFromMessage(message);
    if (!nativeState) {
      continue;
    }
    return {
      message,
      ...nativeState,
    };
  }
  return null;
}

function extractNativeDeepStateFromMessage(message: Record<string, any>): {
  tool_name: string | null;
  function_call_output: string | null;
  job_id: string | null;
  status_url: string | null;
  launch_classification: 'confirmed_launch' | 'blocked_unconfirmed_launch';
} | null {
  const outputItems = Array.isArray(message.output) ? (message.output as Array<Record<string, unknown>>) : [];
  const callLookup = new Map<string, string>();
  for (const item of outputItems) {
    if (asOptionalString(item.type) !== 'function_call') {
      continue;
    }
    const callId = asOptionalString(item.call_id);
    const toolName = asOptionalString(item.name);
    if (callId && toolName) {
      callLookup.set(callId, toolName);
    }
  }

  for (let index = outputItems.length - 1; index >= 0; index -= 1) {
    const item = outputItems[index];
    if (asOptionalString(item.type) !== 'function_call_output') {
      continue;
    }
    const callId = asOptionalString(item.call_id);
    const toolName = callId ? callLookup.get(callId) || null : null;
    if (!isTrackedNativeDeepToolName(toolName)) {
      continue;
    }
    const outputText = normalizeNativeOutputContent(item.output);
    if (!outputText) {
      return {
        tool_name: toolName,
        function_call_output: '',
        job_id: null,
        status_url: null,
        launch_classification: 'blocked_unconfirmed_launch',
      };
    }
    const launch = extractDeepLaunchTokens(outputText);
    if (launch.job_id && launch.status_url) {
      return {
        tool_name: toolName,
        function_call_output: outputText,
        job_id: launch.job_id,
        status_url: launch.status_url,
        launch_classification: 'confirmed_launch',
      };
    }
    return {
      tool_name: toolName,
      function_call_output: outputText,
      job_id: null,
      status_url: null,
      launch_classification: 'blocked_unconfirmed_launch',
    };
  }

  const messageContent = asOptionalString(message.content) || '';
  if (messageContent.includes('Не удалось запустить deep-job')) {
    return {
      tool_name: null,
      function_call_output: messageContent,
      job_id: null,
      status_url: null,
      launch_classification: 'blocked_unconfirmed_launch',
    };
  }
  const launch = extractDeepLaunchTokens(messageContent);
  if (launch.job_id && launch.status_url) {
    return {
      tool_name: null,
      function_call_output: messageContent,
      job_id: launch.job_id,
      status_url: launch.status_url,
      launch_classification: 'confirmed_launch',
    };
  }
  return null;
}

function normalizeNativeOutputContent(value: unknown): string {
  if (typeof value === 'string') {
    return value.trim();
  }
  if (Array.isArray(value)) {
    return value
      .map((item) => {
        if (!item || typeof item !== 'object') {
          return '';
        }
        return asOptionalString((item as Record<string, unknown>).text) || '';
      })
      .join('')
      .trim();
  }
  if (!value) {
    return '';
  }
  return String(value).trim();
}

function extractDeepLaunchTokens(text: string): { job_id: string | null; status_url: string | null } {
  const normalized = String(text || '').trim();
  const jobMatch = normalized.match(/job_id:\s*([^\s]+)/u);
  const statusMatch = normalized.match(/status_url:\s*([^\s]+)/u);
  return {
    job_id: jobMatch?.[1]?.trim() || null,
    status_url: statusMatch?.[1]?.trim() || null,
  };
}

function isTrackedNativeDeepToolName(value: string | null): boolean {
  return value === 'analyze_equipment_deep' || value === 'analyze_document_deep';
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

function mergeNativeToolIds(payload: Record<string, unknown>, toolIds: string[]): Record<string, unknown> {
  const nextPayload = { ...payload };
  const merged = Array.from(new Set([...extractToolIdsFromValue(payload.tool_ids), ...toolIds]));
  const requestedFunctionName = merged
    .map((toolId) => NATIVE_TOOL_FUNCTION_NAME_BY_ID[toolId] || toolId)
    .find((value) => Boolean(value));
  const existingParams =
    payload.params && typeof payload.params === 'object' && !Array.isArray(payload.params)
      ? (payload.params as Record<string, unknown>)
      : {};
  nextPayload.tool_ids = merged;
  nextPayload.selected_tool_ids = merged;
  nextPayload.params = {
    ...existingParams,
    function_calling: 'native',
  };
  if (requestedFunctionName) {
    nextPayload.tool_choice = {
      type: 'function',
      function: {
        name: requestedFunctionName,
      },
    };
  }
  return nextPayload;
}

function extractToolIdsFromValue(value: unknown): string[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value
    .map((item) => asOptionalString(item))
    .filter((item): item is string => Boolean(item));
}

function extractToolRefs(payload: Record<string, unknown> | null): string[] {
  const refs = new Set<string>();
  if (!payload || typeof payload !== 'object') {
    return [];
  }
  const keys = [
    'tool_ids',
    'selected_tool_ids',
    'toolids',
    'selectedtoolids',
    'selected_tools',
    'tool',
    'tool_id',
    'toolid',
    'requested_tool',
    'tool_choice',
    'tools',
  ];
  for (const key of keys) {
    if (!(key in payload)) {
      continue;
    }
    const value = (payload as Record<string, unknown>)[key];
    if (key === 'tools' && Array.isArray(value)) {
      for (const item of value) {
        addToolLike(item, refs);
      }
      continue;
    }
    addToolLike(value, refs);
  }
  return Array.from(refs);
}

function addToolLike(value: unknown, out: Set<string>): void {
  if (!value) {
    return;
  }
  if (Array.isArray(value)) {
    for (const item of value) {
      addToolLike(item, out);
    }
    return;
  }
  if (typeof value === 'string') {
    const trimmed = value.trim();
    if (trimmed) {
      out.add(trimmed);
    }
    return;
  }
  if (typeof value === 'object') {
    for (const key of ['id', 'tool_id', 'toolId', 'name', 'title', 'slug', 'value']) {
      if (key in (value as Record<string, unknown>)) {
        addToolLike((value as Record<string, unknown>)[key], out);
      }
    }
  }
}

function summarizeNativeRequestCapture(payload: Record<string, unknown> | null): Record<string, unknown> | null {
  if (!payload) {
    return null;
  }
  return {
    url: asOptionalString(payload.url),
    mutated: Boolean(payload.mutated),
    tool_refs: Array.isArray(payload.tool_refs) ? payload.tool_refs : [],
    raw_body_excerpt: asOptionalString(payload.raw_body_excerpt),
    payload: summarizePlainObject(payload.payload),
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
