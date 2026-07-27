"""Node that turns uploaded workflow-file references into usable content."""
from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from app.ingestion.extractor import get_extractor
from app.nodes.base import NodeType
from app.nodes.registry import NodeRegistry
from app.runtime.schema import WorkflowFileRef
from app.workflow.file_inputs import WorkflowFileInputError


class WorkflowFileLoaderInput(BaseModel):
    pass


class WorkflowFileLoaderConfig(BaseModel):
    # ``str`` allows a Builder/YAML template such as
    # "{{inputs.source_files}}" to pass compile-time validation. Runtime
    # template resolution replaces it with the actual ref/list before run().
    files: str | WorkflowFileRef | list[WorkflowFileRef]
    max_chars_per_file: int = Field(default=200_000, ge=1_000, le=2_000_000)
    fail_on_unreadable: bool = False


class LoadedWorkflowFile(BaseModel):
    file_id: str
    name: str
    category: str
    minio_key: str
    extracted_chars: int = 0
    truncated: bool = False
    status: str
    error: str | None = None


class WorkflowFileLoaderOutput(BaseModel):
    text: str
    files: list[LoadedWorkflowFile]
    image_files: list[dict[str, Any]]
    total_files: int
    text_file_count: int
    image_count: int


@NodeRegistry.register
class WorkflowFileLoader(NodeType):
    """Read PDF/Office/Markdown/code inputs while retaining images as refs."""

    type_name = "WorkflowFileLoader"
    description = (
        "Load uploaded workflow files. Extracts text from supported PDFs, "
        "DOCX, PPTX, Markdown, spreadsheets, and code; images remain stable "
        "object-storage references for multimodal nodes."
    )
    input_schema = WorkflowFileLoaderInput
    output_schema = WorkflowFileLoaderOutput
    config_schema = WorkflowFileLoaderConfig

    async def run(
        self,
        state: dict[str, Any],
        resolved_config: dict[str, Any],
    ) -> dict[str, Any]:
        config = WorkflowFileLoaderConfig.model_validate(resolved_config)
        if isinstance(config.files, str):
            raise WorkflowFileInputError(
                "WorkflowFileLoader.files did not resolve to uploaded file references"
            )
        refs = config.files if isinstance(config.files, list) else [config.files]
        store = self.services["object_store"]

        text_parts: list[str] = []
        loaded_files: list[LoadedWorkflowFile] = []
        image_files: list[dict[str, Any]] = []

        for ref in refs:
            if ref.category == "image":
                image_files.append(ref.model_dump())
                loaded_files.append(
                    LoadedWorkflowFile(
                        file_id=ref.file_id,
                        name=ref.name,
                        category=ref.category,
                        minio_key=ref.minio_key,
                        status="image_reference",
                    )
                )
                continue

            if not ref.parseable_text:
                message = (
                    f"No text extractor is available for {ref.extension}; "
                    "the file remains available by minio_key."
                )
                if config.fail_on_unreadable:
                    raise WorkflowFileInputError(message)
                loaded_files.append(
                    LoadedWorkflowFile(
                        file_id=ref.file_id,
                        name=ref.name,
                        category=ref.category,
                        minio_key=ref.minio_key,
                        status="unreadable",
                        error=message,
                    )
                )
                continue

            temporary_path: Path | None = None
            try:
                raw = await asyncio.to_thread(store.get_bytes, ref.minio_key)
                with tempfile.NamedTemporaryFile(
                    prefix="eurskem-file-loader-",
                    suffix=ref.extension,
                    delete=False,
                ) as temporary:
                    temporary.write(raw)
                    temporary_path = Path(temporary.name)

                extractor = get_extractor(temporary_path)
                document = await asyncio.to_thread(
                    extractor.extract,
                    temporary_path,
                )
                full_text = document.full_text
                truncated = len(full_text) > config.max_chars_per_file
                extracted = full_text[: config.max_chars_per_file]
                text_parts.append(f"--- {ref.name} ---\n{extracted}")
                loaded_files.append(
                    LoadedWorkflowFile(
                        file_id=ref.file_id,
                        name=ref.name,
                        category=ref.category,
                        minio_key=ref.minio_key,
                        extracted_chars=len(extracted),
                        truncated=truncated,
                        status="extracted",
                    )
                )
            except Exception as exc:
                if config.fail_on_unreadable:
                    raise
                loaded_files.append(
                    LoadedWorkflowFile(
                        file_id=ref.file_id,
                        name=ref.name,
                        category=ref.category,
                        minio_key=ref.minio_key,
                        status="failed",
                        error=str(exc),
                    )
                )
            finally:
                if temporary_path is not None:
                    temporary_path.unlink(missing_ok=True)

        text_file_count = sum(
            1 for item in loaded_files if item.status == "extracted"
        )
        return {
            "text": "\n\n".join(text_parts),
            "files": [item.model_dump() for item in loaded_files],
            "image_files": image_files,
            "total_files": len(refs),
            "text_file_count": text_file_count,
            "image_count": len(image_files),
        }
