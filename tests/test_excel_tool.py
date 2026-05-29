import app.nodes  # noqa: F401
from io import BytesIO
import openpyxl
from app.nodes.registry import NodeRegistry


class StubObjectStore:
    def __init__(self, blobs: dict[str, bytes]):
        self.blobs = blobs
        self.puts: list[tuple[str, bytes, str | None]] = []

    def get_bytes(self, key: str) -> bytes:
        return self.blobs[key]

    def put_bytes(self, data: bytes, key: str, content_type: str | None = None):
        self.puts.append((key, data, content_type))


def _make_xlsx() -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Financials"
    ws.append(["Year", "Revenue", "Cost"])
    ws.append([2023, 100, 70])
    ws.append([2024, 120, 80])
    buf = BytesIO(); wb.save(buf); return buf.getvalue()


async def test_excel_extractor_returns_tables():
    store = StubObjectStore({"input.xlsx": _make_xlsx()})

    cls = NodeRegistry.get("ExcelTableExtractor")
    node = cls(
        node_id="e",
        raw_config={"minio_key": "input.xlsx"},
        services={"object_store": store},
    )
    result = await node.run(state={}, resolved_config=node.config.model_dump())
    assert result["sheet_count"] == 1
    assert result["total_rows"] == 3
    assert result["tables"]["Financials"][0] == ["Year", "Revenue", "Cost"]
    assert result["tables"]["Financials"][1] == [2023, 100, 70]