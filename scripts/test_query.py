import asyncio
import httpx

async def main():
    print("Testing Query Router...")
    async with httpx.AsyncClient(timeout=60.0) as client:
        res = await client.post("http://localhost:8000/api/v1/query", json={
            "question": "总结这两份文档关于核心功能的主要内容，列出三个关键点。",
            "top_k": 5
        })
        print(f"Status: {res.status_code}")
        print(res.text[:500])

if __name__ == "__main__":
    asyncio.run(main())
