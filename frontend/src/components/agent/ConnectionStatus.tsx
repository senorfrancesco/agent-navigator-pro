import { ConnectionStatus as Status, BackendMode } from '@/types/agent';
import { cn } from '@/lib/utils';
import { Wifi, WifiOff, Loader2 } from 'lucide-react';

interface ConnectionStatusProps {
  status: Status;
  backendMode: BackendMode;
  queuePosition?: number;
}

const backendLabels: Record<BackendMode, string> = {
  'llama-cpp-python': 'llama-cpp',
  'llama-server': 'llama-server',
  'vllm': 'vLLM',
};

const statusColors: Record<Status, string> = {
  connected: 'text-[hsl(var(--success))]',
  disconnected: 'text-muted-foreground',
  connecting: 'text-[hsl(var(--warning))]',
  error: 'text-destructive',
};

const statusBgColors: Record<Status, string> = {
  connected: 'bg-[hsl(var(--success))]',
  disconnected: 'bg-muted-foreground',
  connecting: 'bg-[hsl(var(--warning))]',
  error: 'bg-destructive',
};

export function ConnectionStatusBadge({ status, backendMode, queuePosition }: ConnectionStatusProps) {
  return (
    <div className="flex items-center gap-3">
      {/* Connection indicator */}
      <div className="flex items-center gap-2">
        <div className={cn('h-2 w-2 rounded-full', statusBgColors[status])} />
        {status === 'connecting' ? (
          <Loader2 className={cn('h-4 w-4 animate-spin', statusColors[status])} />
        ) : status === 'connected' ? (
          <Wifi className={cn('h-4 w-4', statusColors[status])} />
        ) : (
          <WifiOff className={cn('h-4 w-4', statusColors[status])} />
        )}
      </div>

      {/* Backend mode badge */}
      <span className="rounded-md bg-secondary px-2 py-1 text-xs font-medium text-secondary-foreground">
        {backendLabels[backendMode]}
      </span>

      {/* Queue position for vLLM */}
      {backendMode === 'vllm' && queuePosition !== undefined && queuePosition > 0 && (
        <span className="text-xs text-muted-foreground">
          В очереди: #{queuePosition}
        </span>
      )}
    </div>
  );
}
