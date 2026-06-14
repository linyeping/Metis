import { Children, useEffect, useState } from 'react';
import logo from '../../assets/logo.png';
import { completeFirstRun, getProviderStatus, verifyFirstRun } from '../../lib/api';
import type { ProviderProfile, ProviderValidation } from '../../lib/types';
import { useUiStore } from '../../store/uiStore';
import { useT } from '../../hooks/useT';

interface SetupWizardProps {
  onDone: () => void;
}

const steps = ['欢迎', '供应商', '配置', '验证', '完成'];
type VerificationTone = 'idle' | 'checking' | 'success' | 'warning' | 'error';

interface VerificationState {
  tone: VerificationTone;
  title: string;
  message: string;
  hint: string;
  warnings: string[];
}

const idleVerification: VerificationState = {
  tone: 'idle',
  title: '',
  message: '',
  hint: '',
  warnings: [],
};

export function SetupWizard({ onDone }: SetupWizardProps) {
  const language = useUiStore(state => state.language);
  const t = useT();
  const [step, setStep] = useState(0);
  const [providers, setProviders] = useState<ProviderProfile[]>([]);
  const [backend, setBackend] = useState('deepseek');
  const [baseUrl, setBaseUrl] = useState('https://api.deepseek.com');
  const [model, setModel] = useState('deepseek-v4-flash');
  const [apiKey, setApiKey] = useState('');
  const [verification, setVerification] = useState<VerificationState>(idleVerification);
  const [checking, setChecking] = useState(false);
  const zh = language === 'zh';
  const trimmedApiKey = apiKey.trim();

  useEffect(() => {
    void getProviderStatus().then(status => setProviders(status.providers));
  }, []);

  const selectProvider = (providerId: string) => {
    const provider = providers.find(item => item.providerId === providerId);
    setBackend(providerId);
    setBaseUrl(provider?.baseUrl ?? '');
    setModel(provider?.defaultModel ?? '');
    setVerification(idleVerification);
  };

  const verify = async () => {
    const cleanApiKey = apiKey.trim();
    setApiKey(cleanApiKey);
    setChecking(true);
    setVerification({
      tone: 'checking',
      title: zh ? '本地检查中' : 'Checking locally',
      message: zh ? '正在验证供应商、Base URL、模型和 API key 格式。' : 'Validating provider, Base URL, model, and API key format.',
      hint: '',
      warnings: [],
    });
    try {
      const result = await verifyFirstRun({ backend, baseUrl: baseUrl.trim(), model: model.trim(), apiKey: cleanApiKey });
      setVerification(verificationFromResult(result, zh));
    } catch (error) {
      setVerification({
        tone: 'error',
        title: zh ? '验证失败' : 'Verification failed',
        message: error instanceof Error ? error.message : String(error),
        hint: zh ? '请确认本地后端已启动后重试。' : 'Confirm the local backend is running, then try again.',
        warnings: [],
      });
    } finally {
      setChecking(false);
    }
  };

  const finish = async () => {
    const cleanApiKey = apiKey.trim();
    setApiKey(cleanApiKey);
    if (cleanApiKey) {
      await completeFirstRun({ backend, baseUrl: baseUrl.trim(), model: model.trim(), apiKey: cleanApiKey });
    }
    onDone();
  };

  return (
    <div className="setup-layer">
      <section className="setup-card">
        <aside>
          <img src={logo} alt="" />
          <h1>Metis</h1>
          <p>{zh ? '安静、可靠、面向工作区的 AI' : 'Quiet, capable AI for your workspace'}</p>
        </aside>
        <main>
          <div className="setup-steps">
            {Children.toArray(steps.map((label, index) => (
              <span data-active={index === step} data-done={index < step}>
                {t(label)}
              </span>
            )))}
          </div>
          {step === 0 && (
            <div className="setup-page">
              <h2>{zh ? '欢迎使用 Metis' : 'Welcome to Metis'}</h2>
              <p>{zh ? '一次配置模型供应商，后续可以随时调整。' : 'Set up your provider once. You can change it later.'}</p>
            </div>
          )}
          {step === 1 && (
            <div className="setup-page">
              <h2>{zh ? '选择供应商' : 'Choose provider'}</h2>
              <div className="provider-grid">
                {Children.toArray((providers.length ? providers : fallbackProviders()).map(provider => (
                  <button type="button" data-active={backend === provider.providerId} onClick={() => selectProvider(provider.providerId)}>
                    <strong>{t(provider.displayName)}</strong>
                    <span>{provider.defaultModel || provider.backendType}</span>
                  </button>
                )))}
              </div>
            </div>
          )}
          {step === 2 && (
            <div className="setup-page form-page">
              <label>
                <span>Base URL</span>
                <input value={baseUrl} onChange={event => setBaseUrl(event.target.value)} />
              </label>
              <label>
                <span>{zh ? '模型' : 'Model'}</span>
                <input value={model} onChange={event => setModel(event.target.value)} />
              </label>
              <label>
                <span>API key</span>
                <input value={apiKey} onBlur={() => setApiKey(value => value.trim())} onChange={event => setApiKey(event.target.value)} />
                <small>{apiKeyFormatHint(backend, zh)}</small>
              </label>
            </div>
          )}
          {step === 3 && (
            <div className="setup-page">
              <h2>{zh ? '验证连接' : 'Verify connection'}</h2>
              <div className="setup-verification" data-tone={verification.tone}>
                <strong>{verification.title || (zh ? '本地配置检查' : 'Local configuration check')}</strong>
                <span>{verification.message || (zh ? '这里会检查供应商、Base URL、模型和 API key 是否完整。' : 'Provider, Base URL, model, and API key completeness are checked here.')}</span>
                {verification.hint && <em>{verification.hint}</em>}
                {Children.toArray(verification.warnings.map(warning => <em>{warning}</em>))}
              </div>
              <button type="button" className="primary" disabled={checking || !trimmedApiKey} onClick={verify}>
                {checking ? (zh ? '检查中...' : 'Checking...') : zh ? '检查配置' : 'Check config'}
              </button>
            </div>
          )}
          {step === 4 && (
            <div className="setup-page">
              <h2>{zh ? '准备好了' : 'Ready'}</h2>
              <p>{zh ? '进入桌面工作区。' : 'Enter your desktop workspace.'}</p>
            </div>
          )}
          <footer>
            <button type="button" disabled={step === 0} onClick={() => setStep(value => Math.max(0, value - 1))}>
              {zh ? '返回' : 'Back'}
            </button>
            {step < 4 ? (
              <button type="button" className="primary" onClick={() => setStep(value => value + 1)}>
                {zh ? '继续' : 'Continue'}
              </button>
            ) : (
              <button type="button" className="primary" onClick={() => void finish()}>
                {zh ? '完成' : 'Finish'}
              </button>
            )}
            {step === 3 && (
              <button type="button" onClick={() => void finish()}>
                {zh ? '暂时跳过' : 'Skip for now'}
              </button>
            )}
          </footer>
        </main>
      </section>
    </div>
  );
}

