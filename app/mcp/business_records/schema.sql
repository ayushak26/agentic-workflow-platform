-- Business Records MySQL schema.
--
-- Two namespaces, one per fixture source, kept separate rather than merged
-- into one "customer"/"order" concept — they model two different real
-- systems (a Dataverse-style CRM vs. a Finance & Operations ERP) with
-- different fields and different owners of truth. See seed.py for how each
-- table is populated from its source JSON file.
--
-- d365f_* <- app/mcp/d365_finance/fixtures.json
-- crm_*   <- app/mcp/dynamics/fixtures.json

SET NAMES utf8mb4;
SET FOREIGN_KEY_CHECKS = 0;

-- =====================================================================
-- d365f_* — Dynamics 365 Finance & Supply Chain
-- =====================================================================

CREATE TABLE IF NOT EXISTS d365f_customers (
    customerid                  VARCHAR(64) PRIMARY KEY,
    customer_account             VARCHAR(32),
    name                          VARCHAR(255) NOT NULL,
    data_area_id                  VARCHAR(16),
    sales_region                  VARCHAR(32),
    key_account                   BOOLEAN NOT NULL DEFAULT FALSE,
    account_owner_name            VARCHAR(255),
    territory_sales_owner_name    VARCHAR(255),
    sales_engineer_name           VARCHAR(255),
    application_specialist_name  VARCHAR(255),
    service_owner_name            VARCHAR(255),
    credit_hold                   BOOLEAN NOT NULL DEFAULT FALSE,
    INDEX idx_d365f_customers_name (name)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS d365f_employees (
    employeeid    VARCHAR(64) PRIMARY KEY,
    display_name  VARCHAR(255) NOT NULL,
    active        BOOLEAN NOT NULL DEFAULT TRUE,
    team_name     VARCHAR(255)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS d365f_quotes (
    quoteid               VARCHAR(64) PRIMARY KEY,
    quotation_number      VARCHAR(64) NOT NULL,
    customerid            VARCHAR(64),
    status                VARCHAR(32),
    purchase_order_number VARCHAR(64),
    pump_model            VARCHAR(128),
    FOREIGN KEY (customerid) REFERENCES d365f_customers(customerid),
    INDEX idx_d365f_quotes_number (quotation_number),
    INDEX idx_d365f_quotes_po (purchase_order_number)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS d365f_salesorders (
    salesorderid           VARCHAR(64) PRIMARY KEY,
    order_number           VARCHAR(64) NOT NULL,
    purchase_order_number  VARCHAR(64),
    customerid             VARCHAR(64),
    pump_model             VARCHAR(128),
    order_status           VARCHAR(32),
    production_started     BOOLEAN NOT NULL DEFAULT FALSE,
    fulfilment_status      VARCHAR(32),
    delivery_status        VARCHAR(255),
    FOREIGN KEY (customerid) REFERENCES d365f_customers(customerid),
    INDEX idx_d365f_salesorders_number (order_number),
    INDEX idx_d365f_salesorders_po (purchase_order_number)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS d365f_shipments (
    shipmentid             VARCHAR(64) PRIMARY KEY,
    shipment_number        VARCHAR(64) NOT NULL,
    order_number           VARCHAR(64),
    purchase_order_number  VARCHAR(64),
    customerid             VARCHAR(64),
    status                 VARCHAR(32),
    tracking_number        VARCHAR(64),
    delivery_status        VARCHAR(255),
    FOREIGN KEY (customerid) REFERENCES d365f_customers(customerid),
    INDEX idx_d365f_shipments_number (shipment_number),
    INDEX idx_d365f_shipments_order (order_number)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS d365f_invoices (
    invoiceid              VARCHAR(64) PRIMARY KEY,
    invoice_number         VARCHAR(64) NOT NULL,
    order_number           VARCHAR(64),
    purchase_order_number  VARCHAR(64),
    customerid             VARCHAR(64),
    status                 VARCHAR(32),
    total_amount           DECIMAL(14,2),
    currency               VARCHAR(8),
    FOREIGN KEY (customerid) REFERENCES d365f_customers(customerid),
    INDEX idx_d365f_invoices_number (invoice_number)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS d365f_contracts (
    contractid      VARCHAR(64) PRIMARY KEY,
    contract_number VARCHAR(64) NOT NULL,
    customerid      VARCHAR(64),
    name            VARCHAR(255),
    status          VARCHAR(32),
    valid_from      DATE,
    valid_until     DATE,
    FOREIGN KEY (customerid) REFERENCES d365f_customers(customerid),
    INDEX idx_d365f_contracts_number (contract_number)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS d365f_inventory (
    inventoryid         VARCHAR(64) PRIMARY KEY,
    pump_model          VARCHAR(128) NOT NULL,
    availability_status VARCHAR(32),
    lead_time_days      INT,
    INDEX idx_d365f_inventory_model (pump_model)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS d365f_installedunits (
    installedunitid              VARCHAR(64) PRIMARY KEY,
    serial_number                VARCHAR(64) NOT NULL,
    customerid                   VARCHAR(64),
    pump_model                   VARCHAR(128),
    existing_pump_manufacturer   VARCHAR(128),
    site_or_location             VARCHAR(255),
    warranty_active              BOOLEAN NOT NULL DEFAULT FALSE,
    warranty_end_date            DATE,
    existing_pump_performance    VARCHAR(255),
    FOREIGN KEY (customerid) REFERENCES d365f_customers(customerid),
    INDEX idx_d365f_installedunits_serial (serial_number)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS d365f_products (
    productid       VARCHAR(64) PRIMARY KEY,
    product_name    VARCHAR(255) NOT NULL,
    pump_model      VARCHAR(128),
    product_family  VARCHAR(128),
    manufacturer    VARCHAR(128),
    -- The long tail of technical attributes (flow_rate, pressure,
    -- temperature, viscosity, density, motor_power, ...) varies a lot per
    -- product family and is rarely the search key — kept as one JSON
    -- document rather than 25+ mostly-null columns.
    specs           JSON,
    INDEX idx_d365f_products_name (product_name),
    INDEX idx_d365f_products_family (product_family)
) ENGINE=InnoDB;

-- =====================================================================
-- crm_* — Dynamics 365 CRM (Dataverse)
-- =====================================================================

CREATE TABLE IF NOT EXISTS crm_accounts (
    accountid         VARCHAR(64) PRIMARY KEY,
    name              VARCHAR(255) NOT NULL,
    accountnumber     VARCHAR(32),
    industrycode      VARCHAR(64),
    address1_city     VARCHAR(128),
    address1_country  VARCHAR(128),
    telephone1        VARCHAR(32),
    websiteurl        VARCHAR(255),
    statecode         INT NOT NULL DEFAULT 0,
    INDEX idx_crm_accounts_name (name)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS crm_contacts (
    contactid       VARCHAR(64) PRIMARY KEY,
    fullname        VARCHAR(255) NOT NULL,
    emailaddress1   VARCHAR(255),
    telephone1      VARCHAR(32),
    jobtitle        VARCHAR(128),
    accountid       VARCHAR(64),
    FOREIGN KEY (accountid) REFERENCES crm_accounts(accountid),
    INDEX idx_crm_contacts_name (fullname)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS crm_opportunities (
    opportunityid     VARCHAR(64) PRIMARY KEY,
    name              VARCHAR(255) NOT NULL,
    estimatedvalue    DECIMAL(14,2),
    estimatedclosedate DATE,
    statecode         INT NOT NULL DEFAULT 0,
    accountid         VARCHAR(64),
    FOREIGN KEY (accountid) REFERENCES crm_accounts(accountid)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS crm_quotations (
    quoteid            VARCHAR(64) PRIMARY KEY,
    quotation_number   VARCHAR(64) NOT NULL,
    name               VARCHAR(255),
    status             VARCHAR(32),
    totalamount        DECIMAL(14,2),
    accountid          VARCHAR(64),
    opportunityid      VARCHAR(64),
    FOREIGN KEY (accountid) REFERENCES crm_accounts(accountid),
    FOREIGN KEY (opportunityid) REFERENCES crm_opportunities(opportunityid),
    INDEX idx_crm_quotations_number (quotation_number)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS crm_salesorders (
    salesorderid           VARCHAR(64) PRIMARY KEY,
    order_number           VARCHAR(64) NOT NULL,
    purchase_order_number  VARCHAR(64),
    name                   VARCHAR(255),
    createdon              DATETIME,
    confirmed_date         DATETIME,
    status                 VARCHAR(32),
    totalamount            DECIMAL(14,2),
    accountid              VARCHAR(64),
    FOREIGN KEY (accountid) REFERENCES crm_accounts(accountid),
    INDEX idx_crm_salesorders_number (order_number),
    INDEX idx_crm_salesorders_po (purchase_order_number)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS crm_salesorder_products (
    id              INT AUTO_INCREMENT PRIMARY KEY,
    salesorderid    VARCHAR(64) NOT NULL,
    name            VARCHAR(255),
    product_number  VARCHAR(64),
    serial_number   VARCHAR(64),
    quantity        INT NOT NULL DEFAULT 1,
    FOREIGN KEY (salesorderid) REFERENCES crm_salesorders(salesorderid)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS crm_shipments (
    shipmentid      VARCHAR(64) PRIMARY KEY,
    shipment_number VARCHAR(64) NOT NULL,
    status          VARCHAR(32),
    shipped_date    DATETIME,
    delivered_date  DATETIME,
    salesorderid    VARCHAR(64),
    accountid       VARCHAR(64),
    FOREIGN KEY (salesorderid) REFERENCES crm_salesorders(salesorderid),
    FOREIGN KEY (accountid) REFERENCES crm_accounts(accountid),
    INDEX idx_crm_shipments_number (shipment_number)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS crm_service_cases (
    caseid              VARCHAR(64) PRIMARY KEY,
    service_case_number VARCHAR(64) NOT NULL,
    title               VARCHAR(255) NOT NULL,
    status              VARCHAR(32) NOT NULL DEFAULT 'open',
    priority            VARCHAR(16) NOT NULL DEFAULT 'normal',
    serial_number       VARCHAR(64),
    accountid           VARCHAR(64),
    FOREIGN KEY (accountid) REFERENCES crm_accounts(accountid),
    INDEX idx_crm_service_cases_number (service_case_number)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS crm_contracts (
    contractid      VARCHAR(64) PRIMARY KEY,
    contract_number VARCHAR(64) NOT NULL,
    accountid       VARCHAR(64),
    name            VARCHAR(255),
    status          VARCHAR(32),
    FOREIGN KEY (accountid) REFERENCES crm_accounts(accountid),
    INDEX idx_crm_contracts_number (contract_number)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS crm_installed_equipment (
    equipmentid                 VARCHAR(64) PRIMARY KEY,
    serial_number               VARCHAR(64) NOT NULL,
    accountid                   VARCHAR(64),
    product_name                VARCHAR(255),
    pump_model                  VARCHAR(128),
    existing_pump_manufacturer  VARCHAR(128),
    site_or_location            VARCHAR(255),
    existing_pump_performance   VARCHAR(255),
    FOREIGN KEY (accountid) REFERENCES crm_accounts(accountid),
    INDEX idx_crm_installed_equipment_serial (serial_number)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS crm_products (
    productid     VARCHAR(64) PRIMARY KEY,
    product_name  VARCHAR(255) NOT NULL,
    productnumber VARCHAR(64),
    pump_model    VARCHAR(128),
    product_family VARCHAR(128),
    description   VARCHAR(500),
    INDEX idx_crm_products_name (product_name),
    INDEX idx_crm_products_family (product_family)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS crm_activitypointers (
    activityid           VARCHAR(64) PRIMARY KEY,
    activitytypecode     VARCHAR(32),
    subject              VARCHAR(255),
    createdon             DATETIME,
    statecode             INT NOT NULL DEFAULT 0,
    -- Deliberately not a foreign key: Dataverse activities regard many
    -- different entity types (account, contact, case, ...), not just accounts.
    regardingobjectid     VARCHAR(64)
) ENGINE=InnoDB;

SET FOREIGN_KEY_CHECKS = 1;
