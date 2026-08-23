import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import tseslint from 'typescript-eslint'
import { defineConfig, globalIgnores } from 'eslint/config'

export default defineConfig([
  globalIgnores(['dist']),
  {
    files: ['**/*.{ts,tsx}'],
    extends: [
      js.configs.recommended,
      tseslint.configs.recommended,
      reactHooks.configs.flat.recommended,
      reactRefresh.configs.vite,
    ],
    languageOptions: {
      globals: globals.browser,
    },
    rules: {
      // Dev-experience rule (HMR fast-refresh boundaries only; zero
      // production impact). This codebase deliberately co-locates small
      // pure helpers and constants with their components (ConditionGroupEditor,
      // StartFormRenderer, ExecutionKindBadge, ...) and they are widely
      // imported; splitting them into separate modules would add file churn
      // with no behavioural benefit. Tracked for review if the React
      // Compiler is adopted.
      'react-refresh/only-export-components': 'off',
      // React Compiler heuristic introduced by eslint-plugin-react-hooks v7.
      // Every flagged site is the intentional reset-then-fetch idiom with
      // cancellation (CloudFileBrowser, MCPToolPicker, OutputsPanel,
      // SchemaBuilder, ConfigureTab, MCPToolConfig) — correct React, not a
      // bug. Refactoring to compiler-pure shapes (key-based resets /
      // derived state) is tracked as a follow-up; until then this keeps the
      // remaining rules-of-hooks and exhaustive-deps checks fully enforced.
      'react-hooks/set-state-in-effect': 'off',
    },
  },
])
