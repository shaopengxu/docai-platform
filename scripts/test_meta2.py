import asyncio
from app.core.infrastructure import get_es_client, get_qdrant_client
from config.settings import settings

async def main():
    try:
        es = get_es_client()
        qdrant = get_qdrant_client()
        
        es_res = await es.search(index=settings.es_index_name, body={"query": {"match_all": {}}}, size=1)
        if es_res['hits']['hits']:
            print("ES Example:", es_res['hits']['hits'][0]['_source'])
        
        qd_res = await qdrant.scroll(collection_name=settings.qdrant_collection_name, limit=1)
        if qd_res[0]:
            print("Qdrant Example:", qd_res[0][0].payload)
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(main())
