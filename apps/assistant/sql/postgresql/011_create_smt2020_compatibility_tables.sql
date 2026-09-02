-- Compatibility tables for SMT2020 optional Lotrelease sheets.
-- Some datasets do not contain every optional Lotrelease worksheet.
-- These zero-row tables keep cross-fab DBeaver queries from failing with relation-not-found errors.

CREATE SCHEMA IF NOT EXISTS "fab10";
CREATE SCHEMA IF NOT EXISTS "fab11";
CREATE SCHEMA IF NOT EXISTS "fab12";
CREATE SCHEMA IF NOT EXISTS "fab13";

CREATE TABLE IF NOT EXISTS "fab10"."lotrelease_variable_due_dates" (
    source_row_id BIGSERIAL PRIMARY KEY,
    "product_name" TEXT,
    "route_name" TEXT,
    "lot_name_type" TEXT,
    "priority" INTEGER,
    "superhotlot" TEXT,
    "wafers_per_lot" INTEGER,
    "start_date" TIMESTAMP,
    "due_date" TIMESTAMP,
    "release_scenario" TEXT
);
COMMENT ON TABLE "fab10"."lotrelease_variable_due_dates" IS
    'Compatibility table. Source workbook does not include this SMT2020 worksheet for fab10.';

CREATE TABLE IF NOT EXISTS "fab10"."lotrelease_engineering" (
    source_row_id BIGSERIAL PRIMARY KEY,
    "product_name" TEXT,
    "route_name" TEXT,
    "lot_name_type" TEXT,
    "priority" INTEGER,
    "wafers_per_lot" INTEGER,
    "start_date" TIMESTAMP,
    "due_date" TIMESTAMP,
    "release_scenario" TEXT
);
COMMENT ON TABLE "fab10"."lotrelease_engineering" IS
    'Compatibility table. Source workbook does not include this SMT2020 worksheet for fab10.';

CREATE TABLE IF NOT EXISTS "fab11"."lotrelease_engineering" (
    source_row_id BIGSERIAL PRIMARY KEY,
    "product_name" TEXT,
    "route_name" TEXT,
    "lot_name_type" TEXT,
    "priority" INTEGER,
    "wafers_per_lot" INTEGER,
    "start_date" TIMESTAMP,
    "due_date" TIMESTAMP,
    "release_scenario" TEXT
);
COMMENT ON TABLE "fab11"."lotrelease_engineering" IS
    'Compatibility table. Source workbook does not include this SMT2020 worksheet for fab11.';

CREATE TABLE IF NOT EXISTS "fab12"."lotrelease_variable_due_dates" (
    source_row_id BIGSERIAL PRIMARY KEY,
    "product_name" TEXT,
    "route_name" TEXT,
    "lot_name_type" TEXT,
    "priority" INTEGER,
    "superhotlot" TEXT,
    "wafers_per_lot" INTEGER,
    "start_date" TIMESTAMP,
    "due_date" TIMESTAMP,
    "release_scenario" TEXT
);
COMMENT ON TABLE "fab12"."lotrelease_variable_due_dates" IS
    'Compatibility table. Source workbook does not include this SMT2020 worksheet for fab12.';
