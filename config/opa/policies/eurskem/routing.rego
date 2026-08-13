package eurskem.routing

import rego.v1

# Eurskem LLM routing policy — starter defaults. See docs/architecture (LLM_POLICY_OPA.md).
#
# Input contract (app/security/llm_policy.py::enforce_policy):
#   input.workspace           string
#   input.data_class          "PUBLIC" | "INTERNAL" | "CONFIDENTIAL" | "RESTRICTED"
#   input.request.capabilities []string
#   input.model.id             string | null
#   input.model.family         string | null
#   input.route.backend        "openrouter" | "direct" | "local" | "unknown"
#   input.route.provider       string | null
#   input.route.zdr            boolean | null   (null = unknown ZDR status)
#   input.route.data_collection boolean | null  (null = unknown)
#
# Output contract:
#   { allow, reason_codes, constraints: { zdr_required, data_collection_allowed }, policy_version }
#
# Defaults (configurable — this is a starting point, not a fixed rule):
#   PUBLIC        — any backend.
#   INTERNAL      — any backend.
#   CONFIDENTIAL  — ZDR-approved routes only, or explicitly approved direct/local routes.
#                   Unknown ZDR status (zdr == null) is treated as NOT approved.
#   RESTRICTED    — local/private backend only.

policy_version := "2026-08-v1"

default allow := false

allow if {
	input.data_class == "PUBLIC"
}

allow if {
	input.data_class == "INTERNAL"
}

allow if {
	input.data_class == "CONFIDENTIAL"
	zdr_approved
}

allow if {
	input.data_class == "RESTRICTED"
	input.route.backend == "local"
}

zdr_approved if {
	input.route.zdr == true
}

zdr_approved if {
	input.route.backend == "local"
}

reason_codes contains "data_class_public_or_internal_any_route" if {
	allow
	input.data_class in {"PUBLIC", "INTERNAL"}
}

reason_codes contains "confidential_requires_zdr" if {
	not allow
	input.data_class == "CONFIDENTIAL"
}

reason_codes contains "confidential_zdr_approved" if {
	allow
	input.data_class == "CONFIDENTIAL"
}

reason_codes contains "restricted_requires_local_backend" if {
	not allow
	input.data_class == "RESTRICTED"
}

reason_codes contains "restricted_local_backend_approved" if {
	allow
	input.data_class == "RESTRICTED"
}

reason_codes contains "unknown_data_class" if {
	not input.data_class in {"PUBLIC", "INTERNAL", "CONFIDENTIAL", "RESTRICTED"}
}

constraints := {
	"zdr_required": input.data_class in {"CONFIDENTIAL", "RESTRICTED"},
	"data_collection_allowed": input.data_class in {"PUBLIC", "INTERNAL"},
}

decision := {
	"allow": allow,
	"reason_codes": reason_codes,
	"constraints": constraints,
	"policy_version": policy_version,
}
