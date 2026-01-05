/**
 * Resource Monitor Component
 * Displays VRAM, RAM, CPU usage, CUDA info, and GPU stats
 */

import { MCPServerStatus } from '@/types/agent';
import { ResourceStatus } from '@/hooks/useAgentConnection';
import { Badge } from '@/components/ui/badge';
import { Progress } from '@/components/ui/progress';
import { Cpu, HardDrive, Server, Users, Thermometer, Zap } from 'lucide-react';
import { cn } from '@/lib/utils';

interface ResourceMonitorProps {
  resources: ResourceStatus;
  mcpServers: MCPServerStatus[];
}

export function ResourceMonitor({
  resources,
  mcpServers,
}: ResourceMonitorProps) {
  const vramPercent = resources.vramTotalGb > 0 
    ? (resources.vramUsedGb / resources.vramTotalGb) * 100 
    : 0;

  const getProgressVariant = (percent: number) => {
    if (percent > 90) return 'bg-destructive';
    if (percent > 70) return 'bg-yellow-500';
    return 'bg-primary';
  };

  return (
    <div className="border-b border-border bg-muted/30 px-4 py-3">
      <div className="flex flex-wrap items-center gap-4 text-sm">
        {/* CUDA Status */}
        <div className="flex items-center gap-2">
          <Zap className={cn(
            "h-4 w-4",
            resources.cudaAvailable ? "text-green-500" : "text-muted-foreground"
          )} />
          <span className="text-muted-foreground">CUDA:</span>
          {resources.cudaAvailable ? (
            <Badge variant="default" className="text-xs">
              {resources.cudaVersion || 'OK'}
            </Badge>
          ) : (
            <Badge variant="secondary" className="text-xs">
              Недоступен
            </Badge>
          )}
        </div>

        {/* VRAM (only if GPU available) */}
        {resources.vramTotalGb > 0 && (
          <div className="flex items-center gap-2 min-w-[160px]">
            <Cpu className="h-4 w-4 text-muted-foreground" />
            <div className="flex-1">
              <div className="flex justify-between mb-1">
                <span className="text-muted-foreground">VRAM</span>
                <span className="font-mono text-xs">
                  {resources.vramUsedGb.toFixed(1)}/{resources.vramTotalGb.toFixed(0)}GB
                </span>
              </div>
              <Progress value={vramPercent} className="h-1.5" />
            </div>
          </div>
        )}

        {/* RAM */}
        <div className="flex items-center gap-2 min-w-[160px]">
          <HardDrive className="h-4 w-4 text-muted-foreground" />
          <div className="flex-1">
            <div className="flex justify-between mb-1">
              <span className="text-muted-foreground">RAM</span>
              <span className="font-mono text-xs">
                {resources.ramUsedGb.toFixed(1)}/{resources.ramTotalGb.toFixed(0)}GB
              </span>
            </div>
            <Progress value={resources.ramPercent} className="h-1.5" />
          </div>
        </div>

        {/* CPU */}
        <div className="flex items-center gap-2">
          <span className="text-muted-foreground">CPU:</span>
          <Badge variant="outline" className="font-mono text-xs">
            {resources.cpuPercent.toFixed(0)}%
          </Badge>
          <span className="text-xs text-muted-foreground">
            ({resources.cpuCount} cores)
          </span>
        </div>

        {/* GPU Temperature */}
        {resources.gpuTemperature !== null && (
          <div className="flex items-center gap-2">
            <Thermometer className={cn(
              "h-4 w-4",
              resources.gpuTemperature > 80 ? "text-destructive" : 
              resources.gpuTemperature > 60 ? "text-yellow-500" : "text-muted-foreground"
            )} />
            <span className="font-mono text-xs">{resources.gpuTemperature}°C</span>
          </div>
        )}

        {/* GPU Utilization */}
        {resources.gpuUtilization !== null && (
          <div className="flex items-center gap-2">
            <span className="text-muted-foreground">GPU:</span>
            <Badge 
              variant={resources.gpuUtilization > 80 ? "destructive" : "outline"} 
              className="font-mono text-xs"
            >
              {resources.gpuUtilization}%
            </Badge>
          </div>
        )}

        {/* Active Model */}
        <div className="flex items-center gap-2">
          <Server className="h-4 w-4 text-muted-foreground" />
          <span className="text-muted-foreground">Модель:</span>
          <Badge variant="secondary" className="font-mono text-xs">
            {resources.activeModel || 'Не загружена'}
          </Badge>
        </div>

        {/* Queue */}
        {resources.queueSize > 0 && (
          <div className="flex items-center gap-2">
            <Users className="h-4 w-4 text-muted-foreground" />
            <span className="text-muted-foreground">Очередь:</span>
            <Badge variant="outline">{resources.queueSize}</Badge>
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
                className={cn(
                  "mr-1.5 h-1.5 w-1.5 rounded-full",
                  server.status === 'connected' ? 'bg-green-500' : 'bg-muted-foreground'
                )}
              />
              {server.name}
            </Badge>
          ))}
        </div>
      </div>

      {/* Driver info (smaller) */}
      {resources.driverVersion && (
        <div className="mt-2 text-xs text-muted-foreground">
          Driver: {resources.driverVersion}
        </div>
      )}
    </div>
  );
}
