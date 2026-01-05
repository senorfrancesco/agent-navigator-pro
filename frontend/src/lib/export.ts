import { Chat, Message, AgentStep } from '@/types/agent';

export function exportToJson(chat: Chat): string {
  return JSON.stringify(chat, null, 2);
}

export function exportToMarkdown(chat: Chat): string {
  let md = `# ${chat.title}\n\n`;
  md += `Создано: ${new Date(chat.createdAt).toLocaleString('ru-RU')}\n\n`;
  md += `---\n\n`;

  for (const message of chat.messages) {
    const time = new Date(message.timestamp).toLocaleTimeString('ru-RU', {
      hour: '2-digit',
      minute: '2-digit',
    });
    
    const role = message.role === 'user' ? '👤 Вы' : '🤖 Агент';
    md += `## ${role} (${time})\n\n`;
    md += `${message.content}\n\n`;

    if (message.steps && message.steps.length > 0) {
      md += `### Процесс мышления\n\n`;
      for (const step of message.steps) {
        const icon = step.type === 'thought' ? '🧠' : step.type === 'action' ? '⚙️' : '📋';
        const label = step.type.charAt(0).toUpperCase() + step.type.slice(1);
        const duration = step.duration ? ` (${step.duration.toFixed(2)}s)` : '';
        
        md += `**${icon} ${label}${duration}**\n\n`;
        
        if (step.toolName) {
          md += `Tool: \`${step.toolName}\`\n\n`;
        }
        
        md += `${step.content}\n\n`;
        
        if (step.toolParams && Object.keys(step.toolParams).length > 0) {
          md += `Параметры:\n\`\`\`json\n${JSON.stringify(step.toolParams, null, 2)}\n\`\`\`\n\n`;
        }
      }
    }
    
    md += `---\n\n`;
  }

  return md;
}

export function downloadFile(content: string, filename: string, type: string) {
  const blob = new Blob([content], { type });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}
