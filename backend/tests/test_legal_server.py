"""
Тесты для MCP Legal Server.

Покрытие:
- backward-compatible alias /batch_match
"""

import os
import sys
from unittest.mock import AsyncMock, patch

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.legal_server import mcp_legal_server
from services.legal_server.mcp_legal_server import (
    MatchBatchesRequest,
    MatchBatchesResponse,
    _build_final_score_matrix,
)


@pytest.mark.asyncio
async def test_batch_match_alias_delegates_to_shared_impl():
    request = MatchBatchesRequest(list_old=["a"], list_new=["b"], threshold=0.5)
    expected = MatchBatchesResponse(status="success", matches=[{"type": "MODIFIED"}])

    with patch.object(mcp_legal_server, "_match_batches_impl", new_callable=AsyncMock) as mock_impl:
        mock_impl.return_value = expected

        result = await mcp_legal_server.batch_match(request)

    mock_impl.assert_awaited_once_with(request)
    assert result == expected


def test_build_final_score_matrix_prefers_structurally_consistent_candidate():
    list_old = ["Коммутатор Cisco C9300 48 портов 4 шт"]
    list_new = [
        "Коммутатор Cisco C9200 48 портов 4 шт",
        "Коммутатор Cisco C9300 48 портов 4 шт",
    ]
    dense_scores = np.array([[0.93, 0.91]], dtype=float)
    candidate_map = {0: [0, 1]}

    final_scores, pair_metadata = _build_final_score_matrix(
        list_old,
        list_new,
        dense_scores,
        candidate_map,
    )

    assert final_scores[0, 1] > final_scores[0, 0]
    assert pair_metadata[(0, 1)]["rerank_score"] > pair_metadata[(0, 0)]["rerank_score"]
    assert pair_metadata[(0, 1)]["lexical_overlap_score"] >= pair_metadata[(0, 0)]["lexical_overlap_score"]


@pytest.mark.asyncio
async def test_match_batches_reports_unmatched_old_rows_as_deleted():
    request = MatchBatchesRequest(
        list_old=["Позиция 1", "Позиция 2", "Позиция 3"],
        list_new=["Позиция 1"],
        threshold=0.72,
    )

    embedding_map = {
        "Позиция 1": [1.0, 0.0, 0.0],
        "Позиция 2": [0.0, 1.0, 0.0],
        "Позиция 3": [0.0, 0.0, 1.0],
    }

    def fake_infer(model: str, payload: dict, device_mode: str = "cpu"):
        texts = payload["input"]
        return {
            "data": [
                {"embedding": embedding_map[text]}
                for text in texts
            ]
        }

    with patch.object(mcp_legal_server.ums_client, "infer", side_effect=fake_infer):
        result = await mcp_legal_server._match_batches_impl(request)

    assert result.status == "success"
    assert sum(1 for item in result.matches if item["type"] == "DELETED") == 2
    assert any(
        item["type"] in {"MODIFIED", "UNCHANGED"} and item["old_text"] == "Позиция 1"
        for item in result.matches
    )
