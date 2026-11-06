import assert from 'node:assert/strict'
import test from 'node:test'
import { recoverDraft, assertDraftCanSave } from '../lib/manuscript-draft'

test('recovery keeps H0 and three bodies until an explicit resolution', () => {
  const state = recoverDraft(
    { path: 'main.tex', hash: 'H1', text: 'external edit' },
    { hash: 'H0', baseText: 'base', text: 'my draft' }
  )
  assert.equal(state.base.hash, 'H0')
  assert.equal(state.conflict?.base.text, 'base')
  assert.equal(state.conflict?.disk.text, 'external edit')
  // Modal visibility is deliberately absent from the save contract.
  for (const modalOpen of [true, false]) {
    void modalOpen
    assert.throws(() => assertDraftCanSave(state.conflict))
  }
  const reopened = recoverDraft(state.conflict!.disk, {
    hash: state.base.hash,
    baseText: state.base.text,
    text: state.text,
    conflict: state.conflict,
  })
  assert.throws(() => assertDraftCanSave(reopened.conflict))
})
test('unchanged base and explicit disk adoption allow saves', () => {
  const disk = { path: 'main.tex', hash: 'H1', text: 'disk' }
  assert.doesNotThrow(() =>
    assertDraftCanSave(
      recoverDraft(disk, { hash: 'H1', baseText: 'disk', text: 'new edit' }).conflict
    )
  )
  assert.doesNotThrow(() => assertDraftCanSave(recoverDraft(disk, null).conflict))
})
