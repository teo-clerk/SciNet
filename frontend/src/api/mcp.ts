/**
 * The MCP connection recipe, from the API.
 *
 * The transport is stdio, so there is nothing to connect to: the recipe is
 * the command an MCP client spawns, with this checkout's path filled in, and
 * the tools the server reports about itself. `env` is empty unless the API
 * is off its default port.
 */

export interface McpTool {
  name: string
  /** The tool's own docstring, dedented; the first sentence is the blurb. */
  description: string
}

export interface McpRecipe {
  transport: 'stdio'
  /** The key under `mcpServers` — "scinet". */
  name: string
  command: string
  args: string[]
  backend_dir: string
  api_url: string
  env: Record<string, string>
  /** False only if the mcp package will not import in backend/.venv. */
  available: boolean
  tools: McpTool[]
}

export async function fetchMcpRecipe(): Promise<McpRecipe> {
  const res = await fetch('/api/mcp')
  if (!res.ok) throw new Error(`/api/mcp -> ${res.status}`)
  return (await res.json()) as McpRecipe
}
