"""
title: Community Sum Tool
author: Agent Navigator
version: 1.0.0
requirements:
"""

from pydantic import BaseModel


class Tools:
    class Valves(BaseModel):
        priority: int = 5

    def __init__(self) -> None:
        self.valves = self.Valves()

    async def sum_two_numbers(self, a: int, b: int) -> str:
        """
        Deterministically adds two integers and returns a stable marker.

        :param a: First integer.
        :param b: Second integer.
        :return: Exact string COMMUNITY_TOOL_OK:<sum>
        """
        return f"COMMUNITY_TOOL_OK:{int(a) + int(b)}"
