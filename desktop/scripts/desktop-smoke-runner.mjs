import { spawn } from 'node:child_process';
import { createRequire } from 'node:module';
import http from 'node:http';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const require = createRequire(import.meta.url);
const electronBinary = require('electron');
const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, '..');
const vitePort = Number(process.env.METIS_SMOKE_VITE_PORT || 5187);
const smokeTimeoutMs = Number(process.env.METIS_SMOKE_TIMEOUT_MS || 180000);
const rendererUrl = `http://127.0.0.1:${vitePort}`;

function log(prefix, data) {
  const text = data.toString('utf8');
  for (const line of text.split(/\r?\n/)) {
    if (line) {
      process.stdout.write(`[${prefix}] ${line}\n`);
    }
  }
}

function spawnChild(prefix, command, args, options) {
  const child = spawn(command, args, {
    cwd: root,
    env: process.env,
    stdio: ['ignore', 'pipe', 'pipe'],
    windowsHide: true,
    ...options,
  });

  child.stdout.on('data', data => log(prefix, data));
  child.stderr.on('data', data => log(`${prefix}:err`, data));
  return child;
}

function waitForHttp(url, timeoutMs = 30000) {
  const deadline = Date.now() + timeoutMs;

  return new Promise((resolve, reject) => {
    const tick = () => {
      const req = http.get(url, res => {
        res.resume();
        resolve();
      });

      req.on('error', error => {
        if (Date.now() >= deadline) {
          reject(error);
          return;
        }
        setTimeout(tick, 250);
      });

      req.setTimeout(1200, () => {
        req.destroy(new Error('renderer dev server probe timed out'));
      });
    };

    tick();
  });
}

function stop(child) {
  if (!child || child.killed) {
    return;
  }
  try {
    child.kill('SIGTERM');
  } catch {}
}

async function main() {
  const viteBin = path.join(root, 'node_modules', 'vite', 'bin', 'vite.js');
  let smokeResult = null;

  const vite = spawnChild('vite', process.execPath, [
    viteBin,
    '--host',
    '127.0.0.1',
    '--port',
    String(vitePort),
    '--strictPort',
    'true',
  ]);

  try {
    await waitForHttp(rendererUrl);
    let electronStdoutBuffer = '';

    const parseSmokeLine = line => {
      const marker = 'METIS_SMOKE_RESULT:';
      const index = line.indexOf(marker);
      if (index === -1) return;
      const jsonText = line.slice(index + marker.length).trim();
      try {
        smokeResult = JSON.parse(jsonText);
      } catch (error) {
        smokeResult = { ok: false, error: `Could not parse smoke result: ${error.message}` };
      }
    };

    const electron = spawn(electronBinary, [
      '--no-sandbox',
      '--disable-gpu-sandbox',
      '--in-process-gpu',
      '--disable-gpu',
      '--disable-gpu-compositing',
      '--use-gl=swiftshader',
      '--enable-unsafe-swiftshader',
      '--disable-features=VizDisplayCompositor',
      '.',
    ], {
      cwd: root,
      env: {
        ...process.env,
        METIS_DESKTOP_DEV_SERVER: `${rendererUrl}/?metisSmoke=1`,
        METIS_DESKTOP_SMOKE: '1',
        METIS_FAKE_BACKEND: '1',
      },
      stdio: ['ignore', 'pipe', 'pipe'],
      windowsHide: true,
    });

    electron.stdout.on('data', data => {
      const text = data.toString('utf8');
      log('electron', data);
      electronStdoutBuffer += text;
      const lines = electronStdoutBuffer.split(/\r?\n/);
      electronStdoutBuffer = lines.pop() || '';
      for (const line of lines) {
        parseSmokeLine(line);
      }
    });
    electron.stderr.on('data', data => log('electron:err', data));

    const code = await new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        stop(electron);
        reject(new Error(`Electron smoke timed out after ${Math.round(smokeTimeoutMs / 1000)}s`));
      }, smokeTimeoutMs);

      electron.on('error', error => {
        clearTimeout(timer);
        reject(error);
      });
      electron.on('exit', exitCode => {
        clearTimeout(timer);
        if (electronStdoutBuffer) {
          parseSmokeLine(electronStdoutBuffer);
        }
        resolve(exitCode);
      });
    });

    if (!smokeResult) {
      throw new Error(`Electron exited with code ${code}, but no METIS_SMOKE_RESULT was emitted.`);
    }
    if (code !== 0 || !smokeResult.ok) {
      throw new Error(`Desktop smoke failed: ${JSON.stringify(smokeResult)}`);
    }

    process.stdout.write(`METIS_SMOKE_RESULT:${JSON.stringify(smokeResult)}\n`);
  } finally {
    stop(vite);
  }
}

main().catch(error => {
  process.stderr.write(`[smoke:desktop] ${error.stack || error.message || String(error)}\n`);
  process.exit(1);
});
