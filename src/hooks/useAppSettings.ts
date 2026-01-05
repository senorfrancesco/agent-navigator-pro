import { useState, useEffect, useCallback } from 'react';
import { AppSettings, defaultSettings, ConnectionStatus, BackendMode } from '@/types/agent';

const SETTINGS_KEY = 'react-agent-settings';

export function useAppSettings() {
  const [settings, setSettings] = useState<AppSettings>(() => {
    const stored = localStorage.getItem(SETTINGS_KEY);
    if (stored) {
      try {
        return { ...defaultSettings, ...JSON.parse(stored) };
      } catch {
        return defaultSettings;
      }
    }
    return defaultSettings;
  });

  useEffect(() => {
    localStorage.setItem(SETTINGS_KEY, JSON.stringify(settings));
  }, [settings]);

  const updateServerUrl = useCallback((url: string) => {
    setSettings(prev => ({
      ...prev,
      server: { ...prev.server, url },
    }));
  }, []);

  const updateBackendMode = useCallback((backendMode: BackendMode) => {
    setSettings(prev => ({
      ...prev,
      server: { ...prev.server, backendMode },
    }));
  }, []);

  const updateConnectionStatus = useCallback((status: ConnectionStatus) => {
    setSettings(prev => ({
      ...prev,
      server: {
        ...prev.server,
        connectionStatus: status,
        isConnected: status === 'connected',
      },
    }));
  }, []);

  const updateModelSettings = useCallback((model: Partial<AppSettings['model']>) => {
    setSettings(prev => ({
      ...prev,
      model: { ...prev.model, ...model },
    }));
  }, []);

  const updateMCPServerStatus = useCallback((name: string, status: ConnectionStatus) => {
    setSettings(prev => ({
      ...prev,
      mcpServers: prev.mcpServers.map(server =>
        server.name === name ? { ...server, status } : server
      ),
    }));
  }, []);

  const toggleShowJson = useCallback(() => {
    setSettings(prev => ({
      ...prev,
      showJsonByDefault: !prev.showJsonByDefault,
    }));
  }, []);

  const checkConnection = useCallback(async () => {
    updateConnectionStatus('connecting');
    try {
      const response = await fetch(`${settings.server.url}/health`, {
        method: 'GET',
        signal: AbortSignal.timeout(5000),
      });
      if (response.ok) {
        updateConnectionStatus('connected');
        return true;
      }
      updateConnectionStatus('error');
      return false;
    } catch {
      updateConnectionStatus('disconnected');
      return false;
    }
  }, [settings.server.url, updateConnectionStatus]);

  return {
    settings,
    updateServerUrl,
    updateBackendMode,
    updateConnectionStatus,
    updateModelSettings,
    updateMCPServerStatus,
    toggleShowJson,
    checkConnection,
  };
}
