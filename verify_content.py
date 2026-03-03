import asyncio
import os
import sys
import json

sys.path.insert(0, os.path.join(os.getcwd(), "backend"))

from orchestrator.workflows.document_analysis import create_analysis_graph
from orchestrator.shared.http_client import AsyncHttpClient

async def verify():
    path = "/home/seral/HDD/proj/dev_1_conda/documents/f5jsglrkyfe8p3ogd02g405d9q3r9lfn.pdf"
    app = create_analysis_graph()
    
    state = {
        "input_path": path,
        "doc_name": "test.pdf",
        "doc_type": "",
        "doc_metadata": {},
        "full_text": "",
        "items": [],
        "summary": "",
        "final_report": "",
        "errors": []
    }

    print("--- Running extraction with LLM Polisher ---")
    async for event in app.astream(state):
        for node_name, output in event.items():
            if node_name == "extract":
                items = output.get("items", [])
                print(f"Total items extracted: {len(items)}")
                
                if items:
                    server = items[0]
                    print("\nITEM 1 ANALYSIS (Polished):")
                    print(f"Name: {server.get('name')}")
                    print(f"Specs:\n{server.get('specs', '')}")
                    
                    specs = server.get('specs', '').lower()
                    print("\nKeywords Found by LLM:")
                    for kw in ["tpm", "блоки питания", "pcie", "rack", "юнит"]:
                        status = "✅ YES" if kw in specs else "❌ NO"
                        print(f"  - {kw}: {status}")

    await AsyncHttpClient.close_client()

if __name__ == "__main__":
    asyncio.run(verify())
