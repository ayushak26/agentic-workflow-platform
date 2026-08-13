import type { NodeTypeManifest } from '../../../api/types';

/**
 * Node-type manifest fixtures for tests.
 *
 * A manifest carries presentation metadata (family, execution kind, about,
 * presets) alongside the three schemas. Tests rarely care about any of it, so
 * this supplies honest defaults and lets each test override only the part it is
 * actually asserting on — rather than every test restating the whole shape and
 * needing an edit whenever the manifest grows a field.
 */
export function manifestFixture(
  overrides: Partial<NodeTypeManifest> & Pick<NodeTypeManifest, 'type_name'>,
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
