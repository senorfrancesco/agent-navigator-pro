import { test, expect, APIRequestContext, Locator, Page } from '@playwright/test';
import crypto from 'node:crypto';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const REPO_ROOT = path.resolve(__dirname, '../../..');
const BACKEND_ROOT = path.join(REPO_ROOT, 'backend');
const BACKEND_ENV_PATH = path.join(REPO_ROOT, 'backend', '.env');
const ENV_VALUES = parseEnvFile(BACKEND_ENV_PATH);
const OPENWEBUI_BASE_URL = normalizeBaseUrl(
	process.env.OPENWEBUI_BASE_URL || process.env.BASE_URL || 'http://127.0.0.1:8081'
);
const UMS_BASE_URL = normalizeBaseUrl(
	process.env.OPENWEBUI_UMS_BASE_URL || ENV_VALUES.AGENT_API_UMS_URL || 'http://127.0.0.1:8090'
);
const ADMIN_EMAIL = process.env.WEBUI_ADMIN_EMAIL || ENV_VALUES.WEBUI_ADMIN_EMAIL || 'admin@example.com';
const ADMIN_PASSWORD =
	process.env.WEBUI_ADMIN_PASSWORD || ENV_VALUES.WEBUI_ADMIN_PASSWORD || 'change-me-now';
const GGUF_SOURCE_PATH =
	process.env.OPENWEBUI_RUNTIME_E2E_GGUF_PATH?.trim() || ENV_VALUES.MODEL_PATH_LLM || '';
const CONTROL_TIMEOUT_MS = parseInteger(process.env.OPENWEBUI_CONTROL_TIMEOUT_MS, 45_000);

test.describe.configure({ mode: 'serial' });

