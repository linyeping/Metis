const fs = require('node:fs')
const crypto = require('node:crypto')
const path = require('node:path')
const readline = require('node:readline')

const BRIDGE_VERSION = '0.1.0'

function parseArgs(argv) {
  const valueAfter = flag => {
    const index = argv.indexOf(flag)
    return index >= 0 ? String(argv[index + 1] || '').trim() : ''
  }
  return {
    help: argv.includes('--help') || argv.includes('-h'),
    version: argv.includes('--version') || argv.includes('-v'),
    externalSessionId: valueAfter('--resume') || valueAfter('--session-id')
  }
}

function textFromUserFrame(frame) {
  if (!frame || frame.type !== 'user') return ''
  const content = frame.message?.content
  if (typeof content === 'string') return content
  if (!Array.isArray(content)) return ''
  return content
    .filter(item => item && item.type === 'text' && typeof item.text === 'string')
    .map(item => item.text)
    .join('\n')
    .trim()
}

function normalizedPath(value) {
  return path.resolve(String(value || '')).replace(/[\\/]+$/, '').toLowerCase()
}

function readBridgeState(stateFile) {
  try {
    const value = JSON.parse(fs.readFileSync(stateFile, 'utf8'))
    return value && typeof value === 'object' && !Array.isArray(value) ? value : {}
  } catch {
    return {}
  }
}

function writeBridgeState(stateFile, state) {
  fs.mkdirSync(path.dirname(stateFile), { recursive: true })
  const temp = `${stateFile}.${process.pid}.${Date.now()}.tmp`
  fs.writeFileSync(temp, `${JSON.stringify(state, null, 2)}\n`, 'utf8')
  fs.renameSync(temp, stateFile)
}

function scopedBridgeStateFile(stateFile, key) {
  const parsed = path.parse(stateFile)
  const digest = crypto.createHash('sha256').update(key).digest('hex')
  return path.join(parsed.dir, parsed.name, `${digest}.json`)
}

async function requestJson(backendUrl, pathname, options = {}) {
  const response = await fetch(`${backendUrl}${pathname}`, {
    ...options,
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) }
  })
  const body = await response.json().catch(() => ({}))
  if (!response.ok) {
    const error = new Error(String(body.error || body.message || `HTTP ${response.status}`))
    error.status = response.status
    error.body = body
    throw error
  }
  return body
}

async function ensureWorkspace(backendUrl, projectDir, projectId) {
  const listed = await requestJson(backendUrl, '/workspaces')
  const existing = (Array.isArray(listed.workspaces) ? listed.workspaces : [])
    .find(item => normalizedPath(item?.path) === normalizedPath(projectDir))
  if (existing?.id) return String(existing.id)
  const created = await requestJson(backendUrl, '/workspaces', {
    method: 'POST',
    body: JSON.stringify({ path: projectDir, name: `Design ${projectId}` })
  })
  if (!created.id) throw new Error('Metis did not return a workspace id for the Design project.')
  return String(created.id)
}

async function ensureSession(options) {
  const { backendUrl, projectDir, projectId, externalSessionId, stateFile } = options
  const workspaceId = await ensureWorkspace(backendUrl, projectDir, projectId)
  const key = `${projectId}:${externalSessionId}`
  const scopedStateFile = scopedBridgeStateFile(stateFile, key)
  const scopedState = readBridgeState(scopedStateFile)
  const legacyState = readBridgeState(stateFile)
  const stored = scopedState.sessionId ? scopedState : legacyState[key]
  if (stored?.sessionId && normalizedPath(stored.projectDir) === normalizedPath(projectDir)) {
    const exists = await requestJson(backendUrl, `/sessions/${encodeURIComponent(stored.sessionId)}`).catch(() => null)
    if (exists?.id && String(exists.workspace_id || '') === workspaceId) return String(exists.id)
  }
  const created = await requestJson(backendUrl, '/sessions', {
    method: 'POST',
    body: JSON.stringify({ workspace_id: workspaceId, mode: 'code', activate: false })
  })
  if (!created.id || created.active !== false) {
    throw new Error('Metis could not create an isolated Design session.')
  }
  writeBridgeState(scopedStateFile, {
    sessionId: String(created.id),
    workspaceId,
    projectDir: path.resolve(projectDir),
    updatedAt: new Date().toISOString()
  })
  return String(created.id)
}

