import { AppSettings, BackendMode, MCPServerStatus } from '@/types/agent';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Slider } from '@/components/ui/slider';
import { Textarea } from '@/components/ui/textarea';
import { Switch } from '@/components/ui/switch';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Sheet, SheetContent, SheetHeader, SheetTitle } from '@/components/ui/sheet';
import { Separator } from '@/components/ui/separator';
import { ScrollArea } from '@/components/ui/scroll-area';
import { cn } from '@/lib/utils';
import { Loader2, Check, X, Server } from 'lucide-react';
import { ConnectionStatus } from '@/types/agent';

interface SettingsPanelProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  settings: AppSettings;
  onUpdateServerUrl: (url: string) => void;
  onUpdateBackendMode: (mode: BackendMode) => void;
  onUpdateModelSettings: (settings: Partial<AppSettings['model']>) => void;
  onCheckConnection: () => Promise<void>;
  onToggleShowJson: () => void;
  connectionStatus?: ConnectionStatus;
  isCheckingConnection?: boolean;
}

const backendOptions: { value: BackendMode; label: string; description: string }[] = [
  { value: 'llama-cpp-python', label: 'llama-cpp-python', description: 'Для разработки, слабое железо' },
  { value: 'llama-server', label: 'llama-server', description: 'CLI для новых моделей' },
  { value: 'vllm', label: 'vLLM', description: 'Продакшен, параллельная обработка' },
];

const statusColors = {
  connected: 'bg-[hsl(var(--success))]',
  disconnected: 'bg-muted-foreground',
  connecting: 'bg-[hsl(var(--warning))]',
  error: 'bg-destructive',
};

function MCPServerItem({ server }: { server: MCPServerStatus }) {
  return (
    <div className="flex items-center justify-between rounded-lg bg-secondary/50 px-3 py-2">
      <div className="flex items-center gap-2">
        <Server className="h-4 w-4 text-muted-foreground" />
        <span className="text-sm">{server.name}</span>
      </div>
      <div className="flex items-center gap-2">
        <span className="text-xs text-muted-foreground">:{server.port}</span>
        <div className={cn('h-2 w-2 rounded-full', statusColors[server.status])} />
      </div>
    </div>
  );
}

export function SettingsPanel({
  open,
  onOpenChange,
  settings,
  onUpdateServerUrl,
  onUpdateBackendMode,
  onUpdateModelSettings,
  onCheckConnection,
  onToggleShowJson,
  connectionStatus,
  isCheckingConnection,
}: SettingsPanelProps) {
  const handleCheckConnection = async () => {
    await onCheckConnection();
  };

  const currentStatus = connectionStatus || settings.server.connectionStatus;
  const isChecking = isCheckingConnection || currentStatus === 'connecting';

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="w-full sm:max-w-md">
        <SheetHeader>
          <SheetTitle>Настройки</SheetTitle>
        </SheetHeader>

        <ScrollArea className="h-[calc(100vh-100px)] pr-4">
          <div className="space-y-6 py-4">
            {/* Server Connection */}
            <section className="space-y-4">
              <h3 className="text-sm font-medium">Подключение к серверу</h3>
              
              <div className="space-y-2">
                <Label htmlFor="serverUrl">URL бэкенда</Label>
                <div className="flex gap-2">
                  <Input
                    id="serverUrl"
                    value={settings.server.url}
                    onChange={(e) => onUpdateServerUrl(e.target.value)}
                    placeholder="http://localhost:8000"
                  />
                  <Button 
                    variant="outline" 
                    size="icon"
                    onClick={handleCheckConnection}
                    disabled={isChecking}
                  >
                    {isChecking ? (
                      <Loader2 className="h-4 w-4 animate-spin" />
                    ) : currentStatus === 'connected' ? (
                      <Check className="h-4 w-4 text-[hsl(var(--success))]" />
                    ) : (
                      <X className="h-4 w-4" />
                    )}
                  </Button>
                </div>
                <p className="text-xs text-muted-foreground">
                  Статус: {currentStatus === 'connected' ? 'Подключено' : 
                           currentStatus === 'connecting' ? 'Подключение...' :
                           currentStatus === 'error' ? 'Ошибка' : 'Отключено'}
                </p>
              </div>

              <div className="space-y-2">
                <Label>Режим бэкенда</Label>
                <Select
                  value={settings.server.backendMode}
                  onValueChange={(value) => onUpdateBackendMode(value as BackendMode)}
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {backendOptions.map(option => (
                      <SelectItem key={option.value} value={option.value}>
                        <div className="flex flex-col">
                          <span>{option.label}</span>
                          <span className="text-xs text-muted-foreground">{option.description}</span>
                        </div>
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </section>

            <Separator />

            {/* Model Settings */}
            <section className="space-y-4">
              <h3 className="text-sm font-medium">Параметры модели</h3>

              <div className="space-y-2">
                <div className="flex items-center justify-between">
                  <Label>Temperature</Label>
                  <span className="text-sm text-muted-foreground">
                    {settings.model.temperature.toFixed(2)}
                  </span>
                </div>
                <Slider
                  value={[settings.model.temperature]}
                  onValueChange={([value]) => onUpdateModelSettings({ temperature: value })}
                  min={0}
                  max={2}
                  step={0.1}
                />
              </div>

              <div className="space-y-2">
                <div className="flex items-center justify-between">
                  <Label>Max Tokens</Label>
                  <span className="text-sm text-muted-foreground">
                    {settings.model.maxTokens}
                  </span>
                </div>
                <Slider
                  value={[settings.model.maxTokens]}
                  onValueChange={([value]) => onUpdateModelSettings({ maxTokens: value })}
                  min={256}
                  max={8192}
                  step={256}
                />
              </div>

              <div className="space-y-2">
                <Label htmlFor="systemPrompt">System Prompt</Label>
                <Textarea
                  id="systemPrompt"
                  value={settings.model.systemPrompt}
                  onChange={(e) => onUpdateModelSettings({ systemPrompt: e.target.value })}
                  rows={4}
                  className="resize-none"
                />
              </div>
            </section>

            <Separator />

            {/* MCP Servers */}
            <section className="space-y-4">
              <h3 className="text-sm font-medium">MCP Серверы</h3>
              <div className="space-y-2">
                {settings.mcpServers.map(server => (
                  <MCPServerItem key={server.name} server={server} />
                ))}
              </div>
            </section>

            <Separator />

            {/* UI Settings */}
            <section className="space-y-4">
              <h3 className="text-sm font-medium">Интерфейс</h3>
              
              <div className="flex items-center justify-between">
                <div className="space-y-0.5">
                  <Label>Показывать JSON по умолчанию</Label>
                  <p className="text-xs text-muted-foreground">
                    Разворачивать JSON-ответы в логах
                  </p>
                </div>
                <Switch
                  checked={settings.showJsonByDefault}
                  onCheckedChange={onToggleShowJson}
                />
              </div>
            </section>
          </div>
        </ScrollArea>
      </SheetContent>
    </Sheet>
  );
}
