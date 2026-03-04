"""
Тесты для MCP Legal Server.

Покрытие:
- backward-compatible alias /batch_match
"""

import os
import sys
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.legal_server import mcp_legal_server
from services.legal_server.mcp_legal_server import MatchBatchesRequest, MatchBatchesResponse


@pytest.mark.asyncio
async def test_batch_match_alias_delegates_to_shared_impl():
    request = MatchBatchesRequest(list_old=["a"], list_new=["b"], threshold=0.5)
    expected = MatchBatchesResponse(status="success", matches=[{"type": "MODIFIED"}])

    with patch.object(mcp_legal_server, "_match_batches_impl", new_callable=AsyncMock) as mock_impl:
        mock_impl.return_value = expected

        result = await mcp_legal_server.batch_match(request)

    mock_impl.assert_awaited_once_with(request)
    assert result == expected
