/**
 * Hook for managing connection to the Agent API
 * Handles health checks, status monitoring, and reconnection
 */

import { useState, useCallback, useEffect, useRef } from 'react';
import { ConnectionStatus, BackendMode, MCPServerStatus } from '@/types/agent';
import { 
  checkHealth, 
  getStatus, 
  deriveConnectionStatus, 
  deriveMCPServers,
} from '@/services/agentApi';

export interface ResourceStatus {
  vramUsedGb: number;
  vramTotalGb: number;
  vramFreeGb: number;
  ramUsedGb: number;
  ramTotalGb: number;
  ramFreeGb: number;
  ramPercent: number;
  cpuPercent: number;
  cpuCount: number;
  activeModel: string | null;
  queueSize: number;
  cudaAvailable: boolean;
  cudaVersion: string | null;
  driverVersion: string | null;
  gpuTemperature: number | null;
  gpuUtilization: number | null;
}

interface UseAgentConnectionResult {
  connectionStatus: ConnectionStatus;
  backendMode: BackendMode;
  mcpServers: MCPServerStatus[];
  resources: ResourceStatus;
  isChecking: boolean;
  lastError: string | null;
  checkConnection: () => Promise<void>;
  startAutoRefresh: (intervalMs?: number) => void;
  stopAutoRefresh: () => void;
}

const DEFAULT_RESOURCES: ResourceStatus = {
  vramUsedGb: 0,
  vramTotalGb: 0,
  vramFreeGb: 0,
  ramUsedGb: 0,
  ramTotalGb: 0,
  ramFreeGb: 0,
  ramPercent: 0,
  cpuPercent: 0,
  cpuCount: 0,
  activeModel: null,
  queueSize: 0,
  cudaAvailable: false,
  cudaVersion: null,
  driverVersion: null,
  gpuTemperature: null,
  gpuUtilization: null,
};

export function useAgentConnection(serverUrl: string): UseAgentConnectionResult {
  const [connectionStatus, setConnectionStatus] = useState<ConnectionStatus>('disconnected');
  const [backendMode, setBackendMode] = useState<BackendMode>('llama-cpp-python');
  const [mcpServers, setMcpServers] = useState<MCPServerStatus[]>([]);
  const [resources, setResources] = useState<ResourceStatus>(DEFAULT_RESOURCES);
  const [isChecking, setIsChecking] = useState(false);
  const [lastError, setLastError] = useState<string | null>(null);
  
  const intervalRef = useRef<NodeJS.Timeout | null>(null);

  const checkConnection = useCallback(async () => {
    if (!serverUrl) {
      setConnectionStatus('disconnected');
      return;
    }

    setIsChecking(true);
    setConnectionStatus('connecting');
    setLastError(null);

    try {
      // Check health first
      const health = await checkHealth(serverUrl);
      const status = deriveConnectionStatus(health);
      setConnectionStatus(status);

      // If connected, get detailed status
      if (status === 'connected') {
        try {
          const statusData = await getStatus(serverUrl);
          
          setBackendMode(statusData.backend_mode);
          setMcpServers(deriveMCPServers(statusData));
          setResources({
            vramUsedGb: statusData.vram_used_gb,
            vramTotalGb: statusData.vram_total_gb,
            vramFreeGb: statusData.vram_free_gb,
            ramUsedGb: statusData.ram_used_gb,
            ramTotalGb: statusData.ram_total_gb,
            ramFreeGb: statusData.ram_free_gb,
            ramPercent: statusData.ram_percent,
            cpuPercent: statusData.cpu_percent,
            cpuCount: statusData.cpu_count,
            activeModel: statusData.active_model,
            queueSize: statusData.queue_size,
            cudaAvailable: statusData.cuda_available,
            cudaVersion: statusData.cuda_version,
            driverVersion: statusData.driver_version,
            gpuTemperature: statusData.gpu_temperature,
            gpuUtilization: statusData.gpu_utilization,
          });
        } catch (statusError) {
          // Status endpoint may not be available, but connection is still valid
          console.warn('Could not fetch status:', statusError);
        }
      }
    } catch (error) {
      console.error('Connection check failed:', error);
      setConnectionStatus('error');
      setLastError(error instanceof Error ? error.message : 'Unknown error');
      setMcpServers([]);
      setResources(DEFAULT_RESOURCES);
    } finally {
      setIsChecking(false);
    }
  }, [serverUrl]);

  const startAutoRefresh = useCallback((intervalMs = 30000) => {
    stopAutoRefresh();
    
    // Initial check
    checkConnection();
    
    // Set up interval
    intervalRef.current = setInterval(() => {
      checkConnection();
    }, intervalMs);
  }, [checkConnection]);

  const stopAutoRefresh = useCallback(() => {
    if (intervalRef.current) {
      clearInterval(intervalRef.current);
      intervalRef.current = null;
    }
  }, []);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      stopAutoRefresh();
    };
  }, [stopAutoRefresh]);

  // Check connection when URL changes
  useEffect(() => {
    if (serverUrl) {
      checkConnection();
    }
  }, [serverUrl, checkConnection]);

  return {
    connectionStatus,
    backendMode,
    mcpServers,
    resources,
    isChecking,
    lastError,
    checkConnection,
    startAutoRefresh,
    stopAutoRefresh,
  };
}
