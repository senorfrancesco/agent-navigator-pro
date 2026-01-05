import { Chat, Message, FileAttachment } from '@/types/agent';
import { Chat as ChatUtils } from '@/types/agent';
import { ScrollArea } from '@/components/ui/scroll-area';
import { ChatMessage } from './ChatMessage';
import { ChatInput } from './ChatInput';
import { Bot } from 'lucide-react';

interface ChatAreaProps {
  chat: Chat | null;
  showJsonByDefault?: boolean;
  onSendMessage: (message: string, attachments: FileAttachment[]) => void;
  isProcessing?: boolean;
}

export function ChatArea({ chat, showJsonByDefault, onSendMessage, isProcessing }: ChatAreaProps) {
  return (
    <div className="flex flex-1 flex-col">
      {/* Messages area */}
      <ScrollArea className="flex-1">
        <div className="p-4 space-y-4">
          {!chat || chat.messages.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-[60vh] text-center">
              <div className="h-16 w-16 rounded-full bg-accent/20 flex items-center justify-center mb-4">
                <Bot className="h-8 w-8 text-accent" />
              </div>
              <h2 className="text-xl font-semibold mb-2">ReAct Agent</h2>
              <p className="text-muted-foreground max-w-md">
                Мультиагентная система с инструментами. Начните диалог, чтобы увидеть процесс работы агента.
              </p>
            </div>
          ) : (
            chat.messages.map(message => (
              <ChatMessage
                key={message.id}
                message={message}
                showJsonByDefault={showJsonByDefault}
              />
            ))
          )}
        </div>
      </ScrollArea>

      {/* Input area */}
      <ChatInput
        onSend={onSendMessage}
        disabled={isProcessing}
        placeholder={isProcessing ? 'Агент обрабатывает запрос...' : 'Введите сообщение...'}
      />
    </div>
  );
}
