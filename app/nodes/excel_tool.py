"""Excel tool node — pre-baked variant: extract tables from .xlsx in MinIO."""
from __future__ import annotations
import asyncio
from typing import Any

from pydantic import BaseModel, Field

from app.nodes.base import NodeType
from app.nodes.registry import NodeRegistry
from app.observability.logging import get_logger
from app.tools.excel_io import read_tables_from_xlsx

log = get_logger(__name__)


class ExcelInput(BaseModel):
    """Pydantic model defining the ExcelInput shape."""
    pass


class ExcelConfig(BaseModel):
    """Pydantic model defining the ExcelConfig shape.

    Attributes:
        minio_key (str).
    """
    minio_key: str   # templated; the upstream node provides this


class ExcelOutput(BaseModel):
    """Pydantic model defining the ExcelOutput shape.

    Attributes:
        tables (dict[str, list[list[Any]]]).
        sheet_count (int).
        total_rows (int).
    """
    tables: dict[str, list[list[Any]]]
    sheet_count: int
    total_rows: int


@NodeRegistry.register
class ExcelTableExtractor(NodeType):
    """Workflow node type implementing the ExcelTableExtractor capability."""
    type_name = "ExcelTableExtractor"
    description = "Extract tables from an .xlsx in object storage."
    input_schema = ExcelInput
    output_schema = ExcelOutput
    config_schema = ExcelConfig

    @classmethod
    def required_services(cls, config: dict[str, Any]) -> set[str]:
        """Compute the required services.

        Args:
            config (dict[str, Any]): Node configuration mapping.

        Returns:
            set[str]: The services.
        """
        return {"object_store"}

    async def run(self, state, resolved_config: dict[str, Any]) -> dict[str, Any]:
        """Run the result.

        Args:
            state: Current workflow state.
            resolved_config (dict[str, Any]): Configuration after template resolution.

        Returns:
            dict[str, Any]: The result.
        """
        store = self.services["object_store"]
        cfg = ExcelConfig(**resolved_config)

        # boto3 is sync — push to a thread to keep the event loop free
        raw = await asyncio.to_thread(store.get_bytes, cfg.minio_key)
        tables = await asyncio.to_thread(read_tables_from_xlsx, raw)

        total = sum(len(rows) for rows in tables.values())
        log.info("excel.extracted",
                 node_id=self.node_id, minio_key=cfg.minio_key,
                 sheets=len(tables), total_rows=total)
        return {
            "tables": tables,
            "sheet_count": len(tables),
            "total_rows": total,
        }