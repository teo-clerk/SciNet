/**
 * Upload planning and bookkeeping.
 *
 * Uploads go in batches rather than as one request: a few thousand files is
 * several gigabytes, and a single multipart body that size is a timeout waiting
 * to happen and gives no progress until it finishes. Batching means the count
 * advances steadily and a network hiccup costs one batch, not the whole import.
 *
 * The arithmetic lives here, apart from the button, so the rules — what counts
 * as a duplicate, what a failed batch does to the tally — can be tested
 * without a browser.
 */

/** Everything the backend will read. Kept in step with SUPPORTED_EXTENSIONS in
 *  backend/app/core/paths.py — the upload button and the watched folder have to
 *  agree about what a paper is, or dropping a book in one place works and the
 *  other silently ignores it. */
export const ACCEPTED = [
  '.pdf',
  '.epub',
  '.mobi',
  '.azw3',
  '.djvu',
  '.djv',
  '.docx',
  '.txt',
  '.md',
]

/** A real file extension: a few letters, no digits. Anything else is part of
 *  the name — an arXiv download is called `2504.15673`, and reading `.15673`
 *  as its type is how those got dropped from a library that wanted them. */
export const EXTENSION = /\.[a-z]{1,5}$/

/** Permissive by design, and it has to be: the server decides by reading the
 *  bytes, so the only job here is to spare the user from uploading a folder of
 *  images. Anything without a recognisable extension is passed along for the
 *  server to judge. */
export const isReadable = (file: { name: string }): boolean => {
  const name = file.name.toLowerCase()
  const base = name.slice(
    Math.max(name.lastIndexOf('/'), name.lastIndexOf('\\')) + 1,
  )
  const match = base.match(EXTENSION)
  return match === null || ACCEPTED.includes(match[0])
}

/** Whether a drag carries files at all. Text or a link dragged across the map
 *  must not light the drop overlay, or every selection made in another window
 *  flashes "drop to add" on the way past. */
export const dragCarriesFiles = (types: Iterable<string> | null | undefined): boolean =>
  types !== null && types !== undefined && Array.from(types).includes('Files')

export const BATCH_SIZE = 20

export interface BatchResult {
  queued: number
  duplicates: number
  rejected: string[]
}

export interface UploadSummary {
  queued: number
  duplicates: number
  rejected: string[]
}

export interface Progress extends UploadSummary {
  sent: number
  total: number
}

/** What /api/papers/upload answers with. */
export interface UploadResponse {
  accepted: Array<{ outcome: string }>
  rejected: Array<{ filename: string; reason: string }>
  queued: number
}

export function planBatches<T>(items: T[], size: number = BATCH_SIZE): T[][] {
  if (size < 1) throw new RangeError(`batch size must be positive, got ${size}`)
  return Array.from({ length: Math.ceil(items.length / size) }, (_, i) =>
    items.slice(i * size, (i + 1) * size),
  )
}

/** The server's answer for one batch, in the terms the summary keeps. A file
 *  the server accepted but did not create was already in the library. */
export function decodeBatch(body: UploadResponse): BatchResult {
  return {
    queued: body.queued,
    duplicates: body.accepted.filter((a) => a.outcome !== 'created').length,
    rejected: body.rejected.map((r) => `${r.filename}: ${r.reason}`),
  }
}

/** A batch the request itself lost. Every file in it is reported rejected, by
 *  name, so one failed batch does not discard the ones that succeeded and the
 *  reader can see which files to try again. */
export function failedBatch(
  batch: Array<{ name: string }>,
  error: unknown,
): BatchResult {
  const reason = error instanceof Error ? error.message : 'failed'
  return {
    queued: 0,
    duplicates: 0,
    rejected: batch.map((f) => `${f.name}: ${reason}`),
  }
}

export function summarise(results: BatchResult[]): UploadSummary {
  return results.reduce<UploadSummary>(
    (acc, r) => ({
      queued: acc.queued + r.queued,
      duplicates: acc.duplicates + r.duplicates,
      rejected: [...acc.rejected, ...r.rejected],
    }),
    { queued: 0, duplicates: 0, rejected: [] },
  )
}

export async function uploadBatch(batch: File[]): Promise<BatchResult> {
  const form = new FormData()
  for (const file of batch) form.append('files', file)

  const res = await fetch('/api/papers/upload', { method: 'POST', body: form })
  if (!res.ok) throw new Error(`upload failed (${res.status})`)
  return decodeBatch((await res.json()) as UploadResponse)
}

/**
 * Send every file, a batch at a time, reporting after each one.
 *
 * `post` is injectable so the loop — and what a thrown batch does to the
 * running total — can be exercised without a network.
 */
export async function uploadFiles(
  files: File[],
  onProgress: (p: Progress) => void,
  post: (batch: File[]) => Promise<BatchResult> = uploadBatch,
): Promise<UploadSummary> {
  const total = files.length
  let results: BatchResult[] = []
  let sent = 0
  onProgress({ sent, total, ...summarise(results) })

  for (const batch of planBatches(files)) {
    const result = await post(batch).catch((e: unknown) => failedBatch(batch, e))
    results = [...results, result]
    sent += batch.length
    onProgress({ sent, total, ...summarise(results) })
  }
  return summarise(results)
}
