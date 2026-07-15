import { spawn } from 'node:child_process';
import { cp, mkdir, readFile, rm, stat, writeFile } from 'node:fs/promises';
import path from 'node:path';
import process from 'node:process';
import { fileURLToPath } from 'node:url';

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const desktopRoot = path.resolve(scriptDir, '..');
const repoRoot = path.resolve(desktopRoot, '..');
const sourceRoot = path.resolve(
  process.env.METIS_DESIGN_SOURCE_ROOT || path.join(repoRoot, '..', 'open-design-main'),
);
const destination = path.join(desktopRoot, 'resources', 'open-design-runtime');
const namespace = 'metis-embedded';
const unpackedRoot = path.join(
  sourceRoot,
  '.tmp',
  'tools-pack',
  'out',
  'win',
  'namespaces',
  namespace,
  'builder',
  'win-unpacked',
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
    const child = spawn(command, args, {
      cwd,
      env: { ...process.env, ...env },
      shell: process.platform === 'win32',
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

const required = [
  'Open Design.exe',
  path.join('resources', 'app', 'prebundled', 'daemon', 'daemon-sidecar.mjs'),
  path.join('resources', 'app', 'prebundled', 'daemon', 'daemon-cli.mjs'),
  path.join('resources', 'app', 'prebundled', 'web-sidecar.mjs'),
  path.join('resources', 'open-design'),
  path.join('resources', 'open-design-web-standalone'),
];
for (const relative of required) {
  if (!(await exists(path.join(unpackedRoot, relative)))) {
    throw new Error(`Open Design packaged runtime is missing ${relative}`);
  }
}

await rm(destination, { recursive: true, force: true });
await mkdir(path.dirname(destination), { recursive: true });
await cp(unpackedRoot, destination, { recursive: true, dereference: true });
await cp(path.join(sourceRoot, 'LICENSE'), path.join(destination, 'OPEN-DESIGN-LICENSE.txt'));
await writeFile(
  path.join(destination, 'metis-runtime.json'),
  `${JSON.stringify({ provider: 'open-design', version: sourcePackage.version, builtAt: new Date().toISOString() }, null, 2)}\n`,
  'utf8',
);

process.stdout.write(`Prepared Open Design ${sourcePackage.version} runtime at ${destination}\n`);
