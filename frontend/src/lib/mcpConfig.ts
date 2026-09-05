/**
 * The MCP recipe, rendered for each client.
 *
 * Every string the AI-tools dialog shows is computed here from what
 * GET /api/mcp reports, so it can be tested without a DOM — and so the
 * default Claude Code line is, byte for byte, the one in docs/MCP.md.
 */
import type { McpRecipe } from '@/api/mcp'

export type McpClient = 'claude-code' | 'claude-desktop' | 'cursor' | 'zed'

export const MCP_CLIENTS: readonly { id: McpClient; label: string }[] = [
  { id: 'claude-code', label: 'Claude Code' },
  { id: 'claude-desktop', label: 'Claude Desktop' },
  { id: 'cursor', label: 'Cursor' },
  { id: 'zed', label: 'Zed' },
]

export interface Snippet {
  title: string
  language: 'bash' | 'json'
  text: string
  /** Where it goes, or how it is run — one line under the code block. */
  fileHint: string
}

/** Bare when it can be. Double quotes otherwise: they work in bash, zsh,
 *  PowerShell and cmd alike, and a Windows path keeps its backslashes. */
const BARE = /^[A-Za-z0-9_\-./:\\~=]+$/

export function shellQuote(arg: string): string {
  if (BARE.test(arg)) return arg
  return `"${arg.replace(/(["$`])/g, '\\$1')}"`
}

/** `claude mcp add scinet [-e K=V] -- uv --directory <backend> run scinet-mcp` */
export function claudeCodeCommand(recipe: McpRecipe): string {
  const env = Object.entries(recipe.env).flatMap(([key, value]) => [
    '-e',
    shellQuote(`${key}=${value}`),
  ])
  const run = [recipe.command, ...recipe.args].map(shellQuote)
  return ['claude', 'mcp', 'add', recipe.name, ...env, '--', ...run].join(' ')
}

function serverEntry(recipe: McpRecipe, alwaysEnv: boolean): Record<string, unknown> {
  const hasEnv = Object.keys(recipe.env).length > 0
  return {
    command: recipe.command,
    args: recipe.args,
    ...(alwaysEnv || hasEnv ? { env: recipe.env } : {}),
  }
}

/** Claude Desktop and Cursor share the `mcpServers` shape. */
export function mcpServersJson(recipe: McpRecipe): string {
  return JSON.stringify({ mcpServers: { [recipe.name]: serverEntry(recipe, false) } }, null, 2)
}

/** Zed's `context_servers`; its documented example always carries `env`. */
export function zedSettingsJson(recipe: McpRecipe): string {
  return JSON.stringify(
    { context_servers: { [recipe.name]: serverEntry(recipe, true) } },
    null,
    2,
  )
}

export function snippetFor(client: McpClient, recipe: McpRecipe): Snippet {
  switch (client) {
    case 'claude-code':
      return {
        title: 'Claude Code',
        language: 'bash',
        text: claudeCodeCommand(recipe),
        fileHint: 'Run it in any terminal. Add -s user to make it available in every project.',
      }
    case 'claude-desktop':
      return {
        title: 'Claude Desktop',
        language: 'json',
        text: mcpServersJson(recipe),
        fileHint:
          'claude_desktop_config.json — macOS: ~/Library/Application Support/Claude/ · ' +
          'Windows: %APPDATA%\\Claude\\. Merge under an existing mcpServers key, ' +
          'then restart Claude Desktop.',
      }
    case 'cursor':
      return {
        title: 'Cursor',
        language: 'json',
        text: mcpServersJson(recipe),
        fileHint: '~/.cursor/mcp.json for every project, or .cursor/mcp.json in one project.',
      }
    case 'zed':
      return {
        title: 'Zed',
        language: 'json',
        text: zedSettingsJson(recipe),
        fileHint:
          'settings.json (Zed › Settings › Open Settings), or ' +
          'Settings › AI › MCP Servers › Add Local Server.',
      }
  }
}

/** The blurb for a tool: its docstring's first sentence, on one line. */
export function firstSentence(description: string): string {
  const flat = description.replace(/\s+/g, ' ').trim()
  const match = /^.*?[.!?](?=\s|$)/.exec(flat)
  return match ? match[0] : flat
}

/** Asks the seven tools can actually answer — none about measured values,
 *  which have no tool yet. */
export const EXAMPLE_PROMPTS: readonly string[] = [
  'What does my library cover? Name the biggest regions and what holds each together.',
  'Have I read anything on active inference? Give me the three closest works and what each argues.',
  'Which of my works is closest to "On Liberty", and where do the two disagree?',
]
