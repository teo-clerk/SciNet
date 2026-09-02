/**
 * The frame parser and the framing math — the two pure pieces the librarian
 * UI stands on. The parser is fed deliberately hostile chunking: frames
 * split mid-line, heartbeats interleaved, garbage JSON — because network
 * chunk boundaries owe nothing to frame boundaries.
 */
import { describe, expect, test } from 'bun:test'

import { createFrameParser } from '../api/librarian'
import { frameForSet } from '../lib/framing'

describe('frame parser', () => {
  test('a frame split across chunks assembles once complete', () => {
    const parser = createFrameParser()
    expect(parser.push('event: answer_')).toEqual([])
    expect(parser.push('token\ndata: {"te')).toEqual([])
    const frames = parser.push('xt": "hello"}\n\n')
    expect(frames).toEqual([{ kind: 'answer_token', text: 'hello' }])
  })

  test('heartbeat comments are eaten, not surfaced', () => {
    const parser = createFrameParser()
    expect(parser.push(': connected\n\n: heartbeat\n\n')).toEqual([])
  })

  test('several frames in one chunk all come out in order', () => {
    const parser = createFrameParser()
    const frames = parser.push(
      'event: status\ndata: {"text": "thinking"}\n\n' +
        'event: map_directive\ndata: {"type": "highlight", "paper_ids": [7]}\n\n' +
        'event: done\ndata: {"cited": [7], "dropped": []}\n\n',
    )
    expect(frames.map((f) => f.kind)).toEqual(['status', 'map_directive', 'done'])
  })

  test('garbage json is dropped without ending the stream', () => {
    const parser = createFrameParser()
    expect(parser.push('event: status\ndata: {broken\n\n')).toEqual([])
    expect(parser.push('event: status\ndata: {"text": "ok"}\n\n')).toHaveLength(1)
  })
})

describe('frameForSet', () => {
  const positions = new Float32Array([
    0, 0, 0, // index 0
    10, 0, 0, // index 1
    0, 10, 0, // index 2
  ])

  test('one node frames at the single-paper distance', () => {
    const framing = frameForSet([0], positions)
    expect(framing?.target).toEqual([0, 0, 0])
    expect(framing?.distance).toBe(14)
  })

  test('a spread set frames from further back, centred between', () => {
    const framing = frameForSet([1, 2], positions)
    expect(framing?.target).toEqual([5, 5, 0])
    expect(framing!.distance).toBeGreaterThan(14)
  })

  test('an empty set frames nothing', () => {
    expect(frameForSet([], positions)).toBeNull()
  })
})
