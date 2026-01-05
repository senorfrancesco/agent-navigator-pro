/**
 * API Client for ReAct Agent Backend
 * 
 * Provides functions for:
 * - Health checks and status monitoring
 * - Sending messages to the agent
 * - SSE streaming of agent steps
 */

import { AgentStep, ConnectionStatus, BackendMode, MCPServerStatus, FileAttachment } from '@/types/agent';

// Types for API responses
export interface HealthResponse {
  status: 'healthy' | 'degraded' | 'error';
  services: {
    agent: { status: string };
    ums: { status: string; details?: unknown };
    document_server: { status: string; details?: unknown };
    legal_server: { status: string; details?: unknown };
  };
  timestamp: string;
}

export interface StatusResponse {
  active_model: string | null;
  backend_mode: BackendMode;
  vram_used_gb: number;
  vram_total_gb: number;
  vram_free_gb: number;
  ram_used_gb: number;
  ram_total_gb: number;
  ram_free_gb: number;
  ram_percent: number;
  cpu_percent: number;
  cpu_count: number;
  cuda_available: boolean;
  cuda_version: string | null;
  driver_version: string | null;
  gpu_temperature: number | null;
  gpu_utilization: number | null;
  queue_size: number;
  mcp_servers: Array<{
    name: string;
    port: number;
    status: string;
  }>;
}

export interface CudaInfo {
  cuda_available: boolean;
  cuda_version: string | null;
  driver_version: string | null;
  gpu_count: number;
  gpus: Array<{
    index: number;
    name: string;
    vram_gb: number;
  }>;
}

export interface ChatResponse {
  session_id: string;
  status: string;
  message: string;
}

export interface AgentStepResponse {
  id: string;
  type: 'thought' | 'action' | 'observation' | 'final_answer' | 'start' | 'end';
  content?: string;
  timestamp?: number;
  duration_ms?: number;
  tool_name?: string;
  tool_params?: Record<string, unknown>;
  raw_json?: Record<string, unknown>;
  session_id?: string;
}

// Default timeout values
const HEALTH_CHECK_TIMEOUT = 5000;
const CHAT_TIMEOUT = 10000;

/**
 * Check health of all backend services
 */
export async function checkHealth(baseUrl: string): Promise<HealthResponse> {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), HEALTH_CHECK_TIMEOUT);

  try {
    const response = await fetch(`${baseUrl}/health`, {
      method: 'GET',
      signal: controller.signal,
    });

    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }

    return await response.json();
  } finally {
    clearTimeout(timeoutId);
  }
}

/**
 * Get system status including resource usage
 */
export async function getStatus(baseUrl: string): Promise<StatusResponse> {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), HEALTH_CHECK_TIMEOUT);

  try {
    const response = await fetch(`${baseUrl}/status`, {
      method: 'GET',
      signal: controller.signal,
    });

    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }

    return await response.json();
  } finally {
    clearTimeout(timeoutId);
  }
}

/**
 * Derive connection status from health response
 */
export function deriveConnectionStatus(health: HealthResponse): ConnectionStatus {
  if (health.status === 'healthy') {
    return 'connected';
  }
  if (health.status === 'degraded') {
    return 'connected'; // Still usable
  }
  return 'error';
}

/**
 * Derive MCP server statuses from status response
 */
export function deriveMCPServers(status: StatusResponse): MCPServerStatus[] {
  return status.mcp_servers.map(server => ({
    name: server.name,
    port: server.port,
    status: server.status === 'connected' ? 'connected' : 'disconnected',
  }));
}

/**
 * Send a message to the agent and get session ID
 */
