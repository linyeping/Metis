import { spawn } from 'node:child_process';
import { createRequire } from 'node:module';
import http from 'node:http';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const require = createRequire(import.meta.url);
const electronBinary = require('electron');
const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, '..');
const vitePort = Number(process.env.METIS_PERF_VITE_PORT || 5188);
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
  let perfResult = null;

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
        METIS_DESKTOP_DEV_SERVER: `${rendererUrl}/?metisPerf=1`,
        METIS_DESKTOP_PERF: '1',
        METIS_FAKE_BACKEND: '1',
      },
      stdio: ['ignore', 'pipe', 'pipe'],
      windowsHide: true,
    });

    electron.stdout.on('data', data => {
      const text = data.toString('utf8');
      log('electron', data);
      const marker = 'METIS_PERF_RESULT:';
      const index = text.indexOf(marker);
      if (index !== -1) {
        const jsonText = text.slice(index + marker.length).trim();
        try {
          perfResult = JSON.parse(jsonText);
        } catch (error) {
          perfResult = { ok: false, error: `Could not parse perf result: ${error.message}` };
        }
      }
    });
    electron.stderr.on('data', data => log('electron:err', data));

    const code = await new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        stop(electron);
        reject(new Error('Electron perf timed out after 90s'));
      }, 90000);

      electron.on('error', error => {
        clearTimeout(timer);
        reject(error);
      });
      electron.on('exit', exitCode => {
        clearTimeout(timer);
        resolve(exitCode);
      });
    });

    if (!perfResult) {
      throw new Error(`Electron exited with code ${code}, but no METIS_PERF_RESULT was emitted.`);
    }
    if (code !== 0 || !perfResult.ok) {
      throw new Error(`Desktop perf failed: ${JSON.stringify(perfResult)}`);
    }

    process.stdout.write(`METIS_PERF_RESULT:${JSON.stringify(perfResult)}\n`);
  } finally {
    stop(vite);
  }
}

main().catch(error => {
  process.stderr.write(`[perf:desktop] ${error.stack || error.message || String(error)}\n`);
  process.exit(1);
});

