import type { MarketplaceItem } from './types';

const CJK_RE = /[\u3400-\u9fff]/;

const MCP_DESCRIPTION_ZH: Record<string, string> = {
  'inference.sh': '运行 150 多种 AI 应用，涵盖图像、视频、音频、LLM 和 3D，并支持浏览、执行与流式返回结果。',
  'docs-mcp': 'Tandem 文档远程 MCP 服务，提供安装指南、SDK、工作流和智能体配置帮助。',
  'hood. — .hood name service': '解析 Robinhood Chain 上的 .hood 名称，支持正向解析、反向解析、文本记录和可用性查询。',
  'trading': '用于 AI 交易策略开发，支持回测、市场数据分析和投资组合分析。',
  '1325.ai': '提供经过验证的美国黑人企业目录，支持搜索、地图、会员和奖励功能。',
  'atars mcp': '提供加密市场信号、技术指标和情绪分析能力。',
  'abmeter': '面向 AI 优先实验工作流的功能开关和 A/B 测试平台。',
  'adadvisor mcp server': '查询 Meta 广告账户、广告系列、广告组、广告及其效果指标。',
  'adeu': '自动化 DOCX 文档修订与红线标注处理。',
  'google-ads': '管理 Google Ads 广告系列、关键词和效果指标。',
  'adweave — meta ads mcp server': '为 Meta Ads 提供广告系列、素材、受众和数据洞察工具。',
  'agentberg': '提供智能体之间的交易情报交换、发现发布、质量投票和声誉机制。',
};

const MCP_SEARCH_ALIASES_ZH: Array<[string, string]> = [
  ['交易策略', 'trading'],
  ['回测', 'trading'],
  ['投资组合', 'trading'],
  ['加密市场', 'crypto'],
  ['市场信号', 'crypto'],
  ['技术指标', 'crypto'],
  ['情绪分析', 'crypto'],
  ['meta 广告', 'meta ads'],
  ['广告账户', 'meta ads'],
  ['广告系列', 'ads'],
  ['企业目录', '1325.ai'],
  ['文档修订', 'adeu'],
  ['红线标注', 'adeu'],
  ['功能开关', 'abmeter'],
  ['名称解析', 'hood'],
  ['交易情报', 'agentberg'],
];

export function marketplaceItemDescription(item: MarketplaceItem, language: string): string {
  const locale = language === 'zh' ? 'zh' : 'en';
  const localized = item.descriptions?.[locale]?.trim();
  if (localized) return localized;

  const sourceDescription = item.description.trim();
  if (locale === 'zh' && sourceDescription && CJK_RE.test(sourceDescription)) return sourceDescription;

  if (locale === 'zh' && (item.sourceType === 'registry' || item.category === 'MCP Registry')) {
    const registryDescription = MCP_DESCRIPTION_ZH[item.name.trim().toLowerCase()];
    if (registryDescription) return registryDescription;
  }

  if (sourceDescription && locale !== 'zh') return sourceDescription;

  // A source-specific English description is still more useful than a fabricated generic translation.
  const english = item.descriptions?.en?.trim() || sourceDescription;
  if (english) return english;
  return locale === 'zh' ? '该来源暂未提供详细介绍。' : 'No detailed description was provided by this source.';
}

export function marketplaceRegistrySearchQuery(query: string, language: string): string {
  const value = query.trim();
  if (!value || language !== 'zh') return value;
  const normalized = value.toLowerCase();
  const alias = MCP_SEARCH_ALIASES_ZH.find(([needle]) => normalized.includes(needle));
  return alias?.[1] || value;
}
