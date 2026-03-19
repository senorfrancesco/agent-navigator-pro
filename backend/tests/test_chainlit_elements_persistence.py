import importlib
import os
import sys
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.responses import FileResponse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


@pytest.fixture
def chainlit_elements_module():
    if "orchestrator.chainlit_app" in sys.modules:
        del sys.modules["orchestrator.chainlit_app"]
    module = importlib.import_module("orchestrator.chainlit_app")
    yield module
    sys.modules.pop("orchestrator.chainlit_app", None)


@pytest.mark.asyncio
async def test_local_chainlit_storage_provider_persists_and_deletes_files(tmp_path, chainlit_elements_module):
    chainlit_elements_module.UPLOADS_DIR = str(tmp_path / "uploads")
    provider = chainlit_elements_module._LocalChainlitStorageProvider()

    result = await provider.upload_file(
        object_key="user-1/element-1/test.txt",
        data=b"hello world",
        mime="text/plain",
    )

    expected_path = tmp_path / "uploads" / "chainlit-elements" / "user-1" / "element-1" / "test.txt"
    assert expected_path.exists()
    assert expected_path.read_bytes() == b"hello world"
    assert result["object_key"] == "user-1/element-1/test.txt"
    assert result["url"].endswith("/project/file/user-1/element-1/test.txt")

    deleted = await provider.delete_file("user-1/element-1/test.txt")

    assert deleted is True
    assert not expected_path.exists()


def test_resolve_chainlit_element_fs_path_rejects_parent_traversal(tmp_path, chainlit_elements_module):
    chainlit_elements_module.UPLOADS_DIR = str(tmp_path / "uploads")

    with pytest.raises(HTTPException) as excinfo:
        chainlit_elements_module._resolve_chainlit_element_fs_path("../etc/passwd")

    assert excinfo.value.status_code == 400
    assert excinfo.value.detail == "Invalid element path"


def test_chainlit_data_layer_factory_initializes_local_storage_provider(chainlit_elements_module, tmp_path):
    chainlit_elements_module.UPLOADS_DIR = str(tmp_path / "uploads")

    data_layer = chainlit_elements_module.get_data_layer()

    assert isinstance(data_layer.storage_provider, chainlit_elements_module._LocalChainlitStorageProvider)


@pytest.mark.asyncio
async def test_local_chainlit_element_file_route_enforces_user_prefix(tmp_path, chainlit_elements_module):
    chainlit_elements_module.UPLOADS_DIR = str(tmp_path / "uploads")
    provider = chainlit_elements_module._LocalChainlitStorageProvider()
    await provider.upload_file(
        object_key="user-1/element-1/test.txt",
        data=b"hello world",
        mime="text/plain",
    )

    response = await chainlit_elements_module._serve_local_chainlit_element_file(
        object_key="user-1/element-1/test.txt",
        current_user=SimpleNamespace(identifier="user-1"),
    )

    assert isinstance(response, FileResponse)
    assert str(response.path).endswith("/user-1/element-1/test.txt")

    with pytest.raises(HTTPException) as excinfo:
        await chainlit_elements_module._serve_local_chainlit_element_file(
            object_key="user-1/element-1/test.txt",
            current_user=SimpleNamespace(identifier="user-2"),
        )

    assert excinfo.value.status_code == 403