function parseSsePackets(buffer, onPayload) {
  const packets = buffer.split(/\r?\n\r?\n/)
  const rest = packets.pop() || ''
  for (const packet of packets) {
    for (const line of packet.split(/\r?\n/)) {
      if (!line.startsWith('data: ')) continue
      const value = line.slice(6)
      if (value === '[DONE]') onPayload(value)
      else {
        try { onPayload(JSON.parse(value)) } catch {}
      }
    }
  }
  return rest
}

function eventText(event) {
  const payload = event?.payload && typeof event.payload === 'object' ? event.payload : {}
  if (event?.kind === 'message_delta') return String(payload.text || '')
  if (event?.kind === 'content_delta' || event?.kind === 'text_delta') {
    return String(payload.text || payload.delta || event.text || event.delta || '')
  }
  return ''
}

function eventError(event) {
  if (!event || !['run_failed', 'run_canceled', 'error'].includes(String(event.kind || event.type || ''))) return ''
  const payload = event.payload && typeof event.payload === 'object' ? event.payload : {}
  const error = payload.error && typeof payload.error === 'object' ? payload.error : {}
  return String(error.message || payload.message || event.message || 'Metis Design run failed.')
}

async function streamRun(options) {
  const { backendUrl, runId, emit } = options
  const response = await fetch(`${backendUrl}/runs/${encodeURIComponent(runId)}/events?schema=v2&after=0`)
  if (!response.ok || !response.body) throw new Error(`Metis event stream failed (HTTP ${response.status}).`)
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let started = false
  let sawText = false
  let failure = ''
  const startText = () => {
    if (started) return
    started = true
    emit({ type: 'stream_event', event: { type: 'message_start', message: { id: `metis-${runId}` } } })
    emit({ type: 'stream_event', event: { type: 'content_block_start', index: 0, content_block: { type: 'text' } } })
  }
  const accept = payload => {
    if (payload === '[DONE]') return
    const text = eventText(payload)
    if (text) {
      startText()
      sawText = true
      emit({ type: 'stream_event', event: { type: 'content_block_delta', index: 0, delta: { type: 'text_delta', text } } })
    }
    failure = failure || eventError(payload)
  }
  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    buffer = parseSsePackets(buffer, accept)
  }
  if (buffer.trim()) parseSsePackets(`${buffer}\n\n`, accept)
  if (started) emit({ type: 'stream_event', event: { type: 'content_block_stop', index: 0 } })
  return { failure, sawText }
}

