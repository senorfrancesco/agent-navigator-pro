import { useState, useCallback } from 'react';
import { useIsMobile } from '@/hooks/use-mobile';
import { useAppSettings } from '@/hooks/useAppSettings';
import { useChatHistory } from '@/hooks/useChatHistory';
import { ChatSidebar } from './ChatSidebar';
import { ChatHeader } from './ChatHeader';
import { ChatArea } from './ChatArea';
import { SettingsPanel } from './SettingsPanel';
import { ExportButton } from './ExportButton';
import { FileAttachment, AgentStep, Message } from '@/types/agent';
import { Sheet, SheetContent } from '@/components/ui/sheet';
import { toast } from '@/hooks/use-toast';

// Demo data generator for testing
function generateDemoSteps(): AgentStep[] {
  return [
    {
      id: '1',
      type: 'thought',
      content: 'Пользователь хочет получить информацию о документе. Нужно использовать Document Server для загрузки и анализа.',
      timestamp: Date.now(),
      duration: 0.15,
    },
    {
      id: '2',
      type: 'action',
      content: 'Вызываю инструмент для загрузки документа',
      timestamp: Date.now(),
      toolName: 'document_server.load_document',
      toolParams: { path: '/docs/example.pdf' },
      duration: 1.23,
    },
    {
      id: '3',
      type: 'observation',
      content: 'Документ успешно загружен. Содержит 15 страниц, 3,500 символов текста.',
      timestamp: Date.now(),
      duration: 0.05,
      rawJson: {
        status: 'success',
        pages: 15,
        characters: 3500,
        format: 'pdf',
      },
    },
    {
      id: '4',
      type: 'thought',
      content: 'Документ загружен. Теперь могу предоставить пользователю информацию о его содержимом.',
      timestamp: Date.now(),
      duration: 0.12,
    },
  ];
}

export function AgentInterface() {
  const isMobile = useIsMobile();
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [isProcessing, setIsProcessing] = useState(false);

  const {
    settings,
    updateServerUrl,
    updateBackendMode,
    updateModelSettings,
    checkConnection,
    toggleShowJson,
  } = useAppSettings();

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

    setIsProcessing(true);

    // Simulate API call to the agent backend
    // In real implementation, this would call settings.server.url
    try {
      // Demo: simulate processing time
      await new Promise(resolve => setTimeout(resolve, 1500));

      // Add agent response with demo steps
      addMessage({
        role: 'assistant',
        content: 'Я проанализировал ваш запрос. Документ успешно загружен и обработан. Он содержит 15 страниц текста.',
        steps: generateDemoSteps(),
      });

    } catch (error) {
      toast({
        title: 'Ошибка',
        description: 'Не удалось получить ответ от агента',
        variant: 'destructive',
      });
    } finally {
      setIsProcessing(false);
    }
  }, [addMessage]);

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
          connectionStatus={settings.server.connectionStatus}
          backendMode={settings.server.backendMode}
          onExport={handleExport}
          onToggleSidebar={() => setSidebarOpen(true)}
          isMobile={isMobile}
        />

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
      />
    </div>
  );
}
