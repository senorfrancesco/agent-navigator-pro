// Types for the ReAct Agent UI

export type BackendMode = 'llama-cpp-python' | 'llama-server' | 'vllm';

export type ConnectionStatus = 'connected' | 'disconnected' | 'connecting' | 'error';

export type StepType = 'thought' | 'action' | 'observation';

export interface AgentStep {
  id: string;
  type: StepType;
  content: string;
  timestamp: number;
  duration?: number;
  toolName?: string;
  toolParams?: Record<string, unknown>;
  rawJson?: Record<string, unknown>;
}

export interface Message {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  timestamp: number;
  steps?: AgentStep[];
  isStreaming?: boolean;
  attachments?: FileAttachment[];
}

export interface FileAttachment {
  id: string;
  name: string;
  path: string;
  size: number;
  type: string;
}

export interface Chat {
  id: string;
  title: string;
  messages: Message[];
  createdAt: number;
  updatedAt: number;
}

export interface ServerConfig {
  url: string;
  backendMode: BackendMode;
  isConnected: boolean;
  connectionStatus: ConnectionStatus;
}

export interface ModelSettings {
  temperature: number;
  maxTokens: number;
  systemPrompt: string;
}

export interface MCPServerStatus {
  name: string;
  port: number;
  status: ConnectionStatus;
  endpoint?: string;
}

export interface QueueStatus {
  position: number;
  estimatedWait?: number;
  activeSessions: number;
}

export interface AppSettings {
  server: ServerConfig;
  model: ModelSettings;
  mcpServers: MCPServerStatus[];
  showJsonByDefault: boolean;
}

// Default values
export const defaultSettings: AppSettings = {
  server: {
    url: 'http://localhost:8000',
    backendMode: 'llama-cpp-python',
    isConnected: false,
    connectionStatus: 'disconnected',
  },
  model: {
    temperature: 0.7,
    maxTokens: 2048,
    systemPrompt: 'You are a helpful assistant.',
  },
  mcpServers: [
    { name: 'Document Server', port: 8001, status: 'disconnected' },
    { name: 'Legal Server', port: 8002, status: 'disconnected' },
    { name: 'UMS', port: 8090, status: 'disconnected' },
  ],
  showJsonByDefault: false,
};
