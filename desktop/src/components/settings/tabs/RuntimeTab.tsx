import { memo, useCallback, useEffect, useState } from 'react';
import {
  Activity,
  Archive,
  CheckCircle2,
  Copy,
  FileDown,
  FolderOpen,
  HardDrive,
  Play,
  RefreshCw,
  Server,
  ShieldCheck,
  Wrench,
} from 'lucide-react';
import type { RuntimeManagerCommandResult, RuntimeManagerStatus } from '../../../lib/types';
import {
  runtimeManagerProvision,
  runtimeManagerProvisionStatus,
  runtimeManagerStorageUsage,
  runtimeManagerCleanup,
  runtimeManagerSelfTest,
  runtimeManagerDownloadStart,
  runtimeManagerDownloadProgress,
  type RuntimeProvisionStatus,
  type RuntimeStorageUsage,
  type RuntimeSelfTestResult,
  type RuntimeDownloadProgress,
} from '../../../lib/api';
import { safeJson, formatTime } from '../settingsShared';
import { useT } from '../../../hooks/useT';

interface RuntimeTabProps {
  busy: string;
  message: string;
  onBuildVmAssets: () => void | Promise<void>;
  onBuildVmAssetsPlan: () => void | Promise<void>;
  onBuildPlan: () => void | Promise<void>;
  onDiagnostics: (sessionId?: string) => void | Promise<void>;
  onImport: () => void | Promise<void>;
  onImportPlan: () => void | Promise<void>;
  onPackageBundle: () => void | Promise<void>;
  onPackageVmBundle: () => void | Promise<void>;
  onPrepareBundle: () => void | Promise<void>;
  onRefresh: () => void | Promise<void>;
  onRepair: () => void | Promise<void>;
  onSmoke: () => void | Promise<void>;
  onStartupTest: () => void | Promise<void>;
  onValidateRelease: () => void | Promise<void>;
  result: RuntimeManagerCommandResult | null;
  status: RuntimeManagerStatus | null;
}