test('openwebui runtime model folder flow registers a gguf candidate and shows runtime badge in selector', async ({
	page,
	request
}) => {
	test.slow();
	test.setTimeout(120_000);
	test.skip(!GGUF_SOURCE_PATH, 'Нужен OPENWEBUI_RUNTIME_E2E_GGUF_PATH или MODEL_PATH_LLM в backend/.env.');

	const resolvedSourcePath = resolveConfiguredPath(GGUF_SOURCE_PATH);
	test.skip(!fs.existsSync(resolvedSourcePath), `Не найден GGUF для smoke-теста: ${resolvedSourcePath}`);

	const runId = crypto.randomUUID().slice(0, 8);
	const rootDir = path.join(os.tmpdir(), `openwebui-runtime-models-${runId}`);
	const candidateDirName = `candidate-${runId}`;
	const candidateDir = path.join(rootDir, candidateDirName);
	const candidateFileName = `Runtime-E2E-${runId}-GGUF.gguf`;
	const candidatePath = path.join(candidateDir, candidateFileName);
	const expectedModelId = slugifyCandidateId(candidateFileName.replace(/\.gguf$/iu, ''));
	let registeredModelId = expectedModelId;
	let authToken = '';

	fs.mkdirSync(candidateDir, { recursive: true });
	fs.symlinkSync(resolvedSourcePath, candidatePath);

	try {
		authToken = await signIn(request);
		await openOpenWebUIHome(page, authToken);

		const selectorTrigger = await waitForModelSelectorTrigger(page);
		const selectedModelBefore = normalizeWhitespace(await selectorTrigger.textContent());

		await openModelSelector(page);
		await page.getByRole('button', { name: 'Add model from folder' }).click();
		await expect(page.getByText('Browse folders, preview a model candidate, and add it to the shared list')).toBeVisible({
			timeout: CONTROL_TIMEOUT_MS
		});

		const addFolderResponsePromise = page.waitForResponse((response) => {
			return (
				response.request().method() === 'POST' &&
				response.url().includes('/api/v1/runtime-models/scan-folders')
			);
		});
		await page.getByPlaceholder('Add custom folder path').fill(rootDir);
		await page.getByRole('button', { name: 'Add' }).click();
		const addFolderResponse = await addFolderResponsePromise;
		expect(addFolderResponse.ok()).toBeTruthy();

		const savedRootButton = page.getByRole('button', { name: rootDir }).first();
		await expect(savedRootButton).toBeVisible({ timeout: CONTROL_TIMEOUT_MS });

		const candidateRow = page
			.locator('button')
			.filter({ has: page.getByText(candidateDirName, { exact: true }) })
			.filter({ has: page.getByText('Models', { exact: true }) })
			.first();
		await expect(candidateRow).toBeVisible({ timeout: CONTROL_TIMEOUT_MS });
		await expect(candidateRow.getByText('GGUF', { exact: true })).toBeVisible({ timeout: CONTROL_TIMEOUT_MS });

		await candidateRow.click();
		await expect(page.getByText(candidateDir, { exact: false })).toBeVisible({ timeout: CONTROL_TIMEOUT_MS });

		const useFolderResponsePromise = page.waitForResponse((response) => {
			return (
				response.request().method() === 'POST' &&
				response.url().includes('/api/v1/runtime-models/scan-folders')
			);
		});
		await page.getByRole('button', { name: 'Use this folder' }).click();
		const useFolderResponse = await useFolderResponsePromise;
		expect(useFolderResponse.ok()).toBeTruthy();
		await expect(page.getByRole('button', { name: candidateDirName }).first()).toBeVisible({
			timeout: CONTROL_TIMEOUT_MS
		});

		const previewResponsePromise = page.waitForResponse((response) => {
			return (
				response.request().method() === 'POST' &&
				response.url().includes('/api/v1/runtime-models/preview-path')
			);
		});
		await page.getByRole('button', { name: 'Preview current folder' }).click();
		const previewResponse = await previewResponsePromise;
		expect(previewResponse.ok()).toBeTruthy();
		const previewPayload = (await previewResponse.json()) as RuntimeModelPreviewPayload;
		const readyEntry = previewPayload.entries.find((entry) => entry.status === 'ready');
		expect(readyEntry).toBeTruthy();
		if (!readyEntry) {
			throw new Error('Preview не вернул ready candidate.');
		}
		registeredModelId = readyEntry.candidate_id;

		const previewCard = page
			.getByRole('button', { name: 'Register' })
			.first()
			.locator('xpath=ancestor::div[contains(@class,"rounded-2xl")]')
			.first();
		await expect(previewCard.getByText(readyEntry.display_name, { exact: false })).toBeVisible({
			timeout: CONTROL_TIMEOUT_MS
		});
		await expect(previewCard.getByText('GGUF', { exact: true })).toBeVisible({ timeout: CONTROL_TIMEOUT_MS });
		await expect(previewCard.getByText('Ready', { exact: true })).toBeVisible({ timeout: CONTROL_TIMEOUT_MS });

		const registerResponsePromise = page.waitForResponse((response) => {
			return (
				response.request().method() === 'POST' &&
				response.url().includes('/api/v1/runtime-models/register')
			);
		});
		await previewCard.getByRole('button', { name: 'Register' }).click();
		const registerResponse = await registerResponsePromise;
		expect(registerResponse.ok()).toBeTruthy();

		await expect(
			page.getByText('Browse folders, preview a model candidate, and add it to the shared list')
		).toBeHidden({ timeout: CONTROL_TIMEOUT_MS });

		const selectorTriggerAfterRegister = await waitForModelSelectorTrigger(page);
		const selectedModelAfter = normalizeWhitespace(await selectorTriggerAfterRegister.textContent());
		expect(selectedModelAfter).toBe(selectedModelBefore);

		await openModelSelector(page);
		const searchInput = page.getByLabel('Search In Models');
		await expect(searchInput).toBeVisible({ timeout: CONTROL_TIMEOUT_MS });
		await searchInput.fill(readyEntry.display_name);

		const selectorOption = page
			.getByRole('option', {
				name: new RegExp(`Select ${escapeRegExp(readyEntry.display_name)} model`, 'u')
			})
			.first();
		await expect(selectorOption).toBeVisible({ timeout: CONTROL_TIMEOUT_MS });
		await expect(selectorOption.getByText('GGUF', { exact: true })).toBeVisible({
			timeout: CONTROL_TIMEOUT_MS
		});
	} finally {
		if (authToken) {
			await bestEffortDeleteScanFolders(request, [rootDir, candidateDir]);
			await bestEffortUnregisterDynamicModel(request, registeredModelId);
		}
		fs.rmSync(rootDir, { recursive: true, force: true });
	}
});

type RuntimeModelPreviewEntry = {
	candidate_id: string;
	display_name: string;
	status: string;
};

type RuntimeModelPreviewPayload = {
	entries: RuntimeModelPreviewEntry[];
};

async function signIn(request: APIRequestContext): Promise<string> {
	const response = await request.post(`${OPENWEBUI_BASE_URL}/api/v1/auths/signin`, {
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json'
		},
		data: {
			email: ADMIN_EMAIL,
			password: ADMIN_PASSWORD
		}
	});
	expect(response.ok()).toBeTruthy();
	const payload = (await response.json()) as { token?: string };
	const token = String(payload.token || '').trim();
	expect(token).toBeTruthy();
	return token;
}

async function openOpenWebUIHome(page: Page, authToken: string): Promise<void> {
	await page.addInitScript((tokenValue: string) => {
		window.localStorage.setItem('token', tokenValue);
	}, authToken);
	await page.goto(`${OPENWEBUI_BASE_URL}/`, { waitUntil: 'domcontentloaded' });
	await dismissOpenWebUIReleaseNotes(page);
	await waitForModelSelectorTrigger(page);
}