function fallbackProviders(): ProviderProfile[] {
  return [
    {
      providerId: 'deepseek',
      displayName: 'DeepSeek',
      backendType: 'openai',
      aliases: [],
      baseUrl: 'https://api.deepseek.com',
      chatCompletionsPath: '/chat/completions',
      defaultModel: 'deepseek-v4-flash',
      fallbackModels: ['deepseek-v4-flash', 'deepseek-v4-pro'],
      apiKeyRequired: true,
      openaiCompatible: true,
      capabilities: { stream: true, tools: true, vision: false, parallelToolCalls: true, requiresReasoningPassback: true },
      modelContextWindows: { 'deepseek-v4-flash': 1000000, 'deepseek-v4-pro': 1000000 },
      modelNotes: {},
    },
    {
      providerId: 'openai',
      displayName: 'OpenAI',
      backendType: 'openai',
      aliases: [],
      baseUrl: 'https://api.openai.com/v1',
      chatCompletionsPath: '/chat/completions',
      defaultModel: 'gpt-4o-mini',
      fallbackModels: ['gpt-4o-mini'],
      apiKeyRequired: true,
      openaiCompatible: true,
      capabilities: { stream: true, tools: true, vision: true, parallelToolCalls: true, requiresReasoningPassback: false },
      modelContextWindows: { 'gpt-4o-mini': 128000 },
      modelNotes: {},
    },
    {
      providerId: 'custom-openai',
      displayName: '自定义 OpenAI 中转站',
      backendType: 'openai',
      aliases: ['custom', 'openai-relay'],
      baseUrl: '',
      chatCompletionsPath: '/chat/completions',
      defaultModel: '',
      fallbackModels: [],
      apiKeyRequired: true,
      openaiCompatible: true,
      capabilities: { stream: true, tools: true, vision: false, parallelToolCalls: false, requiresReasoningPassback: false },
      modelContextWindows: {},
      modelNotes: {},
    },
  ];
}

function verificationFromResult(result: ProviderValidation, zh: boolean): VerificationState {
  const warnings = result.warnings || [];
  const tone: VerificationTone = result.ok ? (warnings.length ? 'warning' : 'success') : 'error';
  return {
    tone,
    title: result.title || (result.ok ? (zh ? '配置检查通过' : 'Configuration passed') : zh ? '配置需要调整' : 'Configuration needs attention'),
    message: result.message || (result.ok ? (zh ? '本地配置检查通过。' : 'Local configuration check passed.') : zh ? '配置检查未通过。' : 'Configuration check failed.'),
    hint: result.hint || '',
    warnings,
  };
}

function apiKeyFormatHint(providerId: string, zh: boolean): string {
  const id = providerId.toLowerCase();
  if (id.includes('anthropic')) {
    return zh ? 'Anthropic API key 通常以 sk-ant- 开头。' : 'Anthropic API keys usually start with sk-ant-.';
  }
  if (id.includes('gemini') || id.includes('google')) {
    return zh ? 'Gemini API key 通常来自 Google AI Studio。' : 'Gemini API keys usually come from Google AI Studio.';
  }
  if (id.includes('custom')) {
    return zh ? '自定义 OpenAI 兼容服务请填写服务方提供的 Bearer key。' : 'For custom OpenAI-compatible relays, use the Bearer key from that service.';
  }
  return zh ? 'OpenAI / DeepSeek 兼容密钥通常以 sk- 开头。' : 'OpenAI / DeepSeek-compatible keys usually start with sk-.';
}
