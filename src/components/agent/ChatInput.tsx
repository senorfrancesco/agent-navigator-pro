import { useState, useRef, useCallback } from 'react';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import { Send, Paperclip, X } from 'lucide-react';
import { FileAttachment } from '@/types/agent';
import { cn } from '@/lib/utils';

interface ChatInputProps {
  onSend: (message: string, attachments: FileAttachment[]) => void;
  disabled?: boolean;
  placeholder?: string;
}

function generateId() {
  return Math.random().toString(36).substring(2, 15);
}

export function ChatInput({ onSend, disabled = false, placeholder = 'Введите сообщение...' }: ChatInputProps) {
  const [message, setMessage] = useState('');
  const [attachments, setAttachments] = useState<FileAttachment[]>([]);
  const [isDragging, setIsDragging] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const handleSubmit = useCallback(() => {
    if (!message.trim() && attachments.length === 0) return;
    onSend(message.trim(), attachments);
    setMessage('');
    setAttachments([]);
  }, [message, attachments, onSend]);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSubmit();
    }
  };

  const handleFileSelect = (files: FileList | null) => {
    if (!files) return;
    const newAttachments: FileAttachment[] = Array.from(files).map(file => ({
      id: generateId(),
      name: file.name,
      path: URL.createObjectURL(file),
      size: file.size,
      type: file.type,
    }));
    setAttachments(prev => [...prev, ...newAttachments]);
  };

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(true);
  };

  const handleDragLeave = () => {
    setIsDragging(false);
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(false);
    handleFileSelect(e.dataTransfer.files);
  };

  const removeAttachment = (id: string) => {
    setAttachments(prev => prev.filter(a => a.id !== id));
  };

  return (
    <div
      className={cn(
        'border-t border-border bg-card p-4 transition-colors',
        isDragging && 'bg-accent/10 border-accent'
      )}
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
    >
      {/* Attachments preview */}
      {attachments.length > 0 && (
        <div className="flex flex-wrap gap-2 mb-3">
          {attachments.map(file => (
            <div
              key={file.id}
              className="flex items-center gap-2 rounded-lg bg-secondary px-3 py-1.5 text-sm"
            >
              <Paperclip className="h-3 w-3" />
              <span className="truncate max-w-[150px]">{file.name}</span>
              <Button
                variant="ghost"
                size="icon"
                className="h-4 w-4 p-0"
                onClick={() => removeAttachment(file.id)}
              >
                <X className="h-3 w-3" />
              </Button>
            </div>
          ))}
        </div>
      )}

      {/* Drag overlay */}
      {isDragging && (
        <div className="mb-3 rounded-lg border-2 border-dashed border-accent bg-accent/5 p-4 text-center">
          <p className="text-sm text-muted-foreground">Отпустите файлы здесь</p>
        </div>
      )}

      {/* Input area */}
      <div className="flex gap-3">
        <input
          type="file"
          ref={fileInputRef}
          className="hidden"
          multiple
          onChange={(e) => handleFileSelect(e.target.files)}
        />
        
        <Button
          variant="ghost"
          size="icon"
          onClick={() => fileInputRef.current?.click()}
          disabled={disabled}
        >
          <Paperclip className="h-5 w-5" />
        </Button>

        <Textarea
          value={message}
          onChange={(e) => setMessage(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={placeholder}
          disabled={disabled}
          className="min-h-[44px] max-h-32 resize-none"
          rows={1}
        />

        <Button
          onClick={handleSubmit}
          disabled={disabled || (!message.trim() && attachments.length === 0)}
          size="icon"
        >
          <Send className="h-5 w-5" />
        </Button>
      </div>
    </div>
  );
}
