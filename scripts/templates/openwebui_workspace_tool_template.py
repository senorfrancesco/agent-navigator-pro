"""
title: Example Workspace Tool
author: llm-tools-platform
version: 1.0.0
requirements:
"""

from pydantic import BaseModel


class Tools:
    class Valves(BaseModel):
        priority: int = 5

    def __init__(self) -> None:
        self.valves = self.Valves()

    async def example_action(self, value: str) -> str:
        """
        Replace this method with your real tool entrypoint.

        :param value: Example string argument passed by Open WebUI.
        :return: Replace with a deterministic tool result.
        """
        return f"EXAMPLE_TOOL_OK:{value.strip()}"
