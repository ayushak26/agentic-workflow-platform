# Book Citations (Interview-Ready Concept Summaries)

All citations are concept-level. No page numbers.

## AI Engineering (Chip Huyen)

**Chapter 3-4 — Evaluation**
The core idea is that model quality degrades silently without a systematic evaluation harness.
Chip Huyen argues for golden sets with human-authored expected outputs and automatic scoring.
This is why Phase 10B built the LLM-as-a-Judge evaluator with pinned judge model and prompt version:
judge drift is as real as model drift.
Interview line: "Chip Huyen's evaluation chapter convinced me to pin the judge model and prompt version
in the Scorecard schema — otherwise cross-run comparisons are noise."

**Chapter 6 — Agents**
Agent = LLM + tool use + memory + planning loop. The key insight is that the agent loop
(observe → plan → act → observe) is simple in theory but dangerous in production without
bounded iteration and human checkpoints.
This is why MCPAgent has a `max_iterations` cap and HumanInLoopAgent uses LangGraph interrupt().
Interview line: "Huyen's agent chapter is where I got the discipline to cap MCPAgent iterations
and treat HITL not as a nice-to-have but as a safety boundary."

**Chapter 10 — Reference Architecture**
Foundation model applications have a standard shape: data ingestion → vector store →
retrieval → generation → evaluation. This platform implements that shape as a composable
node system so the architecture is reusable across use cases.
Interview line: "The platform follows the Chapter 10 reference pattern — ingestion, retrieval,
generation, and evaluation are separate concerns with clean interfaces."

## Designing Machine Learning Systems (Chip Huyen)

**Config-Driven Pipelines**
The argument is that code should be generic; business logic should be configuration.
This is why workflows are YAML files compiled at runtime into LangGraph graphs,
not hardcoded Python pipelines.
Interview line: "DMLS's config-driven principle is why the workflow is YAML, not code —
adding a new workflow is a YAML file, not a code change."

## Practical MLOps (Noah Gift)

**Chapter 1 — Hierarchy of Needs**
Before you optimize, you need reproducibility. Before reproducibility, you need automation.
Before automation, you need working code. Docker Compose is the automation layer
that gives us reproducibility before we ever touch a cloud.
Interview line: "Noah Gift's hierarchy says don't optimize what you can't reproduce.
Docker Compose is the reproducibility layer."

**Chapter 3 — Containers**
The container is the unit of deployment. Build once, run anywhere.
The same Docker image runs in local Compose and in ECS Fargate with a config change only.
Interview line: "Practical MLOps chapter 3 — the container is the deployment unit.
That's why there's no special cloud build; the Dockerfile is the artifact."

**Chapter 6 — Monitoring**
Monitoring answers the question: is the system behaving as expected right now?
Metrics (Prometheus) answer it for operators. Scorecards (LLM-as-a-Judge) answer it for AI quality.
Interview line: "Chapter 6 separates system monitoring from model monitoring.
Prometheus is for the system; the evaluation harness is for the model."

**Chapters 7-9 — AWS/Azure/GCP**
Each cloud has the same capabilities under different brand names.
The architectural decisions (container platform, managed vector store, object store) are the same;
the service names differ.
Interview line: "Practical MLOps chapters 7-9 showed me the pattern is the same across clouds —
the Cloud Migration Map in the Operator Console is that insight made interactive."

## Prompt Engineering for Generative AI (O'Reilly)

**Cross-Provider Structured Output**
Different LLM providers implement function calling differently.
The LLMGateway abstraction hides this: `complete_structured()` returns a typed Pydantic model
regardless of whether the backend is Claude, GPT-5, or Gemini.
Interview line: "The gateway pattern exists because structured output schemas differ per provider —
the abstraction lets me swap providers without changing node code."