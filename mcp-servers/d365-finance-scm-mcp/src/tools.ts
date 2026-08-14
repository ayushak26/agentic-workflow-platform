import { McpServer } from "@modelcontextprotocol/server";
import * as z from "zod/v4";
import type { AppConfig } from "./config.js";
import { FnoODataClient } from "./client.js";
import { describeEntitySet, extractEntitySets } from "./metadata.js";
import {
  assertAllowedEntity,
  buildKeyPredicate,
  buildQueryString,
  resolveEntityAlias,
  type FilterCondition,
} from "./odata.js";

const primitiveSchema = z.union([z.string(), z.number(), z.boolean(), z.null()]);
const nonNullPrimitiveSchema = z.union([z.string(), z.number(), z.boolean()]);
const filterSchema = z.object({
  field: z.string().min(1),
  operator: z.enum(["eq", "ne", "gt", "ge", "lt", "le"]),
  value: primitiveSchema,
});
const orderBySchema = z.object({
  field: z.string().min(1),
  direction: z.enum(["asc", "desc"]).default("asc"),
});
const keySchema = z.record(z.string(), nonNullPrimitiveSchema);
const recordSchema = z.record(z.string(), z.unknown());

function ok(data: unknown) {
  return {
    content: [{ type: "text" as const, text: JSON.stringify(data, null, 2) }],
  };
}

function failure(error: unknown) {
  const message = error instanceof Error ? error.message : String(error);
  const details = error && typeof error === "object"
    ? Object.fromEntries(Object.entries(error).filter(([key]) => ["name", "status", "requestId", "body"].includes(key)))
    : {};
  return {
    isError: true,
    content: [{ type: "text" as const, text: JSON.stringify({ error: message, ...details }, null, 2) }],
  };
}

function requireWrites(config: AppConfig, entitySet: string): void {
  if (!config.allowWrites) throw new Error("Writes are disabled. Set FNO_ALLOW_WRITES=true to enable create/update tools.");
  assertAllowedEntity(entitySet, config.writeEntityAllowlist, "write");
}

function requireDeletes(config: AppConfig, entitySet: string): void {
  if (!config.allowDeletes) throw new Error("Deletes are disabled. Set FNO_ALLOW_DELETES=true to enable delete tools.");
  assertAllowedEntity(entitySet, config.deleteEntityAllowlist, "delete");
}