const SandboxProvisionPanel = memo(function SandboxProvisionPanel() {
  const t = useT();
  const [status, setStatus] = useState<RuntimeProvisionStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState('');
  const [storage, setStorage] = useState<RuntimeStorageUsage | null>(null);
  const [cleaning, setCleaning] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const [progress, setProgress] = useState<RuntimeDownloadProgress | null>(null);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<RuntimeSelfTestResult | null>(null);

  const refresh = useCallback(async (deep = false) => {
    try {
      const next = await runtimeManagerProvisionStatus(deep);
      setStatus(next);
    } catch {
      setStatus(null);
    }
  }, []);

  const refreshStorage = useCallback(async () => {
    try {
      setStorage(await runtimeManagerStorageUsage('.'));
    } catch {
      setStorage(null);
    }
  }, []);

  useEffect(() => {
    void refresh(false);
    // Deep pass picks up BIOS virtualization + group membership (slower).
    void refresh(true);
    void refreshStorage();
  }, [refresh, refreshStorage]);

  const cleanup = useCallback(async () => {
    setCleaning(true);
    try {
      await runtimeManagerCleanup({ keepRecent: 5, maxAgeDays: 7 });
      await refreshStorage();
    } finally {
      setCleaning(false);
    }
  }, [refreshStorage]);

  const elevatedNeeds = (status?.needs ?? []).filter(n => n !== 'install_pack');

  const downloadPack = useCallback(async () => {
    setDownloading(true);
    setProgress(null);
    setNote(t('正在下载沙箱运行时（约 800 MB，解压后约 3.2 GB），首次下载较久，请保持网络通畅…'));
    try {
      await runtimeManagerDownloadStart();
      // Poll progress until the background job finishes.
      for (;;) {
        await new Promise(r => setTimeout(r, 1000));
        let p: RuntimeDownloadProgress;
        try {
          p = await runtimeManagerDownloadProgress();
        } catch {
          continue;
        }
        setProgress(p);
        if (p.done) {
          await refresh(true);
          setNote(p.ok ? t('沙箱运行时已下载并安装完成。') : (p.error || t('下载未完成，请重试。')));
          break;
        }
      }
    } catch (err) {
      setNote(String((err as Error)?.message || err));
    } finally {
      setDownloading(false);
    }
  }, [refresh, t]);

  const selfTest = useCallback(async () => {
    setTesting(true);
    setTestResult(null);
    setNote(t('正在自检：真实启动沙箱并运行一个任务…'));
    try {
      const res = await runtimeManagerSelfTest();
      setTestResult(res);
      setNote('');
    } catch (err) {
      setNote(String((err as Error)?.message || err));
    } finally {
      setTesting(false);
    }
  }, [t]);

  const provision = useCallback(async () => {
    setBusy(true);
    setNote(t('已请求管理员授权（UAC），请在弹窗中确认…'));
    try {
      await runtimeManagerProvision(elevatedNeeds);
      await refresh(true);
      setNote(t('开通已执行。若提示需要重启或重新登录，请完成后再回到此处。'));
    } catch (err) {
      setNote(String((err as Error)?.message || err));
    } finally {
      setBusy(false);
    }
  }, [elevatedNeeds, refresh, t]);

  if (!status || !status.supported) return null;

  return (
    <div className="runtime-provision" data-ready={status.ready}>
      <div className="runtime-provision-head">
        {status.ready ? <CheckCircle2 size={15} className="ok" /> : <ShieldCheck size={15} />}
        <span className="runtime-provision-title">{t('HCS 沙箱环境')}</span>
        <span className="runtime-provision-badge" data-ok={status.ready}>
          {status.ready ? t('就绪') : t('需要设置')}
        </span>
      </div>

      <p className="runtime-provision-summary">{status.uxSummary}</p>

      <div className="runtime-provision-checks">
        {status.virtualizationOk !== null && (
          <ProvisionCheck ok={status.virtualizationOk} label={t('CPU 虚拟化')} />
        )}
        <ProvisionCheck ok={status.vmPlatformEnabled} label={t('虚拟机平台')} />
        <ProvisionCheck ok={status.serviceResponding} label={t('沙箱服务')} />
        <ProvisionCheck ok={status.bundleInstalled} label={t('运行时包')} />
      </div>

      {status.virtualizationOk === false && (
        <p className="runtime-provision-note runtime-provision-warn">
          {t('CPU 虚拟化（VT-x/AMD-V）在 BIOS/UEFI 中被禁用。请进固件设置开启后重试——Metis 无法替你修改 BIOS。')}
        </p>
      )}

      {!status.ready && status.virtualizationOk !== false && (
        <div className="runtime-provision-actions">
          {elevatedNeeds.length > 0 && (
            <button type="button" onClick={() => void provision()} disabled={busy}>
              <ShieldCheck size={13} />
              <span>
                {busy
                  ? t('开通中…')
                  : status.rebootRequired
                    ? t('开通沙箱（需一次 UAC + 重启）')
                    : t('开通沙箱（需一次 UAC）')}
              </span>
            </button>
          )}
          <button type="button" onClick={() => void refresh(true)} disabled={busy}>
            <RefreshCw size={13} className={busy ? 'spin' : ''} />
            <span>{t('重新检测')}</span>
          </button>
        </div>
      )}

      {/* Runtime pack download (first launch / missing bundle) */}
      {!status.bundleInstalled && (
        <div className="runtime-provision-actions">
          <button type="button" onClick={() => void downloadPack()} disabled={downloading}>
            <FileDown size={13} className={downloading ? 'spin' : ''} />
            <span>{downloading ? t('下载中…') : t('下载沙箱运行时（约 800 MB）')}</span>
          </button>
        </div>
      )}

      {downloading && progress && (
        <div className="runtime-download-progress">
          <div className="runtime-download-bar">
            <div
              className="runtime-download-bar-fill"
              style={{ width: `${progress.phase === 'downloading' ? progress.percent : (progress.done ? 100 : 100)}%` }}
              data-indeterminate={progress.phase !== 'downloading' && !progress.done}
            />
          </div>
          <span className="runtime-download-label">
            {progress.phase === 'downloading'
              ? `${t('下载中')} ${progress.percent}% · ${formatBytes(progress.downloadedBytes)} / ${formatBytes(progress.totalBytes)}`
              : progress.phase === 'extracting'
                ? t('解压中…')
                : progress.phase === 'decompressing'
                  ? t('展开运行时镜像中（约 3 GB，请稍候）…')
                  : progress.phase === 'verifying'
                    ? t('校验中…')
                    : t('准备中…')}
          </span>
        </div>
      )}

      {/* Real self-test: actually boots the VM + runs a job (no false positives) */}
      {status.ready && (
        <div className="runtime-provision-actions">
          <button type="button" onClick={() => void selfTest()} disabled={testing}>
            <Play size={13} className={testing ? 'spin' : ''} />
            <span>{testing ? t('自检中…') : t('自检沙箱')}</span>
          </button>
        </div>
      )}

      {testResult && (
        <p className="runtime-provision-note" data-ok={testResult.ok}>
          {testResult.ok ? '✓ ' : '✗ '}{testResult.message}
          {testResult.backend ? ` (${testResult.backend})` : ''}
        </p>
      )}

      {note && <p className="runtime-provision-note">{note}</p>}

      {storage && (
        <div className="runtime-provision-storage">
          <span className="runtime-provision-storage-text">
            {t('本地运行缓存')}: {formatBytes(storage.totalBytes)}
            {storage.sessionCount > 0 ? ` · ${storage.sessionCount} ${t('个会话')}` : ''}
          </span>
          <button type="button" onClick={() => void cleanup()} disabled={cleaning || storage.totalBytes === 0}>
            <RefreshCw size={12} className={cleaning ? 'spin' : ''} />
            <span>{cleaning ? t('清理中…') : t('清理缓存')}</span>
          </button>
        </div>
      )}
    </div>
  );
});

