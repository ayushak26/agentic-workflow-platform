import app.nodes  # noqa: F401
from app.nodes.registry import NodeRegistry


class StubObjectStore:
    def __init__(self):
        self.blobs = {}
        self.puts = []
    def get_bytes(self, key): return self.blobs[key]
    def put_bytes(self, data, key, content_type=None):
        self.puts.append((key, data, content_type))
        self.blobs[key] = data


async def test_powerpoint_produces_pptx_with_one_slide_per_section():
    store = StubObjectStore()
    cls = NodeRegistry.get("PowerPointProposalSlides")
    node = cls(
        node_id="p",
        raw_config={
            "sections": {
                "Intro": "Welcome",
                "Approach": "Our method",
                "Pricing": "$$$",
            },
            "proposal_title": "Demo",
            "client_name": "Proudfoot",
        },
        services={"object_store": store},
    )
    result = await node.run(
        state={"inputs": {"SYSTEM.run_id": "rp"}},
        resolved_config=node.config.model_dump(),
    )
    assert result["slide_count"] == 4  # 3 sections + 1 title slide
    # PPTX files are ZIP archives
    assert store.blobs[result["minio_key"]].startswith(b"PK")