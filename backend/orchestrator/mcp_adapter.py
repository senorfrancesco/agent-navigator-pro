"""
MCP Adapter - Интеграция Multi-Server MCP Client для работы с внешними инструментами.
Использует langchain-mcp-adapters для подключения к MCP-серверам.
"""

import asyncio
import os
import json
from typing import List, Dict, Any, Optional
from langchain_mcp_adapters.client import MultiServerMCPClient
from langgraph.prebuilt import create_react_agent
from langchain_core.messages import HumanMessage

class MCPManager:
    def __init__(self):
        self.client = None
        self.servers = {
            "document_server": os.getenv("DOC_SERVER_URL", "http://localhost:8001"),
            "legal_server": os.getenv("LEGAL_SERVER_URL", "http://localhost:8002")
        }

    async def initialize(self):
        """Инициализация клиента и подключение к серверам."""
        if self.client:
            return
        
        # В реальной среде здесь будет подключение к реальным MCP серверам
        # Для демонстрации и совместимости с текущей архитектурой используем адаптер
        try:
            # Пример инициализации MultiServerMCPClient (требует запущенных серверов)
            # self.client = MultiServerMCPClient(self.servers)
            # await self.client.connect()
            pass
        except Exception as e:
            print(f"[MCP] Ошибка инициализации: {e}")

    async def get_tools(self):
        """Возвращает список инструментов от всех подключенных MCP серверов."""
        if not self.client:
            return []
        return self.client.get_tools()

mcp_manager = MCPManager()

async def run_mcp_agent(query: str, model, session_id: str):
    """Запуск агента с использованием MCP инструментов."""
    await mcp_manager.initialize()
    tools = await mcp_manager.get_tools()
    
    # Если инструментов нет, используем базовый ReAct
    if not tools:
        return None
        
    agent = create_react_agent(model, tools)
    result = await agent.ainvoke({"messages": [HumanMessage(content=query)]})
    return result["messages"][-1].content
