const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const http = require('node:http')
const os = require('node:os')
const path = require('node:path')
const { spawn } = require('node:child_process')
const { once } = require('node:events')

const {
  eventError,
  eventText,
  parseArgs,
  parseSsePackets,
  scopedBridgeStateFile,
  textFromUserFrame
} = require('./design-agent-bridge.cjs')

test('bridge reads Open Design stream-json prompts and resume ids', () => {
  assert.equal(parseArgs(['--resume', 'od-session']).externalSessionId, 'od-session')
  assert.equal(textFromUserFrame({
    type: 'user',
    message: { content: [{ type: 'text', text: 'Build the prototype' }] }
  }), 'Build the prototype')
})

test('bridge stores each Open Design session independently', () => {
  const first = scopedBridgeStateFile('C:\\Metis\\bridge-sessions.json', 'p1:s1')
  const second = scopedBridgeStateFile('C:\\Metis\\bridge-sessions.json', 'p1:s2')
  assert.notEqual(first, second)
  assert.match(first, /bridge-sessions[\\/][a-f0-9]{64}\.json$/)
})

test('bridge parses chunked Metis SSE packets and v2 events', () => {
  const values = []
  const rest = parseSsePackets('data: {"kind":"message_delta","payload":{"text":"Hi"}}\n\ndata: [DO', value => values.push(value))
  assert.equal(rest, 'data: [DO')
  parseSsePackets(`${rest}NE]\n\n`, value => values.push(value))
  assert.equal(eventText(values[0]), 'Hi')
  assert.equal(values[1], '[DONE]')
  assert.equal(eventError({ kind: 'run_failed', payload: { error: { message: 'failed' } } }), 'failed')
})

test('bridge completes a scoped Design run through the Metis HTTP contract', async () => {
  const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'metis-design-bridge-'))
  const designRoot = path.join(tempRoot, 'projects')
  const projectId = 'project-one'
  const projectDir = path.join(designRoot, projectId)
  fs.mkdirSync(projectDir, { recursive: true })
  const calls = []

  const server = http.createServer((request, response) => {
    const chunks = []
    request.on('data', chunk => chunks.push(chunk))
    request.on('end', () => {
      const bodyText = Buffer.concat(chunks).toString('utf8')
      const body = bodyText ? JSON.parse(bodyText) : {}
      calls.push({ method: request.method, url: request.url, body, token: request.headers['x-metis-design-bridge-token'] })

      if (request.method === 'GET' && request.url === '/workspaces') {
        response.setHeader('content-type', 'application/json')
        return response.end(JSON.stringify({ workspaces: [] }))
      }
      if (request.method === 'POST' && request.url === '/workspaces') {
        response.setHeader('content-type', 'application/json')
        return response.end(JSON.stringify({ id: 'workspace-one' }))
      }
      if (request.method === 'POST' && request.url === '/sessions') {
        response.setHeader('content-type', 'application/json')
        return response.end(JSON.stringify({ id: 'session-one', active: false }))
      }
      if (request.method === 'POST' && request.url === '/runs') {
        fs.writeFileSync(path.join(projectDir, 'index.html'), '<h1>Metis Design Bridge Works</h1>\n', 'utf8')
        response.setHeader('content-type', 'application/json')
        return response.end(JSON.stringify({ run_id: 'run-one' }))
      }
      if (request.method === 'GET' && request.url === '/runs/run-one/events?schema=v2&after=0') {
        response.setHeader('content-type', 'text/event-stream')
        return response.end('data: {"kind":"message_delta","payload":{"text":"Created index.html"}}\n\ndata: [DONE]\n\n')
      }
      if (request.method === 'GET' && request.url === '/runs/run-one') {
        response.setHeader('content-type', 'application/json')
        return response.end(JSON.stringify({ id: 'run-one', status: 'done' }))
      }
      response.statusCode = 404
      response.setHeader('content-type', 'application/json')
      response.end(JSON.stringify({ error: 'not found' }))
    })
  })

  try {
    server.listen(0, '127.0.0.1')
    await once(server, 'listening')
    const port = server.address().port
    const child = spawn(process.execPath, [path.join(__dirname, 'design-agent-bridge.cjs'), '--session-id', 'od-session'], {
      cwd: projectDir,
      env: {
        ...process.env,
        METIS_BACKEND_URL: `http://127.0.0.1:${port}`,
        METIS_DESIGN_BRIDGE_TOKEN: 'design-secret',
        METIS_DESIGN_ROOT: designRoot,
        METIS_DESIGN_BRIDGE_STATE_FILE: path.join(tempRoot, 'bridge-sessions.json'),
        OD_PROJECT_ID: projectId,
        OD_PROJECT_DIR: projectDir
      },
      stdio: ['pipe', 'pipe', 'pipe']
    })
    let stdout = ''
    let stderr = ''
    child.stdout.on('data', chunk => { stdout += chunk.toString('utf8') })
    child.stderr.on('data', chunk => { stderr += chunk.toString('utf8') })
    child.stdin.end(`${JSON.stringify({ type: 'user', message: { content: [{ type: 'text', text: 'Build it' }] } })}\n`)
    const [code] = await once(child, 'close')

    const frames = stdout.trim().split(/\r?\n/).map(line => JSON.parse(line))
    const createSession = calls.find(call => call.method === 'POST' && call.url === '/sessions')
    const createRun = calls.find(call => call.method === 'POST' && call.url === '/runs')
    assert.equal(code, 0, stderr)
    assert.equal(createSession.body.activate, false)
    assert.equal(createRun.token, 'design-secret')
    assert.equal(createRun.body.design_bridge.project_id, projectId)
    assert.match(fs.readFileSync(path.join(projectDir, 'index.html'), 'utf8'), /Metis Design Bridge Works/)
    assert.ok(frames.some(frame => frame.type === 'stream_event' && frame.event?.type === 'content_block_delta'))
    assert.ok(frames.some(frame => frame.type === 'result' && frame.subtype === 'success'))
  } finally {
    await new Promise(resolve => server.close(resolve))
    fs.rmSync(tempRoot, { recursive: true, force: true })
  }
})
