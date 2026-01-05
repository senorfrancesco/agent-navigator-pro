import { ConnectionStatusBadge } from './ConnectionStatus';
import { ConnectionStatus, BackendMode } from '@/types/agent';
import { Button } from '@/components/ui/button';
import { Download, Menu } from 'lucide-react';

interface ChatHeaderProps {
  connectionStatus: ConnectionStatus;
  backendMode: BackendMode;
  queuePosition?: number;
  onExport: () => void;
  onToggleSidebar?: () => void;
  isMobile?: boolean;
}

export function ChatHeader({
  connectionStatus,
  backendMode,
  queuePosition,
  onExport,
  onToggleSidebar,
  isMobile,
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
        <Button variant="ghost" size="sm" onClick={onExport} className="gap-2">
          <Download className="h-4 w-4" />
          <span className="hidden sm:inline">Экспорт</span>
        </Button>
      </div>
    </header>
  );
}
