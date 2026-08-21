/**
 * What the upload control will hand to the server.
 *
 * The filter used to drop everything but .pdf, silently: choosing a book from
 * the picker produced no upload, no error, and no explanation. The server
 * accepts every format the watched folder does, so this has to as well.
 */
import { describe, expect, test } from 'bun:test'
import { ACCEPTED, isReadable } from '../panels/UploadButton'

describe('upload filter', () => {
  test('accepts every format the backend reads', () => {
    for (const extension of ACCEPTED) {
      expect(isReadable({ name: `book${extension}` })).toBe(true)
    }
  })

  test('is case-insensitive, because Windows is', () => {
    expect(isReadable({ name: 'KUHN.EPUB' })).toBe(true)
    expect(isReadable({ name: 'Paper.PDF' })).toBe(true)
  })

  test('lets an extensionless file through for the server to judge', () => {
    // arXiv downloads arrive named after their identifier alone.
    expect(isReadable({ name: '2504.15673' })).toBe(true)
  })

  test('still refuses what the pipeline cannot read', () => {
    expect(isReadable({ name: 'cover.png' })).toBe(false)
    expect(isReadable({ name: 'library.zip' })).toBe(false)
    expect(isReadable({ name: 'slides.pptx' })).toBe(false)
  })

  test('a dot in a folder name is not an extension', () => {
    expect(isReadable({ name: 'my.papers/2504.15673' })).toBe(true)
  })
})
