import type { Express } from 'express';
import type {
  OpenDesignGithubLatestReleaseResponse,
} from '@open-design/contracts';
import type { RouteDeps } from '../server-context.js';
import type { OpenDesignPublicMetadataService } from '../services/open-design-public-metadata.js';

export interface RegisterOpenDesignPublicMetadataRoutesDeps extends RouteDeps<'http'> {
  openDesignPublicMetadata: OpenDesignPublicMetadataService;
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export function registerOpenDesignPublicMetadataRoutes(
  app: Express,
  ctx: RegisterOpenDesignPublicMetadataRoutesDeps,
): void {
  const { openDesignPublicMetadata } = ctx;

  app.get('/api/github/open-design/releases/latest', async (_req, res) => {
    try {
      const release = await openDesignPublicMetadata.readLatestReleaseInfo();
      const payload: OpenDesignGithubLatestReleaseResponse = {
        repo: 'linyeping/Metis',
        tag_name: release.tagName,
        html_url: release.htmlUrl,
        fetchedAt: release.fetchedAt,
        stale: release.stale,
      };
      res.json(payload);
    } catch (error) {
      res.status(502).json({ error: errorMessage(error) });
    }
  });

}
