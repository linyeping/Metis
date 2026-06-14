import http from 'node:http';
import net from 'node:net';
import path from 'node:path';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const desktopRoot = path.resolve(__dirname, '..');
const host = '127.0.0.1';
const preferredPort = normalizePort(process.env.METIS_DESKTOP_DEV_PORT, 5174);
const rendererUrl = port => `http://${host}:${port}`;
const viteCli = path.join(desktopRoot, 'node_modules', 'vite', 'bin', 'vite.js');
const electronCli = path.join(desktopRoot, 'node_modules', 'electron', 'cli.js');
const electronArgs = [
  '--no-sandbox',
  '--disable-gpu-sandbox',
  '--in-process-gpu',
  '--disable-gpu',
  '--disable-gpu-compositing',
  '--use-gl=swiftshader',
  '--enable-unsafe-swiftshader',
  '--disable-features=VizDisplayCompositor',
  '.',
];

let renderer = null;
let electron = null;
let shuttingDown = false;
let resolvedPort = preferredPort;

function normalizePort(value, fallback) {
  const port = Number.parseInt(String(value || ''), 10);
  return Number.isInteger(port) && port > 0 && port < 65536 ? port : fallback;
}

function prefixPipe(stream, prefix, target) {
  if (!stream) return;
  let buffer = '';
  stream.on('data', chunk => {
    buffer += chunk.toString('utf8');
    const lines = buffer.split(/\r?\n/);
    buffer = lines.pop() ?? '';
    for (const line of lines) {
      if (!line) continue;
      target.write(`${prefix} ${line}\n`);
    }
  });
  stream.on('end', () => {
    if (buffer) target.write(`${prefix} ${buffer}\n`);
  });
}

function findAvailablePort(startPort) {
  return new Promise((resolve, reject) => {
    let port = startPort;
    const tryNext = () => {
      const server = net.createServer();
      server.once('error', () => {
        port += 1;
        if (port >= 65536) {
          reject(new Error('No free port available for renderer.'));
          return;
        }
        tryNext();
      });
      server.listen(port, host, () => {
        server.close(() => resolve(port));
      });
    };
    tryNext();
  });
}

function waitForRenderer(url, timeoutMs = 60000) {
  return new Promise((resolve, reject) => {
    const deadline = Date.now() + timeoutMs;

    const probe = () => {
      const req = http.get(url, response => {
        response.resume();
        if ((response.statusCode || 500) < 500) {
          resolve();
          return;
        }
        retry(new Error(`renderer probe returned HTTP ${response.statusCode}`));
      });
      req.on('error', retry);
      req.setTimeout(1500, () => req.destroy(new Error('renderer probe timed out')));
    };

    const retry = error => {
      if (Date.now() >= deadline) {
        reject(error);
        return;
      }
      setTimeout(probe, 350);
    };

    probe();
  });
}

function terminate(child) {
  if (!child || child.killed) return;
  try {
    child.kill('SIGTERM');
  } catch {}
  setTimeout(() => {
    if (!child.killed) {
      try {
        child.kill('SIGKILL');
      } catch {}
    }
  }, 1500);
}

function shutdown(code = 0) {
  if (shuttingDown) return;
  shuttingDown = true;
  terminate(electron);
  terminate(renderer);
  setTimeout(() => process.exit(code), 100);
}

function spawnRenderer(port) {
  const child = spawn(
    process.execPath,
    [viteCli, '--host', host, '--port', String(port)],
    {
      cwd: desktopRoot,
      env: process.env,
      stdio: ['inherit', 'pipe', 'pipe'],
      windowsHide: true,
    },
  );
  prefixPipe(child.stdout, '[dev:renderer]', process.stdout);
  prefixPipe(child.stderr, '[dev:renderer]', process.stderr);
  child.once('exit', code => {
    if (shuttingDown) return;
    shutdown(code || 0);
  });
  return child;
}

function spawnElectron(port) {
  const child = spawn(
    process.execPath,
    [electronCli, ...electronArgs],
    {
      cwd: desktopRoot,
      env: {
        ...process.env,
        METIS_DESKTOP_DEV_SERVER: rendererUrl(port),
      },
      stdio: ['inherit', 'pipe', 'pipe'],
      windowsHide: true,
    },
  );
  prefixPipe(child.stdout, '[dev:electron]', process.stdout);
  prefixPipe(child.stderr, '[dev:electron]', process.stderr);
  child.once('exit', code => {
    if (shuttingDown) return;
    shutdown(code || 0);
  });
  return child;
}

async function main() {
  resolvedPort = await findAvailablePort(preferredPort);
  if (resolvedPort !== preferredPort) {
    process.stdout.write(`[dev] Port ${preferredPort} is busy, using ${resolvedPort} instead.\n`);
  }

  renderer = spawnRenderer(resolvedPort);
  await waitForRenderer(rendererUrl(resolvedPort));
  process.stdout.write(`[dev] Renderer ready at ${rendererUrl(resolvedPort)}\n`);
  electron = spawnElectron(resolvedPort);
}

process.on('SIGINT', () => shutdown(0));
process.on('SIGTERM', () => shutdown(0));

main().catch(error => {
  process.stderr.write(`[dev] ${error instanceof Error ? error.message : String(error)}\n`);
  shutdown(1);
});
