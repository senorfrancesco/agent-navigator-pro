import { ConnectionStatusBadge } from './ConnectionStatus';
import { ConnectionStatus, BackendMode } from '@/types/agent';
import { Button } from '@/components/ui/button';
import { Download, Menu, Activity } from 'lucide-react';
import { cn } from '@/lib/utils';

interface ChatHeaderProps {
  connectionStatus: ConnectionStatus;
  backendMode: BackendMode;
  queuePosition?: number;
  onExport: () => void;
  onToggleSidebar?: () => void;
  onToggleResourceMonitor?: () => void;
  isMobile?: boolean;
  showResourceMonitor?: boolean;
}

export function ChatHeader({
  connectionStatus,
  backendMode,
  queuePosition,
  onExport,
  onToggleSidebar,
  onToggleResourceMonitor,
  isMobile,
  showResourceMonitor,
}: ChatHeaderProps) {
  return (
    <header className="flex h-14 items-center justify-between border-b border-border bg-card px-4">
      <div className="flex items-center gap-3">
        {isMobile && onToggleSidebar && (
          <Button variant="ghost" size="icon" onClick={onToggleSidebar}>
            <Menu className="h-5 w-5" />
          </Button>
        )}
        <ConnectionStatusBadge
          status={connectionStatus}
          backendMode={backendMode}
          queuePosition={queuePosition}
        />
      </div>

      <div className="flex items-center gap-2">
        {onToggleResourceMonitor && (
          <Button
            variant="ghost"
            size="sm"
            onClick={onToggleResourceMonitor}
            className={cn("gap-2", showResourceMonitor && "bg-secondary")}
          >
            <Activity className="h-4 w-4" />
            <span className="hidden sm:inline">Ресурсы</span>
          </Button>
        )}
        <Button variant="ghost" size="sm" onClick={onExport} className="gap-2">
          <Download className="h-4 w-4" />
          <span className="hidden sm:inline">Экспорт</span>
        </Button>
      </div>
    </header>
  );
}
