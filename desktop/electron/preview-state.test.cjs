const assert = require('node:assert/strict')
const test = require('node:test')
const {
  previewBoundsIntent,
  previewBoundsKey,
  previewOcclusionRestoreIntent
} = require('./preview-state.cjs')

test('preview hidden intent clears cached bounds instead of preserving stale placement', () => {
  const intent = previewBoundsIntent({ visible: false })

  assert.equal(intent.visible, false)
  assert.equal(intent.bounds, null)
  assert.equal(previewOcclusionRestoreIntent(intent.bounds).visible, false)
})

test('tiny preview bounds are treated as hidden and cannot be restored after occlusion', () => {
  const intent = previewBoundsIntent({ visible: true, x: 20, y: 30, width: 4, height: 280 })

  assert.equal(intent.visible, false)
  assert.deepEqual(intent.hiddenBounds, { x: 20, y: 30, width: 4, height: 280 })
  assert.equal(intent.bounds, null)
  assert.equal(previewOcclusionRestoreIntent(intent.bounds).visible, false)
})

test('valid visible preview bounds can be restored after a temporary overlay occlusion', () => {
  const intent = previewBoundsIntent({ visible: true, x: 40.4, y: 52.6, width: 640.2, height: 360.8 })

  assert.equal(intent.visible, true)
  assert.deepEqual(intent.bounds, { x: 40, y: 53, width: 640, height: 361 })
  assert.equal(intent.key, previewBoundsKey(intent.bounds))

  const restore = previewOcclusionRestoreIntent(intent.bounds)
  assert.equal(restore.visible, true)
  assert.deepEqual(restore.bounds, intent.bounds)
  assert.equal(restore.key, intent.key)
})
