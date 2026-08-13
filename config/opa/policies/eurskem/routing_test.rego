package eurskem.routing_test

import data.eurskem.routing
import rego.v1

_base_input := {
	"workspace": "ws_1",
	"request": {"capabilities": []},
	"model": {"id": "test-model", "family": null},
}

test_public_allows_any_route if {
	routing.allow with input as object.union(_base_input, {
		"data_class": "PUBLIC",
		"route": {"backend": "openrouter", "provider": "openrouter", "zdr": null, "data_collection": null},
	})
}

test_internal_allows_any_route if {
	routing.allow with input as object.union(_base_input, {
		"data_class": "INTERNAL",
		"route": {"backend": "openrouter", "provider": "openrouter", "zdr": null, "data_collection": null},
	})
}

test_confidential_denied_without_zdr if {
	not routing.allow with input as object.union(_base_input, {
		"data_class": "CONFIDENTIAL",
		"route": {"backend": "direct", "provider": "openai", "zdr": null, "data_collection": null},
	})
}

test_confidential_denied_when_zdr_false if {
	not routing.allow with input as object.union(_base_input, {
		"data_class": "CONFIDENTIAL",
		"route": {"backend": "openrouter", "provider": "openrouter", "zdr": false, "data_collection": true},
	})
}

test_confidential_allowed_with_zdr_true if {
	routing.allow with input as object.union(_base_input, {
		"data_class": "CONFIDENTIAL",
		"route": {"backend": "direct", "provider": "anthropic", "zdr": true, "data_collection": false},
	})
}

test_confidential_allowed_on_local_backend_without_explicit_zdr if {
	routing.allow with input as object.union(_base_input, {
		"data_class": "CONFIDENTIAL",
		"route": {"backend": "local", "provider": "moonshot-local", "zdr": null, "data_collection": null},
	})
}

test_restricted_denied_off_local_backend if {
	not routing.allow with input as object.union(_base_input, {
		"data_class": "RESTRICTED",
		"route": {"backend": "direct", "provider": "anthropic", "zdr": true, "data_collection": false},
	})
}

test_restricted_allowed_on_local_backend if {
	routing.allow with input as object.union(_base_input, {
		"data_class": "RESTRICTED",
		"route": {"backend": "local", "provider": "zai-local", "zdr": null, "data_collection": null},
	})
}

test_unknown_data_class_denied_and_flagged if {
	decision_input := object.union(_base_input, {
		"data_class": "SECRET",
		"route": {"backend": "direct", "provider": "anthropic", "zdr": true, "data_collection": false},
	})
	not routing.allow with input as decision_input
	"unknown_data_class" in routing.reason_codes with input as decision_input
}

test_constraints_require_zdr_for_confidential_and_restricted if {
	decision_input := object.union(_base_input, {
		"data_class": "CONFIDENTIAL",
		"route": {"backend": "direct", "provider": "anthropic", "zdr": true, "data_collection": false},
	})
	routing.constraints.zdr_required with input as decision_input
	not routing.constraints.data_collection_allowed with input as decision_input
}

test_constraints_allow_data_collection_for_public_and_internal if {
	decision_input := object.union(_base_input, {
		"data_class": "INTERNAL",
		"route": {"backend": "openrouter", "provider": "openrouter", "zdr": null, "data_collection": null},
	})
	not routing.constraints.zdr_required with input as decision_input
	routing.constraints.data_collection_allowed with input as decision_input
}
