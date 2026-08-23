import type { NodeTypeManifest } from '../../../api/types';

/**
 * Node-type manifest fixtures for tests.
 *
 * A manifest carries presentation metadata (family, execution kind, about,
 * presets) alongside the three schemas.
 *
 * Most tests only care about a small part of the manifest, so this helper
 * provides valid defaults and allows each test to override only what it needs.
 */
export function manifestFixture(
  overrides: Partial<NodeTypeManifest> &
    Pick<NodeTypeManifest, 'type_name'>,
): NodeTypeManifest {
  return {
    description: '',
    category: 'Other',
    icon: 'topology',

    family: 'specialized',
    execution_kind: 'deterministic',

    uses_ai: false,
    external_action: false,

    about: {},
    presets: [],

    input_schema: {},
    output_schema: {},
    config_schema: {},

    ...overrides,
  };
}