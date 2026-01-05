import { Message } from '@/types/agent';
import { cn } from '@/lib/utils';
import { useState } from 'react';
import { User, Bot, ChevronDown, ChevronRight, Paperclip } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible';
import { AgentStepComponent } from './AgentStep';

interface ChatMessageProps {
  message: Message;
  showJsonByDefault?: boolean;
}

export function ChatMessage({ message, showJsonByDefault = false }: ChatMessageProps) {
  const [showSteps, setShowSteps] = useState(true);
  const isUser = message.role === 'user';
  const hasSteps = message.steps && message.steps.length > 0;

  return (
    <div
      className={cn(
        'flex gap-4 p-4 rounded-lg',
        isUser ? 'bg-secondary/30' : 'bg-card'
      )}
    >
      {/* Avatar */}
      <div
        className={cn(
          'flex h-8 w-8 shrink-0 items-center justify-center rounded-full',
          isUser ? 'bg-primary' : 'bg-accent'
        )}
      >
        {isUser ? (
          <User className="h-4 w-4 text-primary-foreground" />
        ) : (
          <Bot className="h-4 w-4 text-accent-foreground" />
        )}
      </div>

      {/* Content */}
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 mb-2">
          <span className="text-sm font-medium text-foreground">
            {isUser ? 'Вы' : 'Агент'}
          </span>
          <span className="text-xs text-muted-foreground">
            {new Date(message.timestamp).toLocaleTimeString('ru-RU', {
              hour: '2-digit',
              minute: '2-digit',
            })}
          </span>
          {message.isStreaming && (
            <span className="flex items-center gap-1 text-xs text-[hsl(var(--info))]">
              <span className="h-1.5 w-1.5 rounded-full bg-[hsl(var(--info))] animate-pulse" />
              Обработка...
            </span>
          )}
        </div>

        {/* Attachments */}
        {message.attachments && message.attachments.length > 0 && (
          <div className="flex flex-wrap gap-2 mb-2">
            {message.attachments.map(file => (
              <div
                key={file.id}
                className="flex items-center gap-1.5 rounded bg-secondary px-2 py-1 text-xs"
              >
                <Paperclip className="h-3 w-3" />
                <span className="truncate max-w-[150px]">{file.name}</span>
              </div>
            ))}
          </div>
        )}

        {/* Message content */}
        <p className="text-sm text-foreground whitespace-pre-wrap break-words">
          {message.content}
        </p>

        {/* Agent steps (thinking process) */}
        {hasSteps && (
          <Collapsible open={showSteps} onOpenChange={setShowSteps} className="mt-4">
            <CollapsibleTrigger asChild>
              <Button variant="ghost" size="sm" className="gap-2 text-muted-foreground">
                {showSteps ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
                Процесс мышления ({message.steps!.length} шагов)
              </Button>
            </CollapsibleTrigger>
            <CollapsibleContent>
              <div className="mt-3 space-y-2 border-l-2 border-border pl-4">
                {message.steps!.map(step => (
                  <AgentStepComponent
                    key={step.id}
                    step={step}
                    showJsonByDefault={showJsonByDefault}
                  />
                ))}
              </div>
            </CollapsibleContent>
          </Collapsible>
        )}
      </div>
    </div>
  );
}
