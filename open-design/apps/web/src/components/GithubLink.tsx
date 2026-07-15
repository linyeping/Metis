import { Icon } from './Icon';

export const METIS_GITHUB_URL = 'https://github.com/linyeping/Metis';

export function GithubLink() {
  return (
    <a
      className="entry-github-link od-tooltip"
      href={METIS_GITHUB_URL}
      target="_blank"
      rel="noreferrer noopener"
      aria-label="Metis GitHub"
      data-tooltip="Metis GitHub"
      data-tooltip-placement="bottom"
      data-testid="entry-github-link"
    >
      <Icon name="github-filled" size={16} className="entry-github-link__icon" />
      <span className="entry-github-link__label">GitHub</span>
    </a>
  );
}