export function registerTools(server: McpServer, client: FnoODataClient, config: AppConfig): void {
  server.registerTool(
    "erp_health",
    {
      description: "Validate authentication and OData connectivity to Dynamics 365 Finance & Operations by reading metadata.",
      inputSchema: z.object({}),
    },
    async () => {
      try {
        const xml = await client.getMetadataXml();
        const entitySets = extractEntitySets(xml);
        return ok({ connected: true, entitySetCount: entitySets.length, writesEnabled: config.allowWrites, deletesEnabled: config.allowDeletes });
      } catch (error) {
        return failure(error);
      }
    },
  );

  server.registerTool(
    "erp_list_entity_sets",
    {
      description: "Discover public OData entity sets exposed by the connected Dynamics 365 Finance & Operations environment. Use this before assuming an entity name.",
      inputSchema: z.object({
        contains: z.string().max(100).optional(),
        limit: z.number().int().min(1).max(500).default(200),
      }),
    },
    async ({ contains, limit }) => {
      try {
        const xml = await client.getMetadataXml();
        let entitySets = extractEntitySets(xml);
        if (contains) {
          const needle = contains.toLocaleLowerCase();
          entitySets = entitySets.filter((name) => name.toLocaleLowerCase().includes(needle));
        }
        return ok({ entitySets: entitySets.slice(0, limit), totalMatches: entitySets.length });
      } catch (error) {
        return failure(error);
      }
    },
  );

  server.registerTool(
    "erp_describe_entity",
    {
      description: "Describe one public Finance & Operations OData entity set, including its complete key fields and exposed properties. Call this before create/update when the schema is not known.",
      inputSchema: z.object({
        entity: z.string().min(1).describe("Exact entity set name or configured alias."),
      }),
    },
    async ({ entity }) => {
      try {
        const entitySet = resolveEntityAlias(entity, config.entityAliases);
        assertAllowedEntity(entitySet, config.readEntityAllowlist, "read");
        const xml = await client.getMetadataXml();
        return ok(describeEntitySet(xml, entitySet));
      } catch (error) {
        return failure(error);
      }
    },
  );

  server.registerTool(
    "erp_query",
    {
      description: "Read records from any permitted public Finance & Operations OData entity. Supports structured filters, field selection, ordering, counting, and bounded paging.",
      inputSchema: z.object({
        entity: z.string().min(1).describe("Exact entity set name or configured alias."),
        select: z.array(z.string().min(1)).max(100).optional(),
        filter: z.array(filterSchema).max(20).default([]),
        orderBy: z.array(orderBySchema).max(10).default([]),
        top: z.number().int().min(1).max(config.maxPageSize).default(Math.min(50, config.maxPageSize)),
        count: z.boolean().default(false),
        allPages: z.boolean().default(false),
        crossCompany: z.boolean().default(false).describe("When true, include legal entities the mapped service user can access."),
      }),
    },
    async ({ entity, select, filter, orderBy, top, count, allPages, crossCompany }) => {
      try {
        const entitySet = resolveEntityAlias(entity, config.entityAliases);
        assertAllowedEntity(entitySet, config.readEntityAllowlist, "read");
        const query = buildQueryString({
          select,
          filter: filter as FilterCondition[],
          orderBy,
          top,
          count,
          crossCompany,
        });
        const path = `${entitySet}${query}`;
        if (allPages) return ok(await client.getAllPages(path));
        return ok(await client.request(path));
      } catch (error) {
        return failure(error);
      }
    },
  );

  server.registerTool(
    "erp_get_record",
    {
      description: "Read one Finance & Operations record by its complete OData entity key. Composite keys are supported.",
      inputSchema: z.object({
        entity: z.string().min(1),
        key: keySchema,
        select: z.array(z.string().min(1)).max(100).optional(),
        crossCompany: z.boolean().default(false),
      }),
    },
    async ({ entity, key, select, crossCompany }) => {
      try {
        const entitySet = resolveEntityAlias(entity, config.entityAliases);
        assertAllowedEntity(entitySet, config.readEntityAllowlist, "read");
        const selectQuery = buildQueryString({ select, crossCompany });
        return ok(await client.request(`${entitySet}${buildKeyPredicate(key)}${selectQuery}`));
      } catch (error) {
        return failure(error);
      }
    },
  );

  server.registerTool(
    "erp_create_record",
    {
      description: "Create a record in a permitted Finance & Operations OData entity. Disabled by default. Use only with validated business data.",
      inputSchema: z.object({
        entity: z.string().min(1),
        record: recordSchema,
      }),
    },
    async ({ entity, record }) => {
      try {
        const entitySet = resolveEntityAlias(entity, config.entityAliases);
        requireWrites(config, entitySet);
        const result = await client.request(entitySet, {
          method: "POST",
          body: record,
        });
        return ok(result ?? { success: true });
      } catch (error) {
        return failure(error);
      }
    },
  );

  server.registerTool(
    "erp_update_record",
    {
      description: "Patch a permitted Finance & Operations record by its complete OData key. Disabled by default.",
      inputSchema: z.object({
        entity: z.string().min(1),
        key: keySchema,
        changes: recordSchema,
      }),
    },
    async ({ entity, key, changes }) => {
      try {
        const entitySet = resolveEntityAlias(entity, config.entityAliases);
        requireWrites(config, entitySet);
        const result = await client.request(`${entitySet}${buildKeyPredicate(key)}`, {
          method: "PATCH",
          body: changes,
        });
        return ok(result ?? { success: true });
      } catch (error) {
        return failure(error);
      }
    },
  );

  server.registerTool(
    "erp_delete_record",
    {
      description: "Delete a permitted Finance & Operations record by complete OData key. Requires both FNO_ALLOW_DELETES=true and an entity delete allowlist in production.",
      inputSchema: z.object({
        entity: z.string().min(1),
        key: keySchema,
      }),
    },
    async ({ entity, key }) => {
      try {
        const entitySet = resolveEntityAlias(entity, config.entityAliases);
        requireDeletes(config, entitySet);
        await client.request(`${entitySet}${buildKeyPredicate(key)}`, { method: "DELETE" });
        return ok({ success: true, deleted: true, entity: entitySet, key });
      } catch (error) {
        return failure(error);
      }
    },
  );
}
