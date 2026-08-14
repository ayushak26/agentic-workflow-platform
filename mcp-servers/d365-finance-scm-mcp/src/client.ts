import { randomUUID } from "node:crypto";
import type { AppConfig } from "./config.js";
import { FnoHttpError } from "./errors.js";
import { FnoTokenProvider } from "./auth.js";

interface ODataPage<T> {
  value?: T[];
  "@odata.count"?: number;
  "@odata.nextLink"?: string;
  [key: string]: unknown;
}

interface RequestOptions {
  method?: "GET" | "POST" | "PATCH" | "DELETE";
  body?: unknown;
  headers?: Record<string, string>;
  absoluteUrl?: string;
}

const RETRYABLE = new Set([429, 502, 503, 504]);

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function retryAfterMs(headers: Headers, attempt: number): number {
  const raw = headers.get("retry-after");
  if (raw) {
    const seconds = Number(raw);
    if (Number.isFinite(seconds) && seconds >= 0) return Math.min(seconds * 1_000, 30_000);
    const date = Date.parse(raw);
    if (Number.isFinite(date)) return Math.max(0, Math.min(date - Date.now(), 30_000));
  }
  const exponential = Math.min(500 * 2 ** attempt, 8_000);
  return exponential + Math.floor(Math.random() * 250);
}

async function parseResponseBody(response: Response): Promise<unknown> {
  if (response.status === 204) return null;
  const text = await response.text();
  if (!text) return null;
  const contentType = response.headers.get("content-type") ?? "";
  if (contentType.includes("application/json")) {
    try {
      return JSON.parse(text) as unknown;
    } catch {
      return text;
    }
  }
  return text;
}

export class FnoODataClient {
  private readonly tokenProvider: FnoTokenProvider;

  constructor(private readonly config: AppConfig) {
    this.tokenProvider = new FnoTokenProvider(config);
  }

  private makeUrl(relativePath: string, absoluteUrl?: string): string {
    if (absoluteUrl) {
      const next = new URL(absoluteUrl);
      const expectedOrigin = new URL(this.config.baseUrl).origin;
      if (next.origin !== expectedOrigin) {
        throw new Error(`Refusing to follow OData nextLink to a different origin: ${next.origin}`);
      }
      return next.toString();
    }
    const normalized = relativePath.replace(/^\/+/, "");
    return `${this.config.baseUrl}/data/${normalized}`;
  }

  async request<T = unknown>(relativePath: string, options: RequestOptions = {}): Promise<T> {
    const method = options.method ?? "GET";
    for (let attempt = 0; attempt <= this.config.maxRetries; attempt += 1) {
      const token = await this.tokenProvider.getAccessToken();
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), this.config.timeoutMs);
      const clientRequestId = randomUUID();

      try {
        const response = await fetch(this.makeUrl(relativePath, options.absoluteUrl), {
          method,
          signal: controller.signal,
          headers: {
            Authorization: `Bearer ${token}`,
            Accept: "application/json;odata.metadata=minimal",
            "Content-Type": "application/json",
            "OData-MaxVersion": "4.0",
            "OData-Version": "4.0",
            "x-ms-client-request-id": clientRequestId,
            ...options.headers,
          },
          body: options.body === undefined ? undefined : JSON.stringify(options.body),
        });


        if (RETRYABLE.has(response.status) && attempt < this.config.maxRetries) {
          await sleep(retryAfterMs(response.headers, attempt));
          continue;
        }

        const body = await parseResponseBody(response);
        if (!response.ok) {
          const requestId = response.headers.get("request-id") ?? response.headers.get("x-ms-request-id") ?? undefined;
          throw new FnoHttpError({
            status: response.status,
            requestId,
            body,
            message: `Dynamics 365 Finance & Operations request failed: ${method} ${relativePath} -> HTTP ${response.status}`,
          });
        }

        return body as T;
      } catch (error) {
        if (error instanceof FnoHttpError) throw error;
        if (error instanceof Error && error.name === "AbortError") {
          if (attempt < this.config.maxRetries) continue;
          throw new Error(`Dynamics 365 Finance & Operations request timed out after ${this.config.timeoutMs} ms.`);
        }
        if (attempt < this.config.maxRetries) {
          await sleep(Math.min(500 * 2 ** attempt, 4_000));
          continue;
        }
        throw error;
      } finally {
        clearTimeout(timeout);
      }
    }

    throw new Error("Unexpected retry loop termination.");
  }

  async getAllPages<T>(relativePath: string): Promise<{ value: T[]; count?: number; pages: number }> {
    const rows: T[] = [];
    let pages = 0;
    let nextLink: string | undefined;
    let count: number | undefined;

    do {
      if (pages >= this.config.maxPages) break;
      const page = nextLink
        ? await this.request<ODataPage<T>>("", { absoluteUrl: nextLink })
        : await this.request<ODataPage<T>>(relativePath);
      pages += 1;
      if (Array.isArray(page.value)) rows.push(...page.value);
      if (typeof page["@odata.count"] === "number") count = page["@odata.count"];
      nextLink = typeof page["@odata.nextLink"] === "string" ? page["@odata.nextLink"] : undefined;
    } while (nextLink);

    return count === undefined ? { value: rows, pages } : { value: rows, count, pages };
  }

  async getMetadataXml(): Promise<string> {
    const token = await this.tokenProvider.getAccessToken();
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), this.config.timeoutMs);
    try {
      const response = await fetch(`${this.config.baseUrl}/data/$metadata`, {
        signal: controller.signal,
        headers: {
          Authorization: `Bearer ${token}`,
          Accept: "application/xml,text/xml;q=0.9,*/*;q=0.1",
        },
      });
      const text = await response.text();
      if (!response.ok) {
        throw new FnoHttpError({
          status: response.status,
          body: text,
          message: `Failed to read Finance & Operations OData metadata: HTTP ${response.status}`,
        });
      }
      return text;
    } finally {
      clearTimeout(timeout);
    }
  }
}
