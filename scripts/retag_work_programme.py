# scripts/retag_work_programme.py
"""Retag work programme module.

Part of the operational and development scripts.
"""
import weaviate
client = weaviate.connect_to_local()   # match your app's client config
coll = client.collections.get("DocumentChunk")   # <-- confirm collection name
from weaviate.classes.query import Filter
n = 0
for obj in coll.iterator(return_properties=["doc_type"]):
    if obj.properties.get("doc_type") == "work_programme":
        coll.data.update(uuid=obj.uuid, properties={"doc_type": "annual_work_programme"})
        n += 1
print(f"retagged {n} chunks")
client.close()