/**
 * PI Sidecar — Node.js process running the PI Agent SDK for Brain Agent.
 * Sole agentic loop for both chat and code mode.
 *
 * REST API:
 *   POST /query          — start a new agent prompt, returns query_id
 *   GET  /events/{id}    — poll for events (after=N)
 *   POST /cancel/{id}    — abort running query
 *   POST /answer/{id}    — deliver user answer for interactive mode
 *   GET  /health         — health check
 *
 * All providers are OpenAI-compatible. Brain Agent custom tools accessed via
 * HTTP to the main server's /v1/tools/call endpoint.
 */

import http from "node:http";
import fs from "node:fs";
import path from "node:path";
import {
  createAgentSession,
  createCodingTools,
  createReadOnlyTools,
  defineTool,
  SessionManager,
  AuthStorage,
  ModelRegistry,
  SettingsManager,
  DefaultPackageManager,
  DefaultResourceLoader,
  type AgentSession,
} from "@mariozechner/pi-coding-agent";
import type { Model } from "@mariozechner/pi-ai";

const PORT = parseInt(process.env.PI_SIDECAR_PORT || "8422", 10);
const QUERY_TTL = 300_000; // 5 min to keep finished queries
const AGENT_DIR = path.join(process.env.HOME || "~", ".brain-agent", "pi-agent");
const PACKAGES_CONFIG = path.join(process.env.HOME || "~", ".brain-agent", "pi-packages.json");

// ── Package Config Persistence ──────────────────────────────────────────

interface PackageConfig {
  packages: Array<string | { source: string; enabled: boolean }>;
}

function loadPackageConfig(): PackageConfig {
  try {
    if (fs.existsSync(PACKAGES_CONFIG)) {
      return JSON.parse(fs.readFileSync(PACKAGES_CONFIG, "utf-8"));
    }
  } catch {}
  return { packages: [] };
}

function savePackageConfig(cfg: PackageConfig) {
  fs.mkdirSync(path.dirname(PACKAGES_CONFIG), { recursive: true });
  fs.writeFileSync(PACKAGES_CONFIG, JSON.stringify(cfg, null, 2));
}

/** Get list of enabled package sources for SettingsManager */
function getEnabledPackageSources(): string[] {
  const cfg = loadPackageConfig();
  return cfg.packages
    .filter((p) => typeof p === "string" || p.enabled !== false)
    .map((p) => (typeof p === "string" ? p : p.source));
}

/** Build a SettingsManager with packages from our config */
function buildSettingsManager() {
  const enabledSources = getEnabledPackageSources();
  return SettingsManager.inMemory({
    compaction: { enabled: true, reserveTokens: 16000, keepRecentTokens: 8000 },
    retry: { enabled: true },
    packages: enabledSources,
  });
}

/** Build a DefaultPackageManager for install/remove/list operations */
function buildPackageManager(cwd: string = "/tmp") {
  const settingsManager = buildSettingsManager();
  fs.mkdirSync(AGENT_DIR, { recursive: true });
  return new DefaultPackageManager({
    cwd,
    agentDir: AGENT_DIR,
    settingsManager,
  });
}

// ── Types ────────────────────────────────────────────────────────────────

interface SseEvent {
  event: string;
  data: Record<string, unknown>;
  _t: number;
}

interface Query {
  events: SseEvent[];
  done: boolean;
  _finished_at?: number;
  session?: AgentSession;
  abortController?: AbortController;
  answerResolve?: (answer: string) => void;
}

interface QueryPayload {
  message: string;
  model: string;
  system_prompt: string;
  agent_id: string;
  session_id?: string;
  cwd: string;
  tool_defs: ToolDef[];
  server_url: string;
  provider_config: ProviderInfo;
  interactive?: boolean;
  thinking_level?: string;
  skip_project_context?: boolean;
}

interface ProviderInfo {
  name: string;
  base_url: string;
  api_key: string;
}

interface ToolDef {
  name: string;
  description: string;
  input_schema: Record<string, unknown>;
}

// ── State ────────────────────────────────────────────────────────────────

