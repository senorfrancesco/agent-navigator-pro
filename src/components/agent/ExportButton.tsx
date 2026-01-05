import { Button } from '@/components/ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { Download, FileJson, FileText } from 'lucide-react';
import { Chat } from '@/types/agent';
import { exportToJson, exportToMarkdown, downloadFile } from '@/lib/export';
import { toast } from '@/hooks/use-toast';

interface ExportButtonProps {
  chat: Chat | null;
}

export function ExportButton({ chat }: ExportButtonProps) {
  const handleExportJson = () => {
    if (!chat) {
      toast({
        title: 'Нет данных для экспорта',
        description: 'Начните чат, чтобы экспортировать его.',
        variant: 'destructive',
      });
      return;
    }
    
    const content = exportToJson(chat);
    const filename = `${chat.title.replace(/[^a-zA-Zа-яА-Я0-9]/g, '_')}_${Date.now()}.json`;
    downloadFile(content, filename, 'application/json');
    
    toast({
      title: 'Экспорт завершён',
      description: `Файл ${filename} сохранён.`,
    });
  };

  const handleExportMarkdown = () => {
    if (!chat) {
      toast({
        title: 'Нет данных для экспорта',
        description: 'Начните чат, чтобы экспортировать его.',
        variant: 'destructive',
      });
      return;
    }
    
    const content = exportToMarkdown(chat);
    const filename = `${chat.title.replace(/[^a-zA-Zа-яА-Я0-9]/g, '_')}_${Date.now()}.md`;
    downloadFile(content, filename, 'text/markdown');
    
    toast({
      title: 'Экспорт завершён',
      description: `Файл ${filename} сохранён.`,
    });
  };

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="sm" className="gap-2">
          <Download className="h-4 w-4" />
          <span className="hidden sm:inline">Экспорт</span>
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        <DropdownMenuItem onClick={handleExportJson}>
          <FileJson className="mr-2 h-4 w-4" />
          Экспорт в JSON
        </DropdownMenuItem>
        <DropdownMenuItem onClick={handleExportMarkdown}>
          <FileText className="mr-2 h-4 w-4" />
          Экспорт в Markdown
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
