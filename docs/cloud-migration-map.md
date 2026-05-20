# Cloud Migration Map

The local Docker Compose stack maps cleanly to AWS, Azure, and GCP managed services. This document explains the mapping for each component, the cost model, the migration path, and the interview questions likely to come up per cloud.

Filled out fully in Phase 11. The interactive UI version ships in the Operator Console.

## Component mapping

| Compose service | AWS                            | Azure                            | GCP                               |
|-----------------|--------------------------------|----------------------------------|-----------------------------------|
| app             | ECS Fargate task               | Container Apps revision          | Cloud Run service                 |
| mongo           | DocumentDB / Atlas             | Cosmos DB (Mongo API)            | Atlas on GCP                      |
| redis           | ElastiCache                    | Azure Cache for Redis            | Memorystore                       |
| weaviate        | Weaviate Cloud or self-hosted  | Weaviate Cloud or AKS            | Weaviate Cloud or GKE             |
| minio           | S3 (drop-in)                   | Blob Storage (S3 gateway)        | GCS (S3 interop)                  |
| prometheus      | AMP (Managed Prometheus)       | Azure Monitor Managed Prometheus | Managed Service for Prometheus    |
| grafana         | Amazon Managed Grafana         | Azure Managed Grafana            | Self-hosted on GKE                |

Default cloud target documented as AWS ECS Fargate. Azure and GCP are documented for portability and interview defense.