const queries = new Map<string, Query>();
let queryCounter = 0;

function evictStale() {
  const now = Date.now();
  for (const [id, q] of queries) {
    if (q.done && q._finished_at && now - q._finished_at > QUERY_TTL) {
      queries.delete(id);
    }
  }
}

function pushEvent(queryId: string, event: string, data: Record<string, unknown>) {
  const q = queries.get(queryId);
  if (!q) return;
  q.events.push({ event, data, _t: Date.now() });
  if (event === "_result" || event === "error") {
    q.done = true;
    q._finished_at = Date.now();
  }
}

// ── Tool Bridge ──────────────────────────────────────────────────────────

async function callBrainTool(
  serverUrl: string,
  agentId: string,
  sessionId: string | undefined,
  toolName: string,
  args: Record<string, unknown>,
): Promise<string> {
  const payload = JSON.stringify({
    name: toolName,
    args,
    agent_id: agentId,
    session_id: sessionId || "",
  });

  const res = await fetch(`${serverUrl}/v1/tools/call`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: payload,
    signal: AbortSignal.timeout(120_000),
  });

  const data = await res.json() as Record<string, unknown>;
  if (data.error) return `Error: ${data.error}`;
  return String(data.result ?? JSON.stringify(data));
}

function buildBrainTools(
  toolDefs: ToolDef[],
  serverUrl: string,
  agentId: string,
  sessionId?: string,
) {
  return toolDefs.map((td) =>
    defineTool({
      name: td.name,
      label: td.name,
      description: td.description,
      parameters: td.input_schema as any,
      async execute(_toolCallId: string, params: unknown) {
        const result = await callBrainTool(
          serverUrl, agentId, sessionId,
          td.name, params as Record<string, unknown>,
        );
        return {
          content: [{ type: "text" as const, text: result }],
          details: {},
        };
      },
    }),
  );
}

// ── Model Builder ────────────────────────────────────────────────────────

function buildModel(modelId: string, provider: ProviderInfo): Model<any> {
  const baseUrl = provider.base_url.replace(/\/$/, "");
  const isNonOpenAI = !baseUrl.includes("api.openai.com");
  return {
    id: modelId,
    name: modelId,
    api: "openai-completions" as const,
    provider: provider.name,
    baseUrl,
    reasoning: false,
    input: ["text", "image"],
    cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
    contextWindow: 131072,
    maxTokens: 16384,
    compat: isNonOpenAI ? {
      supportsStore: false,
      supportsDeveloperRole: false,
      supportsUsageInStreaming: true,
      maxTokensField: "max_tokens" as const,
    } : undefined,
  };
}

// ── Agent Session Runner ─────────────────────────────────────────────────

