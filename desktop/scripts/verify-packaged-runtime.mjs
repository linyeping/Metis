import { listPackage } from '@electron/asar';
import { access, readFile, realpath } from 'node:fs/promises';
import path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const desktopRoot = path.resolve(scriptDir, '..');
const archivePath = path.resolve(
  process.env.METIS_PACKAGED_APP_ASAR
    || path.join(desktopRoot, 'release', 'win-unpacked', 'resources', 'app.asar'),
);
const unpackedRoot = `${archivePath}.unpacked`;
const nodeModulesRoot = await realpath(path.join(desktopRoot, 'node_modules'));

async function exists(target) {
  try {
    await access(target);
    return true;
  } catch {
    return false;
  }
}

function archiveEntry(relativePath) {
  return `/${relativePath.split(path.sep).join('/')}`;
}

async function packageRootFor(name, fromDirectory) {
  const requireFromParent = createRequire(path.join(fromDirectory, 'package.json'));
  try {
    return path.dirname(requireFromParent.resolve(`${name}/package.json`));
  } catch (packageJsonError) {
    for (const lookupRoot of requireFromParent.resolve.paths(name) || []) {
      const candidate = path.join(lookupRoot, ...name.split('/'), 'package.json');
      if (await exists(candidate)) {
        const manifest = JSON.parse(await readFile(candidate, 'utf8'));
        if (manifest.name === name) return path.dirname(candidate);
      }
    }
    let current;
    try {
      current = path.dirname(requireFromParent.resolve(name));
    } catch {
      throw packageJsonError;
    }
    while (current.startsWith(nodeModulesRoot)) {
      const candidate = path.join(current, 'package.json');
      if (await exists(candidate)) {
        const manifest = JSON.parse(await readFile(candidate, 'utf8'));
        if (manifest.name === name) return current;
      }
      const parent = path.dirname(current);
      if (parent === current) break;
      current = parent;
    }
    throw packageJsonError;
  }
}

if (!(await exists(archivePath))) {
  throw new Error(`Packaged app archive is missing: ${archivePath}`);
}

const rootManifest = JSON.parse(await readFile(path.join(desktopRoot, 'package.json'), 'utf8'));
const archiveEntries = new Set(
  listPackage(archivePath).map(entry => entry.replaceAll('\\', '/')),
);
const archivedPackageNames = new Set();
for (const entry of archiveEntries) {
  if (!entry.endsWith('/package.json') || !entry.includes('/node_modules/')) continue;
  const tail = entry.split('/node_modules/').at(-1).split('/');
  archivedPackageNames.add(tail[0].startsWith('@') ? `${tail[0]}/${tail[1]}` : tail[0]);
}
const queue = Object.keys(rootManifest.dependencies || {}).map(name => ({
  name,
  fromDirectory: desktopRoot,
  optional: false,
}));
const visited = new Set();
const packagedDependencies = [];

while (queue.length) {
  const dependency = queue.shift();
  let dependencyRoot;
  try {
    dependencyRoot = await realpath(await packageRootFor(dependency.name, dependency.fromDirectory));
  } catch (error) {
    if (dependency.optional) continue;
    throw new Error(`Cannot resolve production dependency ${dependency.name}: ${error.message}`);
  }
  if (visited.has(dependencyRoot)) continue;
  visited.add(dependencyRoot);

  const relativeRoot = path.relative(nodeModulesRoot, dependencyRoot);
  if (!relativeRoot || relativeRoot.startsWith('..') || path.isAbsolute(relativeRoot)) {
    throw new Error(`Production dependency resolved outside node_modules: ${dependency.name} -> ${dependencyRoot}`);
  }

  const manifestPath = path.join(dependencyRoot, 'package.json');
  const manifest = JSON.parse(await readFile(manifestPath, 'utf8'));
  packagedDependencies.push({ name: manifest.name || dependency.name, relativeRoot });

  for (const name of Object.keys(manifest.dependencies || {})) {
    queue.push({ name, fromDirectory: dependencyRoot, optional: false });
  }
  for (const name of Object.keys(manifest.optionalDependencies || {})) {
    queue.push({ name, fromDirectory: dependencyRoot, optional: true });
  }
}

const missing = [];
for (const dependency of packagedDependencies) {
  const relativeManifest = path.join('node_modules', dependency.relativeRoot, 'package.json');
  if (
    !archivedPackageNames.has(dependency.name)
    && !(await exists(path.join(unpackedRoot, relativeManifest)))
  ) {
    missing.push(dependency.name);
  }
}

// extract-zip is required by the Electron main process before app startup. Keep
// its executable dependency chain explicit so a dependency-pruning regression
// fails the release build instead of crashing only after installation.
for (const relativeFile of [
  'node_modules/extract-zip/index.js',
  'node_modules/debug/src/index.js',
  'node_modules/ms/index.js',
  'node_modules/get-stream/index.js',
  'node_modules/yauzl/index.js',
]) {
  const expectedEntry = archiveEntry(relativeFile);
  if (
    !archiveEntries.has(expectedEntry)
    && !(await exists(path.join(unpackedRoot, relativeFile)))
  ) {
    missing.push(expectedEntry);
  }
}

if (missing.length) {
  throw new Error(
    `Packaged runtime is missing ${missing.length} production dependency file(s):\n`
    + missing.slice(0, 30).map(item => `- ${item}`).join('\n')
    + (missing.length > 30 ? `\n- ... and ${missing.length - 30} more` : ''),
  );
}

process.stdout.write(
  `[verify-packaged-runtime] verified ${packagedDependencies.length} production packages in ${archivePath}\n`,
);
