/**
 * What the quarantine view has to get right.
 *
 * The reason string is the whole point of the feature: a reader looking at a
 * rejected book needs to know whether to find a DRM-free copy or to start the
 * worker and try again, and those are different words.
 */
import { describe, expect, test } from 'bun:test'

/** Mirrors the server's `_readable_reason`, which produces what the view
 *  renders. The server is the authority — the same cases are pinned in
 *  backend/tests/test_health.py so the two cannot drift unnoticed. */
function readableReason(error: string | null): string {
  if (!error) return 'no diagnosis was recorded'
  const at = error.indexOf(': ')
  if (at === -1) return error.trim()
  const rest = error.slice(at + 2)
  return rest.trim() ? rest.trim() : error.trim()
}

describe('quarantine reasons', () => {
  test('the exception type is not shown to the reader', () => {
    const reason = readableReason(
      'UnreadableDocument: DRM-protected (Amazon encrypted container); ' +
        'no tool can read it.',
    )

    expect(reason).toStartWith('DRM-protected')
    expect(reason).not.toContain('UnreadableDocument')
  })

  test('a diagnosis with no type prefix survives intact', () => {
    expect(readableReason('the file vanished')).toBe('the file vanished')
  })

  test('a bare exception type is kept rather than blanked', () => {
    // Better a useless string than an empty cell that looks like a UI bug.
    expect(readableReason('MemoryError: ')).toBe('MemoryError:')
  })

  test('a missing diagnosis says so', () => {
    expect(readableReason(null)).toBe('no diagnosis was recorded')
  })

  test('a colon inside the message is not a split point', () => {
    const reason = readableReason(
      'UnreadableDocument: no text layer: it was never run through OCR',
    )

    expect(reason).toBe('no text layer: it was never run through OCR')
  })
})