async function runQuery(queryId: string, payload: QueryPayload) {
  const q = queries.get(queryId);
  if (!q) return;

  const {
    message, model: modelId, system_prompt, agent_id, session_id,
    cwd, tool_defs, server_url, provider_config, thinking_level,
    skip_project_context,
  } = payload;

  try {
    const model = buildModel(modelId, provider_config);
    const brainTools = buildBrainTools(tool_defs, server_url, agent_id, session_id);

    const authStorage = AuthStorage.create();
    authStorage.setRuntimeApiKey(provider_config.name, provider_config.api_key);
    const modelRegistry = ModelRegistry.inMemory(authStorage);

    const settingsManager = buildSettingsManager();

    // Build resource loader with packages from Brain Agent config
    fs.mkdirSync(AGENT_DIR, { recursive: true });
    const resourceLoader = new DefaultResourceLoader({
      cwd,
      agentDir: AGENT_DIR,
      settingsManager,
      noPromptTemplates: true,
      noThemes: true,
    });
    try { await resourceLoader.reload(); } catch (e) {
      console.error("[PI] Resource loader reload failed:", e);
    }

    const apiKey = provider_config.api_key;
    // createCodingTools → [read, bash, edit, write]
    // createReadOnlyTools → [read, grep, find, ls] — skip index 0 to avoid duplicate read
    const builtInTools = [
      ...createCodingTools(cwd),
      ...createReadOnlyTools(cwd).slice(1),
    ];
    const allTools = [...builtInTools, ...brainTools] as any;
    const { session } = await createAgentSession({
      cwd,
      model,
      thinkingLevel: (thinking_level as any) || "off",
      tools: allTools,
      sessionManager: SessionManager.inMemory(),
      settingsManager,
      authStorage,
      modelRegistry,
      resourceLoader,
    });

    // Ensure API key reaches the stream function
    session.agent.getApiKey = async () => apiKey;

    q.session = session;

    // Append project context files (CLAUDE.md, AGENTS.md, .cursorrules) to PI's default prompt.
    // Skipped for chat mode (skip_project_context=true) — only code mode inherits project files.
    if (!skip_project_context) {
      const contextFiles = ["CLAUDE.md", "AGENTS.md", ".cursorrules"];
      const seen = new Set<string>();
      let dir = cwd;
      let projectContext = "";
      while (dir && dir !== path.dirname(dir)) {
        for (const name of contextFiles) {
          const fp = path.join(dir, name);
          try {
            if (fs.existsSync(fp) && !seen.has(fp)) {
              const content = fs.readFileSync(fp, "utf-8").trim();
              if (content) {
                const rel = path.relative(cwd, fp) || name;
                projectContext += `\n\n# Project Instructions (${rel})\n${content}`;
                seen.add(fp);
              }
            }
          } catch {}
        }
        if (fs.existsSync(path.join(dir, ".git"))) break;
        dir = path.dirname(dir);
      }
      if (projectContext) {
        session.agent.state.systemPrompt += projectContext;
      }
    }

    let tokensIn = 0;
    let tokensOut = 0;
    let fullText = "";
    const toolCalls: Array<{ name: string; args: Record<string, unknown> }> = [];

    session.subscribe((event) => {
      if (q.done) return;
      switch (event.type) {
        case "message_update": {
          const mevt = event.assistantMessageEvent;
          if (mevt.type === "text_delta") {
            const delta = (mevt as any).delta ?? "";
            if (delta) {
              pushEvent(queryId, "text_delta", { text: delta });
              fullText += delta;
            }
          } else if (mevt.type === "thinking_delta") {
            const delta = (mevt as any).delta ?? (mevt as any).thinking ?? "";
            if (delta) {
              pushEvent(queryId, "thinking_delta", { text: delta });
            }
          }
          break;
        }

        case "tool_execution_start":
          toolCalls.push({ name: event.toolName, args: event.args || {} });
          pushEvent(queryId, "tool_call", {
            name: event.toolName,
            args: event.args || {},
          });
          break;

        case "tool_execution_end":
          pushEvent(queryId, "tool_result", {
            name: event.toolName,
            result: event.result?.content?.[0]?.type === "text"
              ? (event.result.content[0] as any).text
              : JSON.stringify(event.result),
            isError: event.isError,
          });
          break;

        case "message_end": {
          const msg = event.message as any;
          if (msg?.usage) {
            tokensIn += (msg.usage.input || 0) + (msg.usage.cacheRead || 0);
            tokensOut += msg.usage.output || 0;
          }
          // Capture text from message_end if streaming didn't
          if (msg?.role === "assistant" && msg?.content && !fullText) {
            for (const c of msg.content) {
              if (c.type === "text" && c.text) fullText += c.text;
            }
          }
          break;
        }

        case "agent_end":
          // Final fallback: extract text from completed messages
          if (!fullText && (event as any).messages) {
            for (const m of (event as any).messages) {
              if ((m as any).role === "assistant" && (m as any).content) {
                for (const c of (m as any).content) {
                  if (c.type === "text") fullText += c.text;
                }
              }
            }
          }
          break;

      }
    });

    const abortController = new AbortController();
    q.abortController = abortController;

    await session.prompt(message);

    let stats: any = null;
    try { stats = session.getSessionStats(); } catch (e) {
      console.error("[PI] getSessionStats failed:", e);
    }

    pushEvent(queryId, "_result", {
      text: fullText,
      tokens_in: stats?.tokens?.input ?? tokensIn,
      tokens_out: stats?.tokens?.output ?? tokensOut,
      tokens_cache_read: stats?.tokens?.cacheRead ?? 0,
      tokens_cache_write: stats?.tokens?.cacheWrite ?? 0,
      tokens_total: stats?.tokens?.total ?? (tokensIn + tokensOut),
      cost: stats?.cost ?? 0,
      context_usage: stats?.contextUsage ?? null,
      tools: toolCalls,
      sdk_session_id: stats?.sessionId ?? null,
    });
  } catch (err: unknown) {
    const errMsg = err instanceof Error ? err.message : String(err);
    console.error(`[PI] Query ${queryId} error: ${errMsg}`);
    pushEvent(queryId, "error", { message: errMsg });
  }
}

