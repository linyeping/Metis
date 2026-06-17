import { describe, expect, it } from 'vitest';
import {
  filterSlashWorkflowCommands,
  moveSlashSelection,
  normalizeSlashQuery,
  slashWorkflowCommands,
} from '../slashCommands';

describe('slash workflow commands', () => {
  it('registers the visible workflow commands', () => {
    expect(slashWorkflowCommands.map(command => command.command)).toEqual([
      '/simplify',
      '/skillify',
      '/stuck',
      '/remember',
      '/update-config',
    ]);
  });

  it('returns every workflow for an empty query', () => {
    expect(filterSlashWorkflowCommands('')).toHaveLength(5);
    expect(filterSlashWorkflowCommands('/')).toHaveLength(5);
  });

  it('filters by command name and keywords', () => {
    expect(filterSlashWorkflowCommands('simp').map(command => command.command)).toEqual(['/simplify']);
    expect(filterSlashWorkflowCommands('diff').map(command => command.command)).toEqual(['/simplify']);
    expect(filterSlashWorkflowCommands('权限').map(command => command.command)).toEqual(['/update-config']);
    expect(filterSlashWorkflowCommands('卡死').map(command => command.command)).toEqual(['/stuck']);
  });

  it('normalizes slash queries', () => {
    expect(normalizeSlashQuery('/Update-Config ')).toBe('update-config');
  });

  it('moves the active selection with wraparound', () => {
    expect(moveSlashSelection(0, 5, 1)).toBe(1);
    expect(moveSlashSelection(4, 5, 1)).toBe(0);
    expect(moveSlashSelection(0, 5, -1)).toBe(4);
    expect(moveSlashSelection(10, 5, 1)).toBe(0);
    expect(moveSlashSelection(-1, 5, -1)).toBe(4);
    expect(moveSlashSelection(0, 0, 1)).toBe(-1);
  });
});
