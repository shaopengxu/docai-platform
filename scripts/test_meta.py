import asyncio
import os
from sqlalchemy import text
from app.core.infrastructure import get_db_session

async def main():
    async with get_db_session() as session:
        res = await session.execute(text("SELECT doc_id, group_id, tags FROM documents limit 2"))
        rows = res.fetchall()
        print(rows)

if __name__ == "__main__":
    asyncio.run(main())
