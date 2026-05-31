from fastapi import APIRouter, Request

router = APIRouter(tags=["health"])


@router.get("/health")
async def health(request: Request):
    services = request.app.state.services
    return {
        "status": "ok",
        "services": {
            "mongo": "connected" if services.get("mongo") else "unavailable",
            "weaviate": "connected" if services.get("weaviate_client") else "unavailable",
            "minio": "connected" if services.get("object_store") else "unavailable",
            "redis": "connected" if services.get("redis") else "unavailable",
        },
    }