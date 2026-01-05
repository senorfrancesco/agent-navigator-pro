import { AgentStep as Step } from '@/types/agent';
import { cn } from '@/lib/utils';
import { useState } from 'react';
import { Brain, Cog, FileText, ChevronDown, ChevronRight, Clock } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible';

interface AgentStepProps {
  step: Step;
  showJsonByDefault?: boolean;
}

const stepConfig = {
  thought: {
    icon: Brain,
    label: 'Thought',
    bgColor: 'bg-[hsl(var(--info))]/10',
    borderColor: 'border-[hsl(var(--info))]/30',
    iconColor: 'text-[hsl(var(--info))]',
  },
  action: {
    icon: Cog,
    label: 'Action',
    bgColor: 'bg-[hsl(var(--warning))]/10',
    borderColor: 'border-[hsl(var(--warning))]/30',
    iconColor: 'text-[hsl(var(--warning))]',
  },
  observation: {
    icon: FileText,
    label: 'Observation',
    bgColor: 'bg-[hsl(var(--success))]/10',
    borderColor: 'border-[hsl(var(--success))]/30',
    iconColor: 'text-[hsl(var(--success))]',
  },
};

export function AgentStepComponent({ step, showJsonByDefault = false }: AgentStepProps) {
  const [showJson, setShowJson] = useState(showJsonByDefault);
  const config = stepConfig[step.type];
  const Icon = config.icon;

  return (
    <div
      className={cn(
        'rounded-lg border p-3 transition-colors',
        config.bgColor,
        config.borderColor
      )}
    >
      <div className="flex items-start gap-3">
        <Icon className={cn('h-5 w-5 mt-0.5 shrink-0', config.iconColor)} />
        
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-1">
            <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
              {config.label}
            </span>
            
            {step.toolName && (
              <span className="rounded bg-secondary px-1.5 py-0.5 text-xs font-mono text-secondary-foreground">
                {step.toolName}
              </span>
            )}
            
            {step.duration !== undefined && (
              <span className="flex items-center gap-1 text-xs text-muted-foreground">
                <Clock className="h-3 w-3" />
                {step.duration.toFixed(2)}s
              </span>
            )}
          </div>

          <p className="text-sm text-foreground whitespace-pre-wrap break-words">
            {step.content}
          </p>

          {/* Tool parameters */}
          {step.toolParams && Object.keys(step.toolParams).length > 0 && (
            <div className="mt-2 rounded bg-secondary/50 p-2">
              <p className="text-xs font-medium text-muted-foreground mb-1">Параметры:</p>
              <pre className="text-xs text-foreground font-mono overflow-x-auto">
                {JSON.stringify(step.toolParams, null, 2)}
              </pre>
            </div>
          )}

          {/* Raw JSON toggle */}
          {step.rawJson && (
            <Collapsible open={showJson} onOpenChange={setShowJson} className="mt-2">
              <CollapsibleTrigger asChild>
                <Button variant="ghost" size="sm" className="h-6 gap-1 text-xs text-muted-foreground">
                  {showJson ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
                  JSON
                </Button>
              </CollapsibleTrigger>
              <CollapsibleContent>
                <pre className="mt-2 rounded bg-secondary p-2 text-xs font-mono text-foreground overflow-x-auto max-h-48 overflow-y-auto">
                  {JSON.stringify(step.rawJson, null, 2)}
                </pre>
              </CollapsibleContent>
            </Collapsible>
          )}
        </div>
      </div>
    </div>
  );
}
