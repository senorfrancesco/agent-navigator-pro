import { useState, useEffect, useCallback } from 'react';
import { Chat, Message } from '@/types/agent';

const CHATS_KEY = 'react-agent-chats';

function generateId() {
  return Math.random().toString(36).substring(2, 15);
}

export function useChatHistory() {
  const [chats, setChats] = useState<Chat[]>(() => {
    const stored = localStorage.getItem(CHATS_KEY);
    if (stored) {
      try {
        return JSON.parse(stored);
      } catch {
        return [];
      }
    }
    return [];
  });

  const [activeChatId, setActiveChatId] = useState<string | null>(() => {
    return chats.length > 0 ? chats[0].id : null;
  });

  useEffect(() => {
    localStorage.setItem(CHATS_KEY, JSON.stringify(chats));
  }, [chats]);

  const activeChat = chats.find(c => c.id === activeChatId) || null;

  const createNewChat = useCallback(() => {
    const newChat: Chat = {
      id: generateId(),
      title: 'Новый чат',
      messages: [],
      createdAt: Date.now(),
      updatedAt: Date.now(),
    };
    setChats(prev => [newChat, ...prev]);
    setActiveChatId(newChat.id);
    return newChat;
  }, []);

  const deleteChat = useCallback((chatId: string) => {
    setChats(prev => {
      const filtered = prev.filter(c => c.id !== chatId);
      if (activeChatId === chatId) {
        setActiveChatId(filtered.length > 0 ? filtered[0].id : null);
      }
      return filtered;
    });
  }, [activeChatId]);

  const addMessage = useCallback((message: Omit<Message, 'id' | 'timestamp'>) => {
    const newMessage: Message = {
      ...message,
      id: generateId(),
      timestamp: Date.now(),
    };

    setChats(prev => {
      let chatId = activeChatId;
      let chatExists = prev.some(c => c.id === chatId);

      if (!chatExists) {
        const newChat: Chat = {
          id: generateId(),
          title: message.content.slice(0, 30) + (message.content.length > 30 ? '...' : ''),
          messages: [newMessage],
          createdAt: Date.now(),
          updatedAt: Date.now(),
        };
        setActiveChatId(newChat.id);
        return [newChat, ...prev];
      }

      return prev.map(chat => {
        if (chat.id === chatId) {
          const updatedChat = {
            ...chat,
            messages: [...chat.messages, newMessage],
            updatedAt: Date.now(),
          };
          // Update title from first user message
          if (chat.messages.length === 0 && message.role === 'user') {
            updatedChat.title = message.content.slice(0, 30) + (message.content.length > 30 ? '...' : '');
          }
          return updatedChat;
        }
        return chat;
      });
    });

    return newMessage;
  }, [activeChatId]);

  const updateMessage = useCallback((messageId: string, updates: Partial<Message>) => {
    setChats(prev =>
      prev.map(chat => ({
        ...chat,
        messages: chat.messages.map(msg =>
          msg.id === messageId ? { ...msg, ...updates } : msg
        ),
      }))
    );
  }, []);

  const clearAllChats = useCallback(() => {
    setChats([]);
    setActiveChatId(null);
  }, []);

  return {
    chats,
    activeChat,
    activeChatId,
    setActiveChatId,
    createNewChat,
    deleteChat,
    addMessage,
    updateMessage,
    clearAllChats,
  };
}
