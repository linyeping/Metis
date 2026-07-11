import { describe, expect, it } from 'vitest';

import { marketplaceItemDescription, marketplaceRegistrySearchQuery } from '../marketplaceDescriptions';
import type { MarketplaceItem } from '../types';

function item(overrides: Partial<MarketplaceItem> = {}): MarketplaceItem {
  return {
    id: 'openai:linear',
    kind: 'plugin',
    name: 'Linear',
    version: '1.0.0',
    description: 'Find and reference issues and projects.',
    descriptions: {
      en: 'Find and reference issues and projects.',
      zh: '查找并引用 Linear 中的问题、项目和相关上下文，辅助需求跟踪与项目协作。',
    },
    content: '',
    publisher: 'OpenAI',
    category: 'Productivity',
    featured: true,
    brandColor: '#10A37F',
    iconDataUrl: '',
    sourceType: 'remote-plugin',
    sourceUrl: '',
    marketplaceSource: 'openai-plugins',
    marketplaceName: 'OpenAI 官方',
    license: '',
    revision: '',
    trust: 'official',
    homepage: '',
    installed: false,
    enabled: false,
    needsSetup: false,
    installedVersion: '',
    updateAvailable: false,
    error: '',
    configuredEnv: [],
    components: [],
    ...overrides,
  };
}

describe('marketplaceItemDescription', () => {
  it('selects the source-specific localized description', () => {
    const row = item();
    expect(marketplaceItemDescription(row, 'zh')).toBe(row.descriptions.zh);
    expect(marketplaceItemDescription(row, 'en')).toBe(row.descriptions.en);
  });

  it('keeps a specific source description when a Chinese translation is unavailable', () => {
    const row = item({ id: 'community:deploy', descriptions: {}, description: 'Deploy previews and inspect failed releases.' });
    expect(marketplaceItemDescription(row, 'zh')).toBe('Deploy previews and inspect failed releases.');
  });

  it('does not invent provider boilerplate when the source has no description', () => {
    const row = item({ descriptions: {}, description: '' });
    expect(marketplaceItemDescription(row, 'zh')).toBe('该来源暂未提供详细介绍。');
  });

  it('only applies registry fallbacks to registry items and preserves real Chinese source text', () => {
    const custom = item({ name: 'trading', descriptions: {}, description: 'Custom trading extension.', sourceType: 'remote-plugin' });
    const registry = item({ name: 'trading', descriptions: {}, description: '来源提供的中文交易说明。', sourceType: 'registry', category: 'MCP Registry' });

    expect(marketplaceItemDescription(custom, 'zh')).toBe('Custom trading extension.');
    expect(marketplaceItemDescription(registry, 'zh')).toBe('来源提供的中文交易说明。');
  });

  it('maps localized registry searches to upstream keywords', () => {
    expect(marketplaceRegistrySearchQuery('交易策略', 'zh')).toBe('trading');
    expect(marketplaceRegistrySearchQuery('加密市场信号', 'zh')).toBe('crypto');
    expect(marketplaceRegistrySearchQuery('Meta 广告账户', 'zh')).toBe('meta ads');
    expect(marketplaceRegistrySearchQuery('trading', 'en')).toBe('trading');
  });
});
