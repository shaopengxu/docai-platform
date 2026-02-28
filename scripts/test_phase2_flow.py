import asyncio
import httpx
import uuid
import sys

API_BASE = "http://localhost:8000/api/v1"

async def main():
    print("Testing Phase 2 Flow...")
    async with httpx.AsyncClient() as client:
        # 1. Create a document group
        print("1. Creating Document Group...")
        group_name = "Test Group " + str(uuid.uuid4())[:6]
        res = await client.post(f"{API_BASE}/documents/groups", json={
            "name": group_name,
            "description": "A group for phase 2 testing"
        })
        if res.status_code != 201:
            print(f"Failed to create group: {res.text}")
            return
        
        group = res.json()
        group_id = group["group_id"]
        print(f"   Created group {group_name} with ID {group_id}")

        # 2. Get all documents
        print("2. Fetching existing documents...")
        res = await client.get(f"{API_BASE}/documents")
        docs = res.json().get("documents", [])
        print(f"   Found {len(docs)} documents.")

        if not docs:
            print("   Please upload some documents first for testing.")
            return

        # 3. Assign up to 2 docs to the group
        docs_to_group = docs[:2]
        print(f"3. Assigning {len(docs_to_group)} documents to the group...")
        for d in docs_to_group:
            res = await client.put(f"{API_BASE}/documents/{d['doc_id']}/metadata", json={
                "group_id": group_id,
                "tags": ["test_tag"]
            })
            if res.status_code == 200:
                print(f"   Assigned doc {d['title']} to group.")
            else:
                print(f"   Failed to assign doc {d['title']}: {res.text}")

        # 4. Test Query Router and Map-Reduce (using group_id)
        print("4. Testing Cross-Document Query...")
        query_payload = {
            "question": "总结这两份文档关于核心功能的主要内容，列出三个关键点。",
            "group_id": group_id,
            "top_k": 5
        }
        res = await client.post(f"{API_BASE}/query", json=query_payload)
        if res.status_code == 200:
            result = res.json()
            print("\n================== QUERY RESULT ==================")
            print(result["answer"])
            print("==================================================\n")
            print(f"Confidence: {result['confidence']}")
            print("Citations:")
            for cit in result["citations"]:
                print(f" - [{cit['doc_title']}] {cit['content_snippet'][:50]}...")
        else:
            print(f"Query failed: {res.text}")

if __name__ == "__main__":
    asyncio.run(main())
