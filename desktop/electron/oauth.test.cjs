const assert = require('node:assert/strict')
const fs = require('node:fs/promises')
const os = require('node:os')
const path = require('node:path')
const test = require('node:test')

const {
  deleteExtensionSecrets,
  decryptStoredExtensionSecrets,
  extensionSecretsStatus,
  saveExtensionSecrets,
} = require('./oauth.cjs')

const safeStorage = {
  isEncryptionAvailable: () => true,
  encryptString: value => Buffer.from(`encrypted:${value}`, 'utf8'),
  decryptString: buffer => buffer.toString('utf8').replace(/^encrypted:/, ''),
}

test('extension secrets are encrypted, merged, injected by env name, and deleted', async () => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'metis-extension-secret-'))
  const app = { getPath: () => root }
  try {
    const first = await saveExtensionSecrets({ app, safeStorage, extensionId: '../sample/plugin', values: { API_TOKEN: 'secret-one' } })
    assert.equal(first.ok, true)
    assert.equal(first.extensionId, 'sample-plugin')

    await saveExtensionSecrets({ app, safeStorage, extensionId: '../sample/plugin', values: { SECOND_TOKEN: 'secret-two' } })
    const encrypted = await fs.readFile(path.join(root, 'extensions', 'sample-plugin.enc'), 'utf8')
    assert.equal(encrypted.includes('secret-one'), false)
    assert.equal(encrypted.includes('secret-two'), false)

    const status = await extensionSecretsStatus({ app, safeStorage, extensionId: 'sample-plugin' })
    assert.deepEqual(status.envNames.sort(), ['API_TOKEN', 'SECOND_TOKEN'])
    assert.deepEqual(await decryptStoredExtensionSecrets({ app, safeStorage }), { API_TOKEN: 'secret-one', SECOND_TOKEN: 'secret-two' })

    await deleteExtensionSecrets({ app, extensionId: 'sample-plugin' })
    assert.equal((await extensionSecretsStatus({ app, safeStorage, extensionId: 'sample-plugin' })).configured, false)
  } finally {
    await fs.rm(root, { recursive: true, force: true })
  }
})