export async function sendMessage(
  baseUrl: string,
  query: string,
  attachments?: FileAttachment[],
  modelSettings?: { temperature?: number; maxTokens?: number }
): Promise<ChatResponse> {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), CHAT_TIMEOUT);

  try {
    const response = await fetch(`${baseUrl}/agent/chat`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        query,
        attachments: attachments?.map(a => ({
          name: a.name,
          path: a.path,
          size: a.size,
          type: a.type,
        })),
        model_settings: modelSettings ? {
          maxTokens: modelSettings.maxTokens,
          temperature: modelSettings.temperature,
        } : undefined,
      }),
      signal: controller.signal,
    });

    if (!response.ok) {
      const errorText = await response.text();
      throw new Error(`HTTP ${response.status}: ${errorText}`);
    }

    return await response.json();
  } finally {
    clearTimeout(timeoutId);
  }
}

/**
 * Stream agent steps via SSE
 * 
 * @param baseUrl - Base URL of the agent API
 * @param sessionId - Session ID from sendMessage
 * @param onStep - Callback for each step received
 * @param onError - Callback for errors
 * @param onComplete - Callback when stream ends
 * @returns Cleanup function to close the connection
 */
export function streamAgentSteps(
  baseUrl: string,
  sessionId: string,
  onStep: (step: AgentStep) => void,
  onError: (error: Error) => void,
  onComplete: () => void
): () => void {
  const eventSource = new EventSource(`${baseUrl}/agent/stream/${sessionId}`);
  
  eventSource.onmessage = (event) => {
    try {
      const data: AgentStepResponse = JSON.parse(event.data);
      
      // Handle control messages
      if (data.type === 'start') {
        console.log('Agent stream started:', data.session_id);
        return;
      }
      
      if (data.type === 'end') {
        console.log('Agent stream ended:', data.session_id);
        eventSource.close();
        onComplete();
        return;
      }
      
      // Convert to AgentStep format
      const step: AgentStep = {
        id: data.id,
        type: data.type as 'thought' | 'action' | 'observation',
        content: data.content || '',
        timestamp: data.timestamp || Date.now(),
        duration: data.duration_ms ? data.duration_ms / 1000 : undefined,
        toolName: data.tool_name,
        toolParams: data.tool_params,
        rawJson: data.raw_json,
      };
      
      onStep(step);
    } catch (e) {
      console.error('Error parsing SSE message:', e);
    }
  };
  
  eventSource.onerror = (event) => {
    console.error('SSE error:', event);
    eventSource.close();
    onError(new Error('Connection to agent lost'));
  };
  
  // Return cleanup function
  return () => {
    eventSource.close();
  };
}

/**
 * Upload file to the agent backend
 * Returns the path where the file was saved
 */
export async function uploadFile(
  baseUrl: string,
  file: File
): Promise<{ path: string; name: string; size: number; type: string }> {
  const formData = new FormData();
  formData.append('file', file);

  const response = await fetch(`${baseUrl}/upload`, {
    method: 'POST',
    body: formData,
  });

  if (!response.ok) {
    throw new Error(`Failed to upload file: HTTP ${response.status}`);
  }

  const result = await response.json();
  
  return {
    path: result.path,
    name: file.name,
    size: file.size,
    type: file.type,
  };
}

/**
 * Delete a session
 */
export async function deleteSession(baseUrl: string, sessionId: string): Promise<void> {
  await fetch(`${baseUrl}/agent/session/${sessionId}`, {
    method: 'DELETE',
  });
}

/**
 * Get CUDA information
 */
export async function getCudaInfo(baseUrl: string): Promise<CudaInfo> {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), HEALTH_CHECK_TIMEOUT);

  try {
    const response = await fetch(`${baseUrl}/cuda`, {
      method: 'GET',
      signal: controller.signal,
    });

    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }

    return await response.json();
  } finally {
    clearTimeout(timeoutId);
  }
}

/**
 * Get detailed system resources
 */
export async function getResources(baseUrl: string): Promise<Record<string, unknown>> {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), HEALTH_CHECK_TIMEOUT);

  try {
    const response = await fetch(`${baseUrl}/resources`, {
      method: 'GET',
      signal: controller.signal,
    });

    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }

    return await response.json();
  } finally {
    clearTimeout(timeoutId);
  }
}
