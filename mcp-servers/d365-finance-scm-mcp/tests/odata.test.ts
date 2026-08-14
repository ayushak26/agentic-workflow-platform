import test from "node:test";
import assert from "node:assert/strict";
import {
  assertEntitySetName,
  buildFilter,
  buildKeyPredicate,
  buildQueryString,
  odataLiteral,
  resolveEntityAlias,
} from "../src/odata.js";
import { describeEntitySet, extractEntitySets } from "../src/metadata.js";

test("escapes OData string literals", () => {
  assert.equal(odataLiteral("O'Brien"), "'O''Brien'");
});

test("builds composite key predicates", () => {
  assert.equal(
    buildKeyPredicate({ dataAreaId: "usmf", CustomerAccount: "C0001" }),
    "(dataAreaId='usmf',CustomerAccount='C0001')",
  );
});

test("builds structured filters", () => {
  assert.equal(
    buildFilter([
      { field: "CustomerAccount", operator: "eq", value: "C0001" },
      { field: "Amount", operator: "gt", value: 0 },
    ]),
    "CustomerAccount eq 'C0001' and Amount gt 0",
  );
});

test("builds encoded OData query strings", () => {
  const query = buildQueryString({
    select: ["CustomerAccount", "Name"],
    filter: [{ field: "CustomerAccount", operator: "eq", value: "C0001" }],
    orderBy: [{ field: "Name", direction: "asc" }],
    top: 25,
    count: true,
    crossCompany: true,
  });
  const params = new URLSearchParams(query.slice(1));
  assert.equal(params.get("$select"), "CustomerAccount,Name");
  assert.equal(params.get("$filter"), "CustomerAccount eq 'C0001'");
  assert.equal(params.get("$orderby"), "Name asc");
  assert.equal(params.get("$top"), "25");
  assert.equal(params.get("$count"), "true");
  assert.equal(params.get("cross-company"), "true");
});

test("rejects invalid entity names", () => {
  assert.throws(() => assertEntitySetName("Customers?$top=100"));
});

test("resolves aliases", () => {
  assert.equal(resolveEntityAlias("customers", { customers: "CustomersV3" }), "CustomersV3");
});

test("extracts entity sets from metadata", () => {
  const xml = `<Schema><EntityContainer><EntitySet Name="CustomersV3" EntityType="x.Customer"/><EntitySet Name="SalesOrderHeadersV2" EntityType="x.Order"/></EntityContainer></Schema>`;
  assert.deepEqual(extractEntitySets(xml), ["CustomersV3", "SalesOrderHeadersV2"]);
});


test("describes entity keys and properties from metadata", () => {
  const xml = `<Schema><EntityType Name="CustomerEntity"><Key><PropertyRef Name="dataAreaId"/><PropertyRef Name="CustomerAccount"/></Key><Property Name="dataAreaId" Type="Edm.String" Nullable="false"/><Property Name="CustomerAccount" Type="Edm.String" Nullable="false"/><Property Name="Name" Type="Edm.String"/></EntityType><EntityContainer><EntitySet Name="CustomersV3" EntityType="Microsoft.Dynamics.DataEntities.CustomerEntity"/></EntityContainer></Schema>`;
  const result = describeEntitySet(xml, "CustomersV3");
  assert.deepEqual(result.keys, ["dataAreaId", "CustomerAccount"]);
  assert.equal(result.properties.length, 3);
  assert.equal(result.properties[0]?.nullable, false);
});