function ProvisionCheck({ ok, label }: { ok: boolean; label: string }) {
  return (
    <span className="runtime-provision-check" data-ok={ok}>
      <CheckCircle2 size={12} />
      {label}
    </span>
  );
}

export const RuntimeTab = memo(function RuntimeTab({
  busy,
  message,
  onBuildVmAssets,
  onBuildVmAssetsPlan,
  onBuildPlan,
  onDiagnostics,
  onImport,
  onImportPlan,
  onPackageBundle,
  onPackageVmBundle,
  onPrepareBundle,
  onRefresh,
  onRepair,
  onSmoke,
  onStartupTest,
  onValidateRelease,
  result,
  status,
}: RuntimeTabProps) {
  const t = useT();
  const [copiedPath, setCopiedPath] = useState('');
  const health = status?.health;
  const rootfs = selectedRootfs(status);
  const installed = Boolean(health?.metisWslReady);
  const canImport = Boolean(status?.wslRuntime?.ready_to_import || status?.wslRuntime?.readyToImport);
  const canPrepareBundle = Boolean(health?.rootfsReady);
  const canPackageBundle = Boolean(health?.runtimeBundleReady);
  const vmRuntime = status?.vmRuntime;
  const release = status?.releaseIntegration;
  const canRepairRuntime = Boolean(release?.bundledAvailable || release?.downloadAvailable);
  const canStartupTest = Boolean(health?.ready || vmRuntime?.runnerReady);
  const canPackageVmBundle = Boolean(recordValue(vmRuntime?.assetReport).required_present ?? recordValue(vmRuntime?.assetReport).requiredPresent);
  const latestSession = status?.sessions.sessions[0];
  const resultPaths = extractResultPaths(result);

  const copyPath = async (value: string) => {
    if (!isLocalPath(value)) return;
    await navigator.clipboard?.writeText(value);
    setCopiedPath(value);
    window.setTimeout(() => setCopiedPath(current => (current === value ? '' : current)), 1600);
  };

  const openPath = async (value: string) => {
    if (!isLocalPath(value)) return;
    await window.metis?.openPath?.(value);
  };

  return (
    <div className="settings-card-grid runtime-manager-panel">
      {/* Hero: the sandbox provision panel is the primary UX. */}
      <SandboxProvisionPanel />

      {/* Advanced: Metis Runtime Manager — collapsed by default */}
      <details className="settings-card runtime-collapsible">
        <summary className="settings-section-header">
          <Server size={16} className="section-icon" />
          <span><h3>{t('Metis Runtime Manager')}</h3></span>
          <span className="runtime-manager-status" data-ok={health?.ready ?? false}>
            {health?.preferredBackend || t('检测中')}
          </span>
        </summary>

        <div className="runtime-health-grid">
          <RuntimeMetric label={t('Metis WSL')} ok={health?.metisWslReady} value={installed ? t('已安装') : t('未安装')} />
          <RuntimeMetric label={t('rootfs')} ok={health?.rootfsReady} value={rootfs.size ? formatBytes(rootfs.size) : t('未检测到')} />
          <RuntimeMetric label={t('Bundle')} ok={health?.runtimeBundleReady} value={health?.runtimeBundleReady ? t('已准备') : t('未准备')} />
          <RuntimeMetric label={t('Docker')} ok={health?.dockerAvailable} value={health?.dockerAvailable ? t('可用') : t('不可用')} />
          <RuntimeMetric label={t('WSL')} ok={health?.wslAvailable} value={health?.wslAvailable ? t('可用') : t('不可用')} />
        </div>

        <div className="runtime-actions">
          <button type="button" onClick={() => void onRefresh()} disabled={Boolean(busy)}>
            <RefreshCw size={13} className={busy === 'refresh' ? 'spin' : ''} />
            <span>{t('刷新')}</span>
          </button>
          <button type="button" onClick={() => void onSmoke()} disabled={Boolean(busy) || !health?.ready}>
            <Play size={13} />
            <span>{busy === 'smoke' ? t('验证中...') : t('运行 smoke')}</span>
          </button>
          <button type="button" onClick={() => void onImportPlan()} disabled={Boolean(busy)}>
            <ShieldCheck size={13} />
            <span>{t('导入计划')}</span>
          </button>
          <button type="button" onClick={() => void onImport()} disabled={Boolean(busy) || installed || !canImport}>
            <Archive size={13} />
            <span>{installed ? t('已导入') : t('导入 WSL')}</span>
          </button>
          <button type="button" onClick={() => void onBuildPlan()} disabled={Boolean(busy)}>
            <Wrench size={13} />
            <span>{t('构建计划')}</span>
          </button>
          <button type="button" onClick={() => void onPrepareBundle()} disabled={Boolean(busy) || !canPrepareBundle}>
            <HardDrive size={13} />
            <span>{busy === 'prepare-bundle' ? t('准备中...') : t('准备 Bundle')}</span>
          </button>
          <button type="button" onClick={() => void onPackageBundle()} disabled={Boolean(busy) || !canPackageBundle}>
            <Archive size={13} />
            <span>{busy === 'package-bundle' ? t('打包中...') : t('打包 Bundle')}</span>
          </button>
          <button type="button" onClick={() => void onDiagnostics(latestSession?.sessionId)} disabled={Boolean(busy) || !latestSession}>
            <FileDown size={13} />
            <span>{t('诊断包')}</span>
          </button>
        </div>

        {message ? <p className="runtime-manager-message">{message}</p> : null}
      </details>

      {/* Advanced: VM Runtime Pack — collapsed by default */}
      <details className="settings-card runtime-collapsible">
        <summary className="settings-section-header">
          <HardDrive size={16} className="section-icon" />
          <span><h3>{t('VM Runtime Pack')}</h3></span>
          <span className="runtime-manager-status" data-ok={vmRuntime?.installed && vmRuntime?.assetsVerified}>
            {vmRuntime?.runnerTransport || release?.installStrategy || t('未安装')}
          </span>
        </summary>

        <div className="runtime-health-grid">
          <RuntimeMetric label={t('VM Runtime')} ok={vmRuntime?.installed} value={vmRuntime?.installed ? t('已安装') : t('未安装')} />
          <RuntimeMetric label={t('资产大小')} ok={(vmRuntime?.assetBytes ?? 0) > 0} value={formatBytes(vmRuntime?.assetBytes ?? 0)} />
          <RuntimeMetric label={t('SHA 校验')} ok={vmRuntime?.assetsVerified} value={vmRuntime?.assetsVerified ? t('通过') : t('未通过/无校验')} />
          <RuntimeMetric label={t('Guest')} ok={vmRuntime?.guestProtocolReady || vmRuntime?.hcsDirectReady} value={vmRuntime?.runnerTransport || t('未就绪')} />
          <RuntimeMetric label={t('安装包内置')} ok={release?.bundledAvailable} value={release?.bundledAvailable ? t('可用') : t('未内置')} />
          <RuntimeMetric label={t('下载源')} ok={release?.downloadAvailable} value={release?.downloadAvailable ? t('已配置') : t('未配置')} />
        </div>

        <div className="runtime-actions">
          <button type="button" onClick={() => void onStartupTest()} disabled={Boolean(busy) || !canStartupTest}>
            <Play size={13} />
            <span>{busy === 'startup-test' ? t('测试中...') : t('启动测试')}</span>
          </button>
          <button type="button" onClick={() => void onBuildVmAssetsPlan()} disabled={Boolean(busy)}>
            <ShieldCheck size={13} />
            <span>{busy === 'build-vm-assets-plan' ? t('生成中...') : t('资产计划')}</span>
          </button>
          <button type="button" onClick={() => void onBuildVmAssets()} disabled={Boolean(busy)}>
            <Wrench size={13} />
            <span>{busy === 'build-vm-assets' ? t('构建中...') : t('构建资产')}</span>
          </button>
          <button type="button" onClick={() => void onRepair()} disabled={Boolean(busy) || !canRepairRuntime}>
            <Wrench size={13} />
            <span>{busy === 'repair-runtime' ? t('修复中...') : t('修复 Runtime')}</span>
          </button>
          <button type="button" onClick={() => void onValidateRelease()} disabled={Boolean(busy) || !release?.downloadAvailable}>
            <CheckCircle2 size={13} />
            <span>{busy === 'validate-release' ? t('校验中...') : t('校验 Release')}</span>
          </button>
          <button type="button" onClick={() => void onPackageVmBundle()} disabled={Boolean(busy) || !canPackageVmBundle}>
            <Archive size={13} />
            <span>{busy === 'package-vm-bundle' ? t('打包中...') : t('打包 VM Bundle')}</span>
          </button>
          <button type="button" onClick={() => void onDiagnostics(latestSession?.sessionId)} disabled={Boolean(busy) || !latestSession}>
            <FileDown size={13} />
            <span>{t('导出诊断')}</span>
          </button>
        </div>

        <div className="runtime-vm-summary">
          <span title={vmRuntime?.bundlePath}>{t('当前: ')}{vmRuntime?.bundlePath || t('未检测到')}</span>
          {vmRuntime?.missingRequired?.length ? <em>{t('缺失: ')}{vmRuntime.missingRequired.join(', ')}</em> : null}
        </div>
      </details>

      {result ? (
        <details className="settings-section settings-disclosure runtime-result-details">
          <summary>
            <div>
              <h3>{t('最近结果')}</h3>
              <p className="section-desc">{result.ok ? t('操作完成，可展开查看结构化结果。') : t('操作未完成，展开查看错误细节。')}</p>
            </div>
            <span>{result.ok ? 'ok' : 'error'}</span>
          </summary>
          {resultPaths.length ? (
            <div className="runtime-result-paths">
              {resultPaths.map(item => (
                <div key={`${item.label}:${item.path}`} className="runtime-result-path-row">
                  <span>{item.label}</span>
                  <code title={item.path}>{item.path}</code>
                  <button type="button" onClick={() => void openPath(item.path)} disabled={!isLocalPath(item.path)}>
                    <FolderOpen size={12} />
                    {t('打开')}
                  </button>
                  <button type="button" onClick={() => void copyPath(item.path)} disabled={!isLocalPath(item.path)}>
                    <Copy size={12} />
                    {copiedPath === item.path ? t('已复制') : t('复制')}
                  </button>
                </div>
              ))}
            </div>
          ) : null}
          <pre>{safeJson(result)}</pre>
        </details>
      ) : null}
    </div>
  );
});