// ── HTTP Server ──────────────────────────────────────────────────────────

function readBody(req: http.IncomingMessage): Promise<string> {
  return new Promise((resolve, reject) => {
    const chunks: Buffer[] = [];
    req.on("data", (chunk: Buffer) => chunks.push(chunk));
    req.on("end", () => resolve(Buffer.concat(chunks).toString()));
    req.on("error", reject);
  });
}

function jsonResponse(res: http.ServerResponse, data: unknown, status = 200) {
  const body = JSON.stringify(data);
  res.writeHead(status, {
    "Content-Type": "application/json",
    "Content-Length": Buffer.byteLength(body).toString(),
  });
  res.end(body);
}

const server = http.createServer(async (req, res) => {
  const [path, queryString] = (req.url || "").split("?");
  const params = new URLSearchParams(queryString || "");

  try {
    if (req.method === "GET" && path === "/health") {
      jsonResponse(res, { status: "ok" });
      return;
    }

    if (req.method === "GET" && path?.startsWith("/events/")) {
      const queryId = path.split("/events/")[1];
      const after = parseInt(params.get("after") || "0", 10);
      const q = queries.get(queryId!);
      if (!q) { jsonResponse(res, { error: "query not found" }, 404); return; }
      const events = q.events.slice(after);
      jsonResponse(res, { events, next: after + events.length, done: q.done });
      return;
    }

    if (req.method === "POST" && path === "/query") {
      const body = JSON.parse(await readBody(req)) as QueryPayload;
      evictStale();
      queryCounter++;
      const queryId = `q${queryCounter}`;
      queries.set(queryId, { events: [], done: false });
      runQuery(queryId, body).catch((err) => {
        pushEvent(queryId, "error", { message: String(err) });
      });
      jsonResponse(res, { query_id: queryId });
      return;
    }

    if (req.method === "POST" && path?.startsWith("/cancel/")) {
      const queryId = path.split("/cancel/")[1]!;
      const q = queries.get(queryId);
      if (q) {
        q.done = true;
        q._finished_at = Date.now();
        if (q.session) q.session.abort();
        if (q.answerResolve) { q.answerResolve(""); q.answerResolve = undefined; }
        jsonResponse(res, { status: "cancelled" });
      } else {
        jsonResponse(res, { error: "query not found" }, 404);
      }
      return;
    }

    if (req.method === "POST" && path?.startsWith("/answer/")) {
      const queryId = path.split("/answer/")[1]!;
      const body = JSON.parse(await readBody(req)) as { answer?: string };
      const q = queries.get(queryId);
      if (q?.answerResolve) {
        q.answerResolve(body.answer || "");
        q.answerResolve = undefined;
        jsonResponse(res, { status: "ok" });
      } else {
        jsonResponse(res, { error: "no pending question for this query" }, 404);
      }
      return;
    }

    // ── Package Management Endpoints ──────────────────────────────────────

    if (req.method === "GET" && path === "/packages") {
      try {
        const cfg = loadPackageConfig();
        const pm = buildPackageManager();
        const configured = pm.listConfiguredPackages();
        // Merge our enabled/disabled state with SDK's resolved info
        const packages = cfg.packages.map((p) => {
          const source = typeof p === "string" ? p : p.source;
          const enabled = typeof p === "string" || p.enabled !== false;
          const found = configured.find((c) => c.source === source);
          return {
            source,
            enabled,
            scope: found?.scope || "user",
            installedPath: found?.installedPath || null,
          };
        });
        // Resolve to get resource details
        let resolved: any = null;
        try {
          resolved = await pm.resolve(async () => "skip" as const);
        } catch {}
        jsonResponse(res, { packages, resolved });
      } catch (e: any) {
        jsonResponse(res, { error: e.message }, 500);
      }
      return;
    }

    if (req.method === "POST" && path === "/packages/install") {
      const body = JSON.parse(await readBody(req)) as { source: string };
      if (!body.source) { jsonResponse(res, { error: "source required" }, 400); return; }
      try {
        const pm = buildPackageManager();
        await pm.install(body.source);
        // Add to our config
        const cfg = loadPackageConfig();
        const exists = cfg.packages.some((p) =>
          (typeof p === "string" ? p : p.source) === body.source
        );
        if (!exists) {
          cfg.packages.push(body.source);
          savePackageConfig(cfg);
        }
        jsonResponse(res, { status: "installed", source: body.source });
      } catch (e: any) {
        jsonResponse(res, { error: e.message }, 500);
      }
      return;
    }

    if (req.method === "POST" && path === "/packages/remove") {
      const body = JSON.parse(await readBody(req)) as { source: string };
      if (!body.source) { jsonResponse(res, { error: "source required" }, 400); return; }
      try {
        const pm = buildPackageManager();
        try { await pm.remove(body.source); } catch {}
        // Remove from config
        const cfg = loadPackageConfig();
        cfg.packages = cfg.packages.filter((p) =>
          (typeof p === "string" ? p : p.source) !== body.source
        );
        savePackageConfig(cfg);
        jsonResponse(res, { status: "removed", source: body.source });
      } catch (e: any) {
        jsonResponse(res, { error: e.message }, 500);
      }
      return;
    }

    if (req.method === "POST" && path === "/packages/toggle") {
      const body = JSON.parse(await readBody(req)) as { source: string; enabled: boolean };
      if (!body.source) { jsonResponse(res, { error: "source required" }, 400); return; }
      try {
        const cfg = loadPackageConfig();
        const idx = cfg.packages.findIndex((p) =>
          (typeof p === "string" ? p : p.source) === body.source
        );
        if (idx === -1) { jsonResponse(res, { error: "package not found" }, 404); return; }
        cfg.packages[idx] = { source: body.source, enabled: body.enabled };
        savePackageConfig(cfg);
        jsonResponse(res, { status: "toggled", source: body.source, enabled: body.enabled });
      } catch (e: any) {
        jsonResponse(res, { error: e.message }, 500);
      }
      return;
    }

    if (req.method === "GET" && path === "/packages/search") {
      const query = params.get("q") || "pi-package";
      try {
        // Search npm registry for packages tagged with pi-package
        const npmUrl = `https://registry.npmjs.org/-/v1/search?text=keywords:pi-package+${encodeURIComponent(query)}&size=30`;
        const npmRes = await fetch(npmUrl, {
          headers: { "Accept": "application/json" },
          signal: AbortSignal.timeout(10_000),
        });
        const data = await npmRes.json() as any;
        const results = (data.objects || []).map((obj: any) => ({
          name: obj.package?.name || "",
          version: obj.package?.version || "",
          description: obj.package?.description || "",
          author: obj.package?.author?.name || obj.package?.publisher?.username || "",
          keywords: obj.package?.keywords || [],
          npm_url: obj.package?.links?.npm || "",
          repository: obj.package?.links?.repository || "",
          date: obj.package?.date || "",
        }));
        jsonResponse(res, { results });
      } catch (e: any) {
        jsonResponse(res, { error: e.message }, 500);
      }
      return;
    }

    res.writeHead(404);
    res.end("Not found");
  } catch (err) {
    console.error("Request error:", err);
    res.writeHead(500);
    res.end(JSON.stringify({ error: String(err) }));
  }
});

server.listen(PORT, "127.0.0.1", () => {
  console.log(`PI Sidecar on http://127.0.0.1:${PORT}`);
});
