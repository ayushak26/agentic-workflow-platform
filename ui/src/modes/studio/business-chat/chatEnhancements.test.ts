import { describe, expect, it } from 'vitest';
import {
  applySlashCommand, classifyResponseFormat, extractTemplateVariables, followUpExecutionOutput,
  matchingSlashCommands, renderPromptTemplate, SLASH_COMMANDS,
} from './chatEnhancements';

describe('prompt variables', () => {
  it('extracts unique placeholders and renders supplied values', () => {
    expect(extractTemplateVariables('{{topic}} in {{ tone }} about {{topic}}')).toEqual(['topic', 'tone']);
    expect(renderPromptTemplate('{{topic}} — {{tone}} — {{length}}', { topic: 'AI', tone: 'formal' }))
      .toBe('AI — formal — {{length}}');
  });
});

describe('slash commands', () => {
  it('autocompletes commands and applies format hints', () => {
    expect(matchingSlashCommands('/ta').map(item => item.command)).toEqual(['/table']);
    const table = SLASH_COMMANDS.find(item => item.command === '/table')!;
    expect(applySlashCommand(table, '/table compare A and B')).toEqual({
      text: 'compare A and B', format: 'table',
    });
  });
  it('opens deep research while preserving the command question', () => {
    const research = SLASH_COMMANDS.find(item => item.command === '/research')!;
    expect(applySlashCommand(research, '/research latest battery recycling evidence')).toEqual({
      text: 'latest battery recycling evidence', action: 'research',
    });
  });
  it('does not show autocomplete after command arguments begin', () => {
    expect(matchingSlashCommands('/table compare')).toEqual([]);
  });
});

describe('adaptive format classifier', () => {
  it.each([
    ['Compare A versus B', 'table'],
    ['List the available options', 'bullets'],
    ['What steps should I follow?', 'numbered'],
    ['Chart the trend: 10, 20, 35', 'chart'],
    ['Explain why the sky is blue', 'prose'],
  ] as const)('classifies %s as %s', (query, expected) => {
    expect(classifyResponseFormat(query)).toBe(expected);
  });
});

describe('follow-up execution intent', () => {
  it('routes only artifact-producing continuations to another workflow', () => {
    expect(followUpExecutionOutput('Turn that into a presentation')).toBe('pptx');
    expect(followUpExecutionOutput('Export this as a PDF')).toBe('pdf');
    expect(followUpExecutionOutput('Explain the risks')).toBeNull();
  });
});