function RuntimeMetric({ label, ok, value }: { label: string; ok?: boolean; value: string }) {
  return (
    <div className="runtime-health-metric" data-ok={Boolean(ok)}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function RuntimePath({
  copied,
  label,
  onCopy,
  onOpen,
  value,
}: {
  copied: boolean;
  label: string;
  onCopy: (value: string) => void | Promise<void>;
  onOpen: (value: string) => void | Promise<void>;
  value: string;
}) {
  const t = useT();
  const usable = isLocalPath(value);
  return (
    <div className="runtime-path-row">
      <span>{label}</span>
      <code title={value}>{value}</code>
      <div className="runtime-path-actions">
        <button type="button" disabled={!usable} onClick={() => void onOpen(value)} title={t('打开位置')}>
          <FolderOpen size={12} />
        </button>
        <button type="button" disabled={!usable} onClick={() => void onCopy(value)} title={t('复制路径')}>
          <Copy size={12} />
          <span>{copied ? t('已复制') : t('复制')}</span>
        </button>
      </div>
    </div>
  );
}

function actionTitle(id: string, fallback: string, t: (zh: string) => string): string {
  if (id === 'smoke') return t('运行 runtime smoke');
  if (id === 'import') return t('导入 MetisRuntime');
  if (id === 'build-plan') return t('准备 rootfs 构建计划');
  if (id === 'prepare-bundle') return t('准备 Metis Runtime Bundle');
  if (id === 'package-bundle') return t('打包 Runtime Bundle');
  if (id === 'repair-runtime') return t('修复/安装 VM Runtime');
  if (id === 'startup-test') return t('运行启动测试');
  if (id === 'package-vm-bundle') return t('打包 VM Runtime Bundle');
  if (id === 'build-vm-assets') return t('构建真实 VM 资产');
  if (id === 'validate-release') return t('校验 Runtime Release');
  if (id === 'fallback') return t('使用本地副本兜底');
  if (id === 'diagnostics') return t('导出运行时诊断');
  return t(fallback);
}

function actionDescription(id: string, fallback: string, t: (zh: string) => string): string {
  if (id === 'smoke') return t('验证 Python、Node、Git、rg 和产物回收链路。');
  if (id === 'import') return t('把已校验的 rootfs 注册成 Metis 管理的 WSL 发行版。');
  if (id === 'build-plan') return t('生成构建计划；真正长时间构建应走后台任务。');
  if (id === 'prepare-bundle') return t('写入 bundle manifest、origin 溯源文件、安装脚本和 latest 元数据。');
  if (id === 'package-bundle') return t('生成 release zip、SHA256 文件和 runtime release manifest。');
  if (id === 'repair-runtime') return t('从安装包内置资源或下载源安装/修复 VM runtime pack。');
  if (id === 'startup-test') return t('创建运行时会话并验证命令执行、stdout/stderr 和产物回收。');
  if (id === 'package-vm-bundle') return t('生成 v2 release zip、manifest、SHA256SUMS、安装脚本和 latest 元数据。');
  if (id === 'build-vm-assets') return t('构建 rootfs.vhdx、vmlinuz、initrd、metis-bin.vhdx，并可直接打包成 v2 release。');
  if (id === 'validate-release') return t('下载 release manifest/zip，校验 package SHA 和内部 SHA256SUMS。');
  if (id === 'fallback') return t('没有 Docker/WSL 时仍可使用本机 copy-mode。');
  if (id === 'diagnostics') return t('收集最近运行 manifest、命令日志、产物和 patch 摘要。');
  return t(fallback);
}

function selectedRootfs(status: RuntimeManagerStatus | null): { path: string; size: number } {
  const rootfs = status?.rootfs?.selected_rootfs;
  if (!rootfs || typeof rootfs !== 'object') return { path: '', size: 0 };
  const row = rootfs as { path?: unknown; size_bytes?: unknown; sizeBytes?: unknown };
  return {
    path: typeof row.path === 'string' ? row.path : '',
    size: typeof row.size_bytes === 'number' ? row.size_bytes : typeof row.sizeBytes === 'number' ? row.sizeBytes : 0,
  };
}

function formatBytes(size: number): string {
  if (!Number.isFinite(size) || size <= 0) return '0 B';
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  if (size < 1024 * 1024 * 1024) return `${(size / 1024 / 1024).toFixed(1)} MB`;
  return `${(size / 1024 / 1024 / 1024).toFixed(2)} GB`;
}

function isLocalPath(value: string): boolean {
  const path = String(value || '').trim();
  return Boolean(path && !/^https?:\/\//i.test(path) && !/^未检测到$/i.test(path));
}

function recordValue(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' ? (value as Record<string, unknown>) : {};
}

function stringValue(value: unknown): string {
  return typeof value === 'string' ? value : '';
}

function extractResultPaths(result: RuntimeManagerCommandResult | null): Array<{ label: string; path: string }> {
  if (!result) return [];
  const created = recordValue(result.created);
  const run = recordValue(result.run);
  const rows = [
    { label: 'Diagnostics', path: stringValue(result.diagnosticsZip ?? result.diagnostics_zip) },
    { label: 'Summary', path: stringValue(result.summary_path) },
    { label: 'Patch', path: stringValue(result.patch_path) },
    { label: 'Artifacts', path: stringValue(result.artifacts_dir ?? run.artifacts_dir ?? created.artifacts_dir) },
    { label: 'Workspace', path: stringValue(created.workspace_dir) },
  ];
  const seen = new Set<string>();
  return rows.filter(item => {
    if (!item.path || seen.has(item.path)) return false;
    seen.add(item.path);
    return true;
  });
}
