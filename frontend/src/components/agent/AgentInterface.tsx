import { useState, useCallback } from 'react';
import { useIsMobile } from '@/hooks/use-mobile';
import { useAppSettings } from '@/hooks/useAppSettings';
import { useChatHistory } from '@/hooks/useChatHistory';
import { useAgentConnection } from '@/hooks/useAgentConnection';
import { ChatSidebar } from './ChatSidebar';
import { ChatHeader } from './ChatHeader';
import { ChatArea } from './ChatArea';
import { SettingsPanel } from './SettingsPanel';
import { ResourceMonitor } from './ResourceMonitor';
import { FileAttachment, AgentStep, Message } from '@/types/agent';
import { Sheet, SheetContent } from '@/components/ui/sheet';
import { toast } from '@/hooks/use-toast';
import { sendMessage, streamAgentSteps } from '@/services/agentApi';

export function AgentInterface() {
  const isMobile = useIsMobile();
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [isProcessing, setIsProcessing] = useState(false);
  const [showResourceMonitor, setShowResourceMonitor] = useState(false);

  const {
    settings,
    updateServerUrl,
    updateBackendMode,
    updateModelSettings,
    toggleShowJson,
  } = useAppSettings();

  const {
    connectionStatus,
    backendMode,
    mcpServers,
    resources,
    isChecking,
    checkConnection,
  } = useAgentConnection(settings.server.url);

  const {
    chats,
    activeChat,
    activeChatId,
    setActiveChatId,
    createNewChat,
    deleteChat,
    addMessage,
    updateMessage,
  } = useChatHistory();

  const handleSendMessage = useCallback(async (content: string, attachments: FileAttachment[]) => {
    // Add user message
    const userMessage = addMessage({
      role: 'user',
      content,
      attachments,
    });

    // Create placeholder for assistant message
    const assistantMessage = addMessage({
      role: 'assistant',
      content: '',
      isStreaming: true,
      steps: [],
    });

    setIsProcessing(true);

    try {
      // Send message to agent API
      const response = await sendMessage(
        settings.server.url,
        content,
        attachments.length > 0 ? attachments : undefined,
        {
          temperature: settings.model.temperature,
          maxTokens: settings.model.maxTokens,
        }
      );

      const collectedSteps: AgentStep[] = [];
      let finalAnswer = '';

      // Stream agent steps
      const cleanup = streamAgentSteps(
        settings.server.url,
        response.session_id,
        (step) => {
          // Handle streaming chunks and final answer
          if (step.type === 'chunk') {
            finalAnswer += step.content;
          } else if (step.type === 'final_answer') {
            finalAnswer = step.content;
          } else {
            collectedSteps.push(step);
          }

          // Update message with new steps and content
          updateMessage(assistantMessage.id, {
            steps: [...collectedSteps],
            content: finalAnswer || 'Обрабатываю запрос...',
            isStreaming: true,
          });
        },
        (error) => {
          console.error('Stream error:', error);
          toast({
            title: 'Ошибка соединения',
            description: error.message,
            variant: 'destructive',
          });
          setIsProcessing(false);
          
          updateMessage(assistantMessage.id, {
            isStreaming: false,
            content: finalAnswer || 'Произошла ошибка при получении ответа.',
          });
        },
        () => {
          // Stream completed
          setIsProcessing(false);
          
          updateMessage(assistantMessage.id, {
            isStreaming: false,
            content: finalAnswer || 'Анализ завершен.',
            steps: collectedSteps,
          });
        }
      );

    } catch (error) {
      console.error('Failed to send message:', error);
      
      toast({
        title: 'Ошибка',
        description: error instanceof Error ? error.message : 'Не удалось отправить сообщение',
        variant: 'destructive',
      });

      // Update assistant message with error
      updateMessage(assistantMessage.id, {
        isStreaming: false,
        content: 'Не удалось подключиться к агенту. Проверьте настройки сервера.',
      });
      
      setIsProcessing(false);
    }
  }, [addMessage, updateMessage, settings.server.url, settings.model]);

  const handleExport = useCallback(() => {
    // Export handled by ExportButton component
  }, []);

  const sidebar = (
    <ChatSidebar
      chats={chats}
      activeChatId={activeChatId}
      onSelectChat={setActiveChatId}
      onNewChat={createNewChat}
      onDeleteChat={deleteChat}
      onOpenSettings={() => setSettingsOpen(true)}
    />
  );

  return (
    <div className="flex h-screen bg-background">
      {/* Desktop sidebar */}
      {!isMobile && sidebar}

      {/* Mobile sidebar */}
      {isMobile && (
        <Sheet open={sidebarOpen} onOpenChange={setSidebarOpen}>
          <SheetContent side="left" className="w-64 p-0">
            {sidebar}
          </SheetContent>
        </Sheet>
      )}

      {/* Main content */}
      <div className="flex flex-1 flex-col">
        <ChatHeader
          connectionStatus={connectionStatus}
          backendMode={backendMode}
          onExport={handleExport}
          onToggleSidebar={() => setSidebarOpen(true)}
          onToggleResourceMonitor={() => setShowResourceMonitor(!showResourceMonitor)}
          isMobile={isMobile}
          showResourceMonitor={showResourceMonitor}
        />

        {/* Resource Monitor */}
        {showResourceMonitor && (
          <ResourceMonitor
            resources={resources}
            mcpServers={mcpServers}
          />
        )}

        <ChatArea
          chat={activeChat}
          showJsonByDefault={settings.showJsonByDefault}
          onSendMessage={handleSendMessage}
          isProcessing={isProcessing}
        />
      </div>

      {/* Settings panel */}
      <SettingsPanel
        open={settingsOpen}
        onOpenChange={setSettingsOpen}
        settings={settings}
        onUpdateServerUrl={updateServerUrl}
        onUpdateBackendMode={updateBackendMode}
        onUpdateModelSettings={updateModelSettings}
        onCheckConnection={checkConnection}
        onToggleShowJson={toggleShowJson}
        connectionStatus={connectionStatus}
        isCheckingConnection={isChecking}
      />
    </div>
  );
}
