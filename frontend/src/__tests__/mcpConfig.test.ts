/**
 * The MCP snippets, byte for byte.
 *
 * The dialog's whole job is that a reader pastes what it shows and it works,
 * so the default Claude Code line is pinned to the one docs/MCP.md gives,
 * and every quoting edge — a space, a Windows path, a non-default port — has
 * its own case.
 */
import { describe, expect, test } from 'bun:test'

import type { McpRecipe } from '../api/mcp'
import {
  EXAMPLE_PROMPTS,
  MCP_CLIENTS,
  claudeCodeCommand,
  firstSentence,
  mcpServersJson,
  shellQuote,
  snippetFor,
  zedSettingsJson,
} from '../lib/mcpConfig'

const recipe = (overrides: Partial<McpRecipe> = {}): McpRecipe => {
  const backend = overrides.backend_dir ?? '/home/me/SciNet/backend'
  return {
    transport: 'stdio',
    name: 'scinet',
    command: 'uv',
    args: ['--directory', backend, 'run', 'scinet-mcp'],
    backend_dir: backend,
    api_url: 'http://127.0.0.1:8000',
    env: {},
    available: true,
    tools: [
      {
        name: 'search_library',
        description:
          'Search the user\'s library.\n\nmode: "semantic" finds papers *about* the query ' +
          'even in other words;\n"fulltext" finds exact words and returns the matching snippet.',
      },
      {
        name: 'region_details',
        // as FastMCP stores it before cleandoc: the body's indentation intact
        description:
          'One region: name, overview, terms, and its most representative\n' +
          '        members (highest cluster confidence first).',
      },
    ],
    ...overrides,
  }
}

describe('claudeCodeCommand', () => {
  test('the default line is the one in docs/MCP.md', () => {
    expect(claudeCodeCommand(recipe())).toBe(
      'claude mcp add scinet -- uv --directory /home/me/SciNet/backend run scinet-mcp',
    )
  })

  test('a directory with a space is quoted', () => {
    const line = claudeCodeCommand(recipe({ backend_dir: '/Users/ann/My Papers/SciNet/backend' }))
    expect(line).toContain('--directory "/Users/ann/My Papers/SciNet/backend" run')
  })

  test('a Windows path stays bare, backslashes intact', () => {
    const line = claudeCodeCommand(recipe({ backend_dir: 'C:\\Users\\ann\\SciNet\\backend' }))
    expect(line).toContain('--directory C:\\Users\\ann\\SciNet\\backend run')
  })

  test('a non-default port is pinned with -e before the --', () => {
    const line = claudeCodeCommand(
      recipe({
        api_url: 'http://127.0.0.1:8123',
        env: { SCINET_API_URL: 'http://127.0.0.1:8123' },
      }),
    )
    expect(line).toStartWith('claude mcp add scinet -e SCINET_API_URL=http://127.0.0.1:8123 -- uv')
  })
})

describe('shellQuote', () => {
  test('escapes what double quotes do not protect', () => {
    expect(shellQuote('a b')).toBe('"a b"')
    expect(shellQuote('say "hi" $HOME')).toBe('"say \\"hi\\" \\$HOME"')
    expect(shellQuote('~/SciNet/backend')).toBe('~/SciNet/backend')
  })
})

describe('mcpServersJson (Claude Desktop, Cursor)', () => {
  test('round-trips to the documented shape, without an env key by default', () => {
    const parsed = JSON.parse(mcpServersJson(recipe()))
    expect(parsed).toEqual({
      mcpServers: {
        scinet: {
          command: 'uv',
          args: ['--directory', '/home/me/SciNet/backend', 'run', 'scinet-mcp'],
        },
      },
    })
  })

  test('carries env only when the recipe has one', () => {
    const env = { SCINET_API_URL: 'http://127.0.0.1:8123' }
    const parsed = JSON.parse(mcpServersJson(recipe({ env })))
    expect(parsed.mcpServers.scinet.env).toEqual(env)
  })

  test('a Windows path survives JSON', () => {
    const dir = 'C:\\Users\\ann\\SciNet\\backend'
    const parsed = JSON.parse(mcpServersJson(recipe({ backend_dir: dir })))
    expect(parsed.mcpServers.scinet.args[1]).toBe(dir)
  })
})

describe('zedSettingsJson', () => {
  test('uses context_servers and always carries env', () => {
    const parsed = JSON.parse(zedSettingsJson(recipe()))
    expect(Object.keys(parsed)).toEqual(['context_servers'])
    expect(parsed.context_servers.scinet.env).toEqual({})
    expect(parsed.context_servers.scinet.args.at(-1)).toBe('scinet-mcp')
  })
})

describe('snippetFor', () => {
  test('one snippet per client, bash for the CLI and json for the rest', () => {
    const languages = MCP_CLIENTS.map((c) => snippetFor(c.id, recipe()).language)
    expect(languages).toEqual(['bash', 'json', 'json', 'json'])
  })

  test('each hint names the file the snippet goes into', () => {
    expect(snippetFor('claude-desktop', recipe()).fileHint).toContain('claude_desktop_config.json')
    expect(snippetFor('cursor', recipe()).fileHint).toContain('mcp.json')
    expect(snippetFor('zed', recipe()).fileHint).toContain('settings.json')
    expect(snippetFor('claude-code', recipe()).fileHint).toContain('-s user')
  })
})

describe('firstSentence', () => {
  test('stops at the first sentence of a multi-paragraph docstring', () => {
    const [search] = recipe().tools
    expect(firstSentence(search!.description)).toBe("Search the user's library.")
  })

  test('joins a sentence FastMCP left split across indented lines', () => {
    const [, region] = recipe().tools
    expect(firstSentence(region!.description)).toBe(
      'One region: name, overview, terms, and its most representative members ' +
        '(highest cluster confidence first).',
    )
  })

  test('a description with no terminator comes back whole', () => {
    expect(firstSentence('  nearest in embedding space  ')).toBe('nearest in embedding space')
  })
})

test('three example asks, each a full sentence', () => {
  expect(EXAMPLE_PROMPTS).toHaveLength(3)
  for (const prompt of EXAMPLE_PROMPTS) expect(prompt).toMatch(/[.?]$/)
})
