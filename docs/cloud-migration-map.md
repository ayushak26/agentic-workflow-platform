# Cloud Migration Map

## Philosophy

Every component in Docker Compose maps to a managed cloud service.
The code does not change. The Docker image is the unit of deployment.

## Component Mapping

| Local (Docker Compose)  | AWS                          | Azure                        | GCP                          |
|-------------------------|------------------------------|------------------------------|------------------------------|
| FastAPI container       | ECS Fargate                  | Azure Container Apps         | Cloud Run                    |
| MongoDB                 | DocumentDB or Atlas          | Cosmos DB (Mongo API)        | Atlas on GCP                 |
| Weaviate                | Weaviate Cloud / EC2         | Weaviate Cloud / ACI         | Weaviate Cloud / GKE         |
| MinIO                   | S3 (zero code change)        | Azure Blob Storage           | GCS                          |
| Redis                   | ElastiCache (Redis)          | Azure Cache for Redis        | Memorystore                  |
| Prometheus              | CloudWatch + remote write    | Azure Monitor                | Cloud Monitoring             |
| Grafana                 | Managed Grafana              | Azure Managed Grafana        | Cloud Monitoring dashboards  |

## Why Each Choice

**ECS Fargate vs Container Apps vs Cloud Run**: All are serverless container platforms.
Cloud Run is cheapest for bursty workloads (pay-per-request). Container Apps is the
best fit if you're already Azure AD / Microsoft 365 (which Eurskem is). ECS Fargate
integrates best with AWS-native services (IAM, Secrets Manager, ALB).

**DocumentDB vs Cosmos DB**: DocumentDB is MongoDB-wire-compatible, easiest migration.
Cosmos DB adds global distribution but requires verifying API compatibility.

**MinIO → S3**: Zero code change. MinIO uses the S3 protocol. The only change is
pointing `S3_ENDPOINT_URL` at `https://s3.amazonaws.com`.

**Redis → ElastiCache**: Drop-in. Same redis-py client. Add TLS and AUTH token from
Secrets Manager.

## Migration Path (Local → Cloud, 3 Steps)

1. Push Docker images to ECR / ACR / Artifact Registry.
2. Replace Docker Compose service URLs with managed service endpoints in `.env`.
3. Add IAM roles / Managed Identity so containers authenticate to managed services without credentials in code.

No code changes. Config change only.

## Interview Questions Per Component

### FastAPI → ECS Fargate
- Why Fargate over EC2? No instance management, pay per task-second, auto-scaling via Application Auto Scaling.
- How does your container get secrets? IAM role → Secrets Manager → injected as env vars at task start.

### Weaviate → Weaviate Cloud
- Why not just use Pinecone or OpenSearch? Weaviate was chosen because it supports hybrid BM25+vector in one query without a second service.
- What changes at scale? Single-node Weaviate → Weaviate cluster with replication factor 2.

### MongoDB → DocumentDB
- Wire-compatible means my code doesn't change. The risk is that DocumentDB doesn't implement 100% of the MongoDB API — check aggregation pipeline operators before migration.