async function runBridge(argv = process.argv.slice(2), env = process.env) {
  const args = parseArgs(argv)
  if (args.version) {
    process.stdout.write(`metis-design-bridge ${BRIDGE_VERSION}\n`)
    return 0
  }
  if (args.help) {
    process.stdout.write('Metis managed agent bridge for Open Design.\n')
    return 0
  }

  const backendUrl = String(env.METIS_BACKEND_URL || '').replace(/\/$/, '')
  const token = String(env.METIS_DESIGN_BRIDGE_TOKEN || '')
  const projectId = String(env.OD_PROJECT_ID || '').trim()
  const projectDir = path.resolve(String(env.OD_PROJECT_DIR || process.cwd()))
  const designRoot = path.resolve(String(env.METIS_DESIGN_ROOT || ''))
  const stateFile = path.resolve(String(env.METIS_DESIGN_BRIDGE_STATE_FILE || path.join(designRoot, '..', 'bridge-sessions.json')))
  const externalSessionId = args.externalSessionId || `${projectId}-${Date.now()}`
  if (!/^https?:\/\/(?:127\.0\.0\.1|localhost|\[::1\])(?::\d+)?$/i.test(backendUrl)) {
    throw new Error('Metis backend URL is not a loopback origin.')
  }
  if (!token || !projectId || !normalizedPath(projectDir).startsWith(`${normalizedPath(designRoot)}${path.sep}`)) {
    throw new Error('Design bridge scope is incomplete or outside the managed Design root.')
  }

  const emit = value => process.stdout.write(`${JSON.stringify(value)}\n`)
  emit({ type: 'system', subtype: 'init', session_id: externalSessionId, model: 'metis' })

  let activeRunId = ''
  let started = false
  let resolveCompletion
  let rejectCompletion
  const completion = new Promise((resolve, reject) => {
    resolveCompletion = resolve
    rejectCompletion = reject
  })
  const sessionId = await ensureSession({ backendUrl, projectDir, projectId, externalSessionId, stateFile })
  const input = readline.createInterface({ input: process.stdin, crlfDelay: Infinity })

  const start = async prompt => {
    const created = await requestJson(backendUrl, '/runs', {
      method: 'POST',
      headers: { 'X-Metis-Design-Bridge-Token': token },
      body: JSON.stringify({
        message: prompt,
        session_id: sessionId,
        assistant_id: `design-${Date.now()}`,
        surface_mode: 'code',
        execution_profile: 'local_direct',
        design_bridge: { project_id: projectId }
      })
    })
    activeRunId = String(created.run_id || created.id || '')
    if (!activeRunId) throw new Error('Metis did not return a Design run id.')
    const streamed = await streamRun({ backendUrl, runId: activeRunId, emit })
    const finalRun = await requestJson(backendUrl, `/runs/${encodeURIComponent(activeRunId)}`).catch(() => null)
    const failure = streamed.failure || (finalRun?.status === 'failed' ? String(finalRun.error || 'Metis Design run failed.') : '')
    if (failure) throw new Error(failure)
    emit({
      type: 'result',
      subtype: 'success',
      is_error: false,
      session_id: externalSessionId,
      duration_ms: 0,
      stop_reason: 'end_turn',
      usage: { input_tokens: 0, output_tokens: 0 }
    })
  }

  input.on('line', line => {
    let frame
    try { frame = JSON.parse(line) } catch { return }
    const text = textFromUserFrame(frame)
    if (!text) return
    if (!started) {
      started = true
      void start(text).then(resolveCompletion, rejectCompletion)
      return
    }
    if (activeRunId) {
      void requestJson(backendUrl, `/runs/${encodeURIComponent(activeRunId)}/followups`, {
        method: 'POST',
        body: JSON.stringify({ id: `design-followup-${Date.now()}`, message: text, behavior: 'steer' })
      }).catch(() => {})
    }
  })
  input.on('close', () => {
    if (!started) rejectCompletion(new Error('Open Design did not provide a prompt.'))
  })

  const cancel = () => {
    if (activeRunId) void fetch(`${backendUrl}/runs/${encodeURIComponent(activeRunId)}/cancel`, { method: 'POST' }).catch(() => {})
  }
  process.once('SIGINT', cancel)
  process.once('SIGTERM', cancel)
  try {
    await completion
    return 0
  } finally {
    input.close()
    process.removeListener('SIGINT', cancel)
    process.removeListener('SIGTERM', cancel)
  }
}

if (require.main === module) {
  runBridge().then(
    code => { process.exitCode = code },
    error => {
      const message = error?.message || String(error)
      process.stdout.write(`${JSON.stringify({
        type: 'result',
        subtype: 'error_during_execution',
        is_error: true,
        result: message,
        stop_reason: 'end_turn'
      })}\n`)
      process.stderr.write(`[metis-design-bridge] ${message}\n`)
      process.exitCode = 1
    }
  )
}

module.exports = {
  eventError,
  eventText,
  normalizedPath,
  parseArgs,
  parseSsePackets,
  scopedBridgeStateFile,
  textFromUserFrame
}
