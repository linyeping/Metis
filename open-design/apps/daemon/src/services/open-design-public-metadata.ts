export interface OpenDesignGithubLatestReleaseInfo {
  tagName: string;
  htmlUrl: string;
  fetchedAt: number;
  stale: boolean;
}

export interface OpenDesignPublicMetadataService {
  readLatestReleaseInfo(): Promise<OpenDesignGithubLatestReleaseInfo>;
}

interface CachedGithubLatestReleaseInfo {
  tagName: string;
  htmlUrl: string;
  fetchedAt: number;
}

interface GithubLatestReleasePayload {
  tag_name?: unknown;
  html_url?: unknown;
}

export interface OpenDesignPublicMetadataServiceOptions {
  fetchImpl?: typeof fetch;
  now?: () => number;
}

const OPEN_DESIGN_GITHUB_RELEASE_LATEST_API = 'https://api.github.com/repos/linyeping/Metis/releases/latest';
const OPEN_DESIGN_GITHUB_CACHE_TTL_MS = 60 * 60 * 1000;
const OPEN_DESIGN_GITHUB_TIMEOUT_MS = 4_000;

function withFreshness<T extends { fetchedAt: number }>(
  value: T,
  stale: boolean,
): T & { stale: boolean } {
  return { ...value, stale };
}

export function createOpenDesignPublicMetadataService({
  fetchImpl = fetch,
  now = () => Date.now(),
}: OpenDesignPublicMetadataServiceOptions = {}): OpenDesignPublicMetadataService {
  let githubLatestReleaseCache: CachedGithubLatestReleaseInfo | null = null;
  let githubLatestReleaseInflight: Promise<OpenDesignGithubLatestReleaseInfo> | null = null;

  async function readLatestReleaseInfo(): Promise<OpenDesignGithubLatestReleaseInfo> {
    const currentTime = now();
    if (
      githubLatestReleaseCache &&
      currentTime - githubLatestReleaseCache.fetchedAt < OPEN_DESIGN_GITHUB_CACHE_TTL_MS
    ) {
      return withFreshness(githubLatestReleaseCache, false);
    }

    if (githubLatestReleaseInflight) return githubLatestReleaseInflight;

    githubLatestReleaseInflight = (async () => {
      const ctrl = new AbortController();
      const timeout = setTimeout(() => ctrl.abort(), OPEN_DESIGN_GITHUB_TIMEOUT_MS);
      try {
        const response = await fetchImpl(OPEN_DESIGN_GITHUB_RELEASE_LATEST_API, {
          headers: {
            accept: 'application/vnd.github+json',
            'user-agent': 'metis-design-daemon',
          },
          signal: ctrl.signal,
        });
        if (!response.ok) {
          throw new Error(`GitHub latest release request failed with HTTP ${response.status}`);
        }
        const payload = (await response.json()) as GithubLatestReleasePayload;
        const tagName = typeof payload.tag_name === 'string' ? payload.tag_name : null;
        const htmlUrl = typeof payload.html_url === 'string' ? payload.html_url : null;
        if (!tagName || !htmlUrl) {
          throw new Error('GitHub latest release metadata did not include tag_name/html_url');
        }
        githubLatestReleaseCache = { tagName, htmlUrl, fetchedAt: now() };
        return withFreshness(githubLatestReleaseCache, false);
      } catch (error) {
        if (githubLatestReleaseCache) return withFreshness(githubLatestReleaseCache, true);
        throw error;
      } finally {
        clearTimeout(timeout);
        githubLatestReleaseInflight = null;
      }
    })();

    return githubLatestReleaseInflight;
  }

  return {
    readLatestReleaseInfo,
  };
}
