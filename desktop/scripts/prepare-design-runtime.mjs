import { spawn } from 'node:child_process';
import { cp, mkdir, readFile, rm, stat, writeFile } from 'node:fs/promises';
import path from 'node:path';
import process from 'node:process';
import { fileURLToPath } from 'node:url';

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const desktopRoot = path.resolve(scriptDir, '..');
const repoRoot = path.resolve(desktopRoot, '..');
const sourceRoot = path.resolve(
  process.env.METIS_DESIGN_SOURCE_ROOT || path.join(repoRoot, 'open-design'),
);
const destination = path.join(desktopRoot, 'resources', 'open-design-runtime');
const namespace = 'metis-embedded';
const toolsPackRoot = path.join(sourceRoot, '.tmp', 'tools-pack');
const namespaceRoot = path.join(
  sourceRoot,
  '.tmp',
  'tools-pack',
  'out',
  'win',
  'namespaces',
  namespace,
);

async function exists(target) {
  try {
    await stat(target);
    return true;
  } catch {
    return false;
  }
}

async function run(command, args, cwd, env = {}) {
  await new Promise((resolve, reject) => {
    const windowsPnpm = process.platform === 'win32' && command === 'pnpm';
    const child = spawn(windowsPnpm ? (process.env.ComSpec || 'cmd.exe') : command, windowsPnpm
      ? ['/d', '/s', '/c', 'pnpm.cmd', ...args]
      : args, {
      cwd,
      env: { ...process.env, ...env },
      shell: false,
      stdio: 'inherit',
      windowsHide: true,
    });
    child.once('error', reject);
    child.once('exit', code => code === 0 ? resolve() : reject(new Error(`${command} exited with ${code}`)));
  });
}

const sourcePackage = JSON.parse(await readFile(path.join(sourceRoot, 'package.json'), 'utf8'));
if (sourcePackage.version !== '0.15.1') {
  throw new Error(`Open Design 0.15.1 is required; found ${sourcePackage.version || 'unknown'}`);
}

await run(
  'pnpm',
  ['tools-pack', 'win', 'build', '--to', 'dir', '--portable', '--namespace', namespace],
  sourceRoot,
  { OD_WEB_OUTPUT_MODE: 'standalone' },
);

const builtManifest = JSON.parse(await readFile(path.join(namespaceRoot, 'built-app.json'), 'utf8'));
if (builtManifest.version !== 1 || typeof builtManifest.unpackedRoot !== 'string') {
  throw new Error('Open Design tools-pack returned an invalid built-app manifest');
}
const unpackedRoot = path.resolve(builtManifest.unpackedRoot);
const relativeUnpackedRoot = path.relative(toolsPackRoot, unpackedRoot);
if (
  !relativeUnpackedRoot
  || relativeUnpackedRoot.startsWith('..')
  || path.isAbsolute(relativeUnpackedRoot)
  || path.basename(unpackedRoot).toLowerCase() !== 'win-unpacked'
) {
  throw new Error(`Open Design tools-pack returned an unsafe unpacked root: ${unpackedRoot}`);
}

const required = [
  path.join('resources', 'app', 'prebundled', 'daemon', 'daemon-sidecar.mjs'),
  path.join('resources', 'app', 'prebundled', 'daemon', 'daemon-cli.mjs'),
  path.join('resources', 'app', 'prebundled', 'web-sidecar.mjs'),
  path.join('resources', 'open-design'),
  path.join('resources', 'open-design-web-standalone'),
  path.join('resources', 'dom-to-pptx.bundle.js.gz'),
];
for (const relative of required) {
  if (!(await exists(path.join(unpackedRoot, relative)))) {
    throw new Error(`Open Design packaged runtime is missing ${relative}`);
  }
}

await rm(destination, { recursive: true, force: true });
await mkdir(destination, { recursive: true });
await cp(
  path.join(unpackedRoot, 'resources', 'app', 'prebundled'),
  path.join(destination, 'app', 'prebundled'),
  { recursive: true, dereference: true },
);
await cp(
  path.join(unpackedRoot, 'resources', 'open-design'),
  path.join(destination, 'open-design'),
  { recursive: true, dereference: true },
);
await cp(
  path.join(unpackedRoot, 'resources', 'open-design-web-standalone'),
  path.join(destination, 'web-standalone'),
  { recursive: true, dereference: true },
);
const desktopRendererRoot = path.join(destination, 'app', 'prebundled', 'desktop-renderer');
await mkdir(desktopRendererRoot, { recursive: true });
for (const fileName of ['artifact-export.js', 'deck-capture.js', 'pdf-export.js']) {
  const sourcePath = path.join(sourceRoot, 'apps', 'desktop', 'dist', 'main', fileName);
  if (!(await exists(sourcePath))) throw new Error(`Open Design desktop renderer is missing ${sourcePath}`);
  await cp(sourcePath, path.join(desktopRendererRoot, fileName));
}
await writeFile(path.join(desktopRendererRoot, 'package.json'), '{"type":"module"}\n', 'utf8');
await cp(
  path.join(unpackedRoot, 'resources', 'dom-to-pptx.bundle.js.gz'),
  path.join(desktopRendererRoot, 'dom-to-pptx.bundle.js.gz'),
);
await cp(path.join(sourceRoot, 'LICENSE'), path.join(destination, 'OPEN-DESIGN-LICENSE.txt'));
await writeFile(
  path.join(destination, 'metis-runtime.json'),
  `${JSON.stringify({ provider: 'metis-open-design-source', version: sourcePackage.version, builtAt: new Date().toISOString(), executable: 'Metis' }, null, 2)}\n`,
  'utf8',
);

process.stdout.write(`Prepared Open Design ${sourcePackage.version} runtime at ${destination}\n`);
