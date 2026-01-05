/**
 * Resource Monitor Component
 * Displays VRAM, RAM usage, active model, and queue status
 */

import { MCPServerStatus } from '@/types/agent';
import { Badge } from '@/components/ui/badge';
import { Progress } from '@/components/ui/progress';
import { Cpu, HardDrive, Server, Users } from 'lucide-react';

interface ResourceMonitorProps {
  resources: {
    vramUsedGb: number;
    vramTotalGb: number;
    ramUsedGb: number;
    ramTotalGb: number;
  };
  activeModel: string | null;
  queueSize: number;
  mcpServers: MCPServerStatus[];
}

export function ResourceMonitor({
  resources,
  activeModel,
  queueSize,
  mcpServers,
}: ResourceMonitorProps) {
  const vramPercent = (resources.vramUsedGb / resources.vramTotalGb) * 100;
  const ramPercent = (resources.ramUsedGb / resources.ramTotalGb) * 100;

  const getProgressColor = (percent: number) => {
    if (percent > 90) return 'bg-destructive';
    if (percent > 70) return 'bg-yellow-500';
    return 'bg-primary';
  };

  return (
    <div className="border-b border-border bg-muted/30 px-4 py-3">
      <div className="flex flex-wrap items-center gap-6 text-sm">
        {/* VRAM */}
        <div className="flex items-center gap-2 min-w-[180px]">
          <Cpu className="h-4 w-4 text-muted-foreground" />
          <div className="flex-1">
            <div className="flex justify-between mb-1">
              <span className="text-muted-foreground">VRAM</span>
              <span className="font-mono">
                {resources.vramUsedGb.toFixed(1)}/{resources.vramTotalGb}GB
              </span>
            </div>
            <Progress 
              value={vramPercent} 
              className="h-1.5"
            />
          </div>
        </div>

        {/* RAM */}
        <div className="flex items-center gap-2 min-w-[180px]">
          <HardDrive className="h-4 w-4 text-muted-foreground" />
          <div className="flex-1">
            <div className="flex justify-between mb-1">
              <span className="text-muted-foreground">RAM</span>
              <span className="font-mono">
                {resources.ramUsedGb.toFixed(1)}/{resources.ramTotalGb}GB
              </span>
            </div>
            <Progress 
              value={ramPercent} 
              className="h-1.5"
            />
          </div>
        </div>

        {/* Active Model */}
        <div className="flex items-center gap-2">
          <Server className="h-4 w-4 text-muted-foreground" />
          <span className="text-muted-foreground">Модель:</span>
          <Badge variant="secondary" className="font-mono text-xs">
            {activeModel || 'Не загружена'}
          </Badge>
        </div>

        {/* Queue */}
        {queueSize > 0 && (
          <div className="flex items-center gap-2">
            <Users className="h-4 w-4 text-muted-foreground" />
            <span className="text-muted-foreground">Очередь:</span>
            <Badge variant="outline">{queueSize}</Badge>
          </div>
        )}

        {/* MCP Servers */}
        <div className="flex items-center gap-2 ml-auto">
          {mcpServers.map((server) => (
            <Badge
              key={server.name}
              variant={server.status === 'connected' ? 'default' : 'secondary'}
              className="text-xs"
            >
              <span
                className={`mr-1.5 h-1.5 w-1.5 rounded-full ${
                  server.status === 'connected' ? 'bg-green-500' : 'bg-muted-foreground'
                }`}
              />
              {server.name}
            </Badge>
          ))}
        </div>
      </div>
    </div>
  );
}
