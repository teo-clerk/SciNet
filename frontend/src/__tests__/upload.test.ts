/**
 * What the upload control will hand to the server, and how it keeps count.
 *
 * The filter used to drop everything but .pdf, silently: choosing a book from
 * the picker produced no upload, no error, and no explanation. The server
 * accepts every format the watched folder does, so this has to as well.
 */
import { describe, expect, test } from 'bun:test'

import {
  BATCH_SIZE,
  decodeBatch,
  dragCarriesFiles,
  failedBatch,
  planBatches,
  summarise,
  uploadFiles,
  type BatchResult,
} from '../lib/upload'
import { ACCEPTED, isReadable } from '../panels/UploadButton'

describe('dragCarriesFiles', () => {
  test('a file drag says so in its types', () => {
    expect(dragCarriesFiles(['Files'])).toBe(true)
    expect(dragCarriesFiles(['text/plain', 'Files'])).toBe(true)
  })

  test('dragged text or a link is not a drop', () => {
    expect(dragCarriesFiles(['text/plain', 'text/uri-list'])).toBe(false)
    expect(dragCarriesFiles([])).toBe(false)
  })

  test('no dataTransfer at all is not a drop', () => {
    expect(dragCarriesFiles(null)).toBe(false)
    expect(dragCarriesFiles(undefined)).toBe(false)
  })
})

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

describe('planBatches', () => {
  test('cuts a list into batches of the default size, remainder last', () => {
    const items = Array.from({ length: BATCH_SIZE * 2 + 3 }, (_, i) => i)
    const batches = planBatches(items)

    expect(batches.map((b) => b.length)).toEqual([BATCH_SIZE, BATCH_SIZE, 3])
    expect(batches.flat()).toEqual(items)
  })

  test('an empty list is no batches, not one empty batch', () => {
    expect(planBatches([])).toEqual([])
  })

  test('a list smaller than one batch is one batch', () => {
    expect(planBatches(['a', 'b'], 5)).toEqual([['a', 'b']])
  })

  test('refuses a size that would loop forever', () => {
    expect(() => planBatches([1], 0)).toThrow(RangeError)
  })
})

describe('decodeBatch', () => {
  test('a file the server accepted but did not create was already present', () => {
    const result = decodeBatch({
      queued: 2,
      accepted: [{ outcome: 'created' }, { outcome: 'created' }, { outcome: 'duplicate' }],
      rejected: [{ filename: 'cover.png', reason: 'not a document' }],
    })

    expect(result).toEqual({
      queued: 2,
      duplicates: 1,
      rejected: ['cover.png: not a document'],
    })
  })
})

describe('summarise', () => {
  const batch = (queued: number, duplicates = 0, rejected: string[] = []): BatchResult => ({
    queued,
    duplicates,
    rejected,
  })

  test('adds the batches up', () => {
    expect(summarise([batch(20), batch(17, 3), batch(5, 0, ['x.pdf: bad'])])).toEqual({
      queued: 42,
      duplicates: 3,
      rejected: ['x.pdf: bad'],
    })
  })

  test('nothing sent is all zeros', () => {
    expect(summarise([])).toEqual({ queued: 0, duplicates: 0, rejected: [] })
  })

  test('does not alter its inputs', () => {
    const first = batch(1, 0, ['a'])
    summarise([first, batch(1, 0, ['b'])])
    expect(first.rejected).toEqual(['a'])
  })
})

describe('failedBatch', () => {
  test('every file in a lost batch is rejected by name, with the reason', () => {
    const result = failedBatch(
      [{ name: 'a.pdf' }, { name: 'b.epub' }],
      new Error('upload failed (502)'),
    )

    expect(result).toEqual({
      queued: 0,
      duplicates: 0,
      rejected: ['a.pdf: upload failed (502)', 'b.epub: upload failed (502)'],
    })
  })

  test('a non-Error failure still names the files', () => {
    expect(failedBatch([{ name: 'a.pdf' }], 'boom').rejected).toEqual(['a.pdf: failed'])
  })
})

describe('uploadFiles', () => {
  const files = (n: number) =>
    Array.from({ length: n }, (_, i) => new File(['x'], `paper-${i}.pdf`))

  test('one failed batch does not discard the ones that succeeded', async () => {
    let calls = 0
    const post = async (batch: File[]): Promise<BatchResult> => {
      calls += 1
      if (calls === 2) throw new Error('upload failed (502)')
      return { queued: batch.length, duplicates: 0, rejected: [] }
    }

    const summary = await uploadFiles(files(BATCH_SIZE * 2 + 5), () => {}, post)

    expect(summary.queued).toBe(BATCH_SIZE + 5)
    expect(summary.rejected).toHaveLength(BATCH_SIZE)
    expect(summary.rejected[0]).toBe(`paper-${BATCH_SIZE}.pdf: upload failed (502)`)
  })

  test('reports before the first batch and after every one', async () => {
    const sent: number[] = []
    const post = async (batch: File[]): Promise<BatchResult> => ({
      queued: batch.length,
      duplicates: 0,
      rejected: [],
    })

    await uploadFiles(files(BATCH_SIZE + 1), (p) => sent.push(p.sent), post)

    expect(sent).toEqual([0, BATCH_SIZE, BATCH_SIZE + 1])
  })

  test('no files is a finished upload of nothing', async () => {
    const post = async (): Promise<BatchResult> => {
      throw new Error('should not be called')
    }
    expect(await uploadFiles([], () => {}, post)).toEqual({
      queued: 0,
      duplicates: 0,
      rejected: [],
    })
  })
})