async function dismissOpenWebUIReleaseNotes(page: Page): Promise<void> {
	const dialog = page
		.getByRole('dialog')
		.filter({ has: page.getByRole('heading', { name: /What's New in Open WebUI/iu }) })
		.first();

	try {
		if (!(await dialog.isVisible())) {
			return;
		}
	} catch {
		return;
	}

	await dialog.getByRole('button', { name: /^Close$/u }).first().click();
	await expect(dialog).toBeHidden({ timeout: CONTROL_TIMEOUT_MS });
}

async function waitForModelSelectorTrigger(page: Page): Promise<Locator> {
	const deadline = Date.now() + CONTROL_TIMEOUT_MS;
	while (Date.now() < deadline) {
		const candidates = [
			page.locator('button[aria-label^="Selected model:"]').first(),
			page.getByRole('button', { name: /^(Select a model|Выберите модель)$/u }).first()
		];

		for (const candidate of candidates) {
			try {
				if (await candidate.isVisible()) {
					return candidate;
				}
			} catch {
				// Ignore re-rendering while the app hydrates.
			}
		}

		await page.waitForTimeout(250);
	}

	throw new Error('Не удалось дождаться выбора модели в Open WebUI.');
}

async function openModelSelector(page: Page): Promise<void> {
	const trigger = await waitForModelSelectorTrigger(page);
	await trigger.click();
	await expect(page.getByLabel('Search In Models')).toBeVisible({ timeout: CONTROL_TIMEOUT_MS });
}

async function bestEffortDeleteScanFolders(
	request: APIRequestContext,
	pathsToDelete: string[]
): Promise<void> {
	try {
		const response = await request.get(`${UMS_BASE_URL}/models/scan-folders`, {
			headers: {
				Accept: 'application/json'
			}
		});
		if (!response.ok()) {
			return;
		}
		const payload = (await response.json()) as {
			folders?: Array<{ id?: string; path?: string }>;
		};
		const folders = Array.isArray(payload.folders) ? payload.folders : [];
		const paths = new Set(pathsToDelete.map((item) => path.resolve(item)));
		for (const folder of folders) {
			const folderPath = String(folder.path || '').trim();
			const folderId = String(folder.id || '').trim();
			if (!folderPath || !folderId || !paths.has(path.resolve(folderPath))) {
				continue;
			}
			await request.delete(`${UMS_BASE_URL}/models/scan-folders/${encodeURIComponent(folderId)}`, {
				headers: {
					Accept: 'application/json'
				}
			});
		}
	} catch {
		// Cleanup is best-effort.
	}
}

async function bestEffortUnregisterDynamicModel(
	request: APIRequestContext,
	modelId: string
): Promise<void> {
	if (!modelId) {
		return;
	}
	try {
		await request.delete(`${UMS_BASE_URL}/models/${encodeURIComponent(modelId)}/registration`, {
			headers: {
				Accept: 'application/json'
			}
		});
	} catch {
		// Cleanup is best-effort.
	}
}

function slugifyCandidateId(name: string): string {
	return String(name || '')
		.toLowerCase()
		.replace(/[^a-z0-9]+/gu, '-')
		.replace(/^-+|-+$/gu, '') || 'model';
}

function normalizeBaseUrl(value: string): string {
	return String(value || '').trim().replace(/\/+$/u, '');
}

function normalizeWhitespace(value: string | null | undefined): string {
	return String(value || '')
		.replace(/\s+/gu, ' ')
		.trim();
}

function parseInteger(value: string | undefined, fallback: number): number {
	const parsed = Number.parseInt(String(value || '').trim(), 10);
	return Number.isFinite(parsed) ? parsed : fallback;
}

function escapeRegExp(value: string): string {
	return String(value).replace(/[.*+?^${}()|[\]\\]/gu, '\\$&');
}

function resolveConfiguredPath(value: string): string {
	const rawValue = String(value || '').trim();
	if (!rawValue) {
		return '';
	}
	return path.isAbsolute(rawValue) ? path.resolve(rawValue) : path.resolve(BACKEND_ROOT, rawValue);
}

function parseEnvFile(filePath: string): Record<string, string> {
	if (!fs.existsSync(filePath)) {
		return {};
	}
	const env: Record<string, string> = {};
	const content = fs.readFileSync(filePath, 'utf-8');
	for (const line of content.split(/\r?\n/gu)) {
		const trimmed = line.trim();
		if (!trimmed || trimmed.startsWith('#')) {
			continue;
		}
		const separatorIndex = trimmed.indexOf('=');
		if (separatorIndex <= 0) {
			continue;
		}
		const key = trimmed.slice(0, separatorIndex).trim();
		let value = trimmed.slice(separatorIndex + 1).trim();
		if (
			(value.startsWith('"') && value.endsWith('"')) ||
			(value.startsWith("'") && value.endsWith("'"))
		) {
			value = value.slice(1, -1);
		}
		env[key] = value;
	}
	return env;
}
