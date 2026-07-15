// @vitest-environment jsdom

import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { GithubLink, METIS_GITHUB_URL } from '../../src/components/GithubLink';

describe('GithubLink', () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it('links to Metis without a star count or network request', () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch');
    render(<GithubLink />);

    const link = screen.getByRole('link', { name: 'Metis GitHub' });
    expect(link.getAttribute('href')).toBe(METIS_GITHUB_URL);
    expect(screen.getByText('GitHub')).toBeTruthy();
    expect(screen.queryByText(/star/i)).toBeNull();
    expect(fetchSpy).not.toHaveBeenCalled();
  });
});
