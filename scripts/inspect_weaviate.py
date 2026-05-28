"""Inspect what's actually indexed in Weaviate.

Useful sanity check before running retrieval — tells you what fields each
chunk has, what session_ids exist, and what doc_types are represented.
The retrieval smoke test queries need to match the data that's actually
there, so we look first.
"""
import weaviate
from app.config import settings


def main():
    client = weaviate.connect_to_local(host="weaviate", port=8080)
    try:
        coll = client.collections.get(settings.weaviate_collection)

        # Schema — what properties exist?
        config = coll.config.get()
        print("=== Properties ===")
        for prop in config.properties:
            print(f"  {prop.name}: {prop.data_type}")
        print()

        # Total count
        agg = coll.aggregate.over_all(total_count=True)
        print(f"=== Total objects: {agg.total_count} ===\n")

        # Sample 3 objects
        print("=== Sample 3 chunks ===")
        for i, obj in enumerate(coll.iterator(return_properties=None), start=1):
            if i > 3:
                break
            print(f"\n--- Chunk {i} ---")
            for k, v in obj.properties.items():
                val = str(v)
                if len(val) > 200:
                    val = val[:200] + "..."
                print(f"  {k}: {val}")

    finally:
        client.close()


if __name__ == "__main__":
    main()