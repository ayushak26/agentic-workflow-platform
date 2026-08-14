import "dotenv/config";
import { McpServer } from "@modelcontextprotocol/server";
import { serveStdio } from "@modelcontextprotocol/server/stdio";
import { loadConfig } from "./config.js";
import { FnoODataClient } from "./client.js";
import { registerTools } from "./tools.js";

function createServer(): McpServer {
  const config = loadConfig();
  const client = new FnoODataClient(config);
  const server = new McpServer(
    {
      name: "d365-finance-scm-mcp",
      version: "1.0.0",
    },
    {
      capabilities: { tools: {} },
    },
  );

  registerTools(server, client, config);
  return server;
}

void serveStdio(createServer);
console.error("Dynamics 365 Finance & Supply Chain MCP server running on stdio.");
