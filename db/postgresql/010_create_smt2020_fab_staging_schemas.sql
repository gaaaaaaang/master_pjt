-- SMT2020 General Data staging schemas for PostgreSQL.
-- Dataset mapping: dataset 1=fab10, dataset 2=fab11, dataset 3=fab12, dataset 4=fab13.
-- Each Excel worksheet is represented as one table in its mapped fab schema.

-- dataset 1 -> fab10: SMT_2020 - Final/General Data/dataset 1/SMT_2020_Model_Data_-_HVLM.xlsx
CREATE SCHEMA IF NOT EXISTS "fab10";

CREATE TABLE IF NOT EXISTS "fab10"."toolgroups" (
    source_row_id BIGSERIAL PRIMARY KEY,
    "area" TEXT,
    "toolgroup" TEXT,
    "number_of_tools" INTEGER,
    "cascadingtool" TEXT,
    "bacthingtool" TEXT,
    "batchcriterion" TEXT,
    "batching_unit" TEXT,
    "loadingtime" INTEGER,
    "lt_units" TEXT,
    "unloadingtime" INTEGER,
    "ult_units" TEXT,
    "toolgrouplocation" TEXT,
    "dispatching" TEXT,
    "ranking_1" TEXT,
    "ranking_2" TEXT,
    "ranking_3" TEXT,
    "tool_wake_up_ranking" TEXT
);
COMMENT ON TABLE "fab10"."toolgroups" IS 'SMT2020 General Data sheet: Toolgroups; header row: 1';

CREATE TABLE IF NOT EXISTS "fab10"."pm" (
    source_row_id BIGSERIAL PRIMARY KEY,
    "pm_event_name" TEXT,
    "pm_event_valid_for_type" TEXT,
    "type_name" TEXT,
    "pm_type" TEXT,
    "mtbeforepm" INTEGER,
    "mtbpm_units" TEXT,
    "ttr_distribution" TEXT,
    "mean" NUMERIC,
    "offset" NUMERIC,
    "ttr_units" TEXT,
    "first_one_at_distribution" TEXT,
    "foa" NUMERIC,
    "foa_units" TEXT
);
COMMENT ON TABLE "fab10"."pm" IS 'SMT2020 General Data sheet: PM; header row: 1';

CREATE TABLE IF NOT EXISTS "fab10"."breakdown" (
    source_row_id BIGSERIAL PRIMARY KEY,
    "down_event_name" TEXT,
    "down_event_valid_for_type" TEXT,
    "type_name" TEXT,
    "down_type" TEXT,
    "ttf_distribution" TEXT,
    "mttf" INTEGER,
    "mttf_units" TEXT,
    "ttr_distribution" TEXT,
    "mttr" NUMERIC,
    "mttr_units" TEXT,
    "first_one_at_distribution" TEXT,
    "foa" INTEGER,
    "foa_units" TEXT
);
COMMENT ON TABLE "fab10"."breakdown" IS 'SMT2020 General Data sheet: Breakdown; header row: 1';

CREATE TABLE IF NOT EXISTS "fab10"."route_product_3" (
    source_row_id BIGSERIAL PRIMARY KEY,
    "route" TEXT,
    "step" TEXT,
    "step_description" TEXT,
    "area" TEXT,
    "toolgroup" TEXT,
    "processing_unit" TEXT,
    "processingtime_distribution" TEXT,
    "mean" NUMERIC,
    "offset" NUMERIC,
    "pt_units" TEXT,
    "cascading_interval" NUMERIC,
    "c_units" TEXT,
    "batch_minimum" INTEGER,
    "batch_maximum" INTEGER,
    "setup" TEXT,
    "when" TEXT,
    "setup_distribution" TEXT,
    "setup_time" INTEGER,
    "offset_2" TEXT,
    "st_units" TEXT,
    "step_for_ltl_dedication" TEXT,
    "rework_probability_in_percent" NUMERIC,
    "r_unit" TEXT,
    "step_for_rework" TEXT,
    "processing_probability_in_percent_sampling" INTEGER,
    "step_for_critical_queue_time" TEXT,
    "cqt" INTEGER,
    "cqtunits" TEXT
);
COMMENT ON TABLE "fab10"."route_product_3" IS 'SMT2020 General Data sheet: Route_Product_3; header row: 1';

CREATE TABLE IF NOT EXISTS "fab10"."route_product_4" (
    source_row_id BIGSERIAL PRIMARY KEY,
    "route" TEXT,
    "step" TEXT,
    "step_description" TEXT,
    "area" TEXT,
    "toolgroup" TEXT,
    "processing_unit" TEXT,
    "processingtime_distribution" TEXT,
    "mean" NUMERIC,
    "offset" NUMERIC,
    "pt_units" TEXT,
    "cascading_interval" NUMERIC,
    "c_units" TEXT,
    "batch_minimum" INTEGER,
    "batch_maximum" INTEGER,
    "setup" TEXT,
    "when" TEXT,
    "setup_distribution" TEXT,
    "setup_time" INTEGER,
    "offset_2" TEXT,
    "st_units" TEXT,
    "step_for_ltl_dedication" TEXT,
    "rework_probability_in_percent" NUMERIC,
    "r_unit" TEXT,
    "step_for_rework" TEXT,
    "processing_probability_in_percent_sampling" INTEGER,
    "step_for_critical_queue_time" TEXT,
    "cqt" INTEGER,
    "cqtunits" TEXT
);
COMMENT ON TABLE "fab10"."route_product_4" IS 'SMT2020 General Data sheet: Route_Product_4; header row: 1';

CREATE TABLE IF NOT EXISTS "fab10"."lotrelease" (
    source_row_id BIGSERIAL PRIMARY KEY,
    "product_name" TEXT,
    "route_name" TEXT,
    "lot_name_type" TEXT,
    "priority" INTEGER,
    "superhotlot" TEXT,
    "wafers_per_lot" INTEGER,
    "start_date" TIMESTAMP,
    "release_distribution" TEXT,
    "release_interval" NUMERIC,
    "r_units" TEXT,
    "lots_per_release" INTEGER,
    "due_date" TIMESTAMP,
    "release_scenario" TEXT
);
COMMENT ON TABLE "fab10"."lotrelease" IS 'SMT2020 General Data sheet: Lotrelease; header row: 1';

CREATE TABLE IF NOT EXISTS "fab10"."setups" (
    source_row_id BIGSERIAL PRIMARY KEY,
    "setup_group_name" TEXT,
    "current_setup" TEXT,
    "new_setup" TEXT,
    "setup_time" INTEGER,
    "st_units" TEXT,
    "minmal_number_of_runs" INTEGER
);
COMMENT ON TABLE "fab10"."setups" IS 'SMT2020 General Data sheet: Setups; header row: 1';

CREATE TABLE IF NOT EXISTS "fab10"."setup_matrix_implant_gas" (
    source_row_id BIGSERIAL PRIMARY KEY,
    "col_001" TEXT,
    "product_route" TEXT,
    "step" TEXT,
    "setup" TEXT,
    "su128_3" TEXT,
    "su128_1" TEXT,
    "su128_1_2" TEXT,
    "su128_2" TEXT,
    "su128_1_3" TEXT,
    "su128_2_2" TEXT,
    "su128_2_3" TEXT,
    "su128_3_2" TEXT,
    "su128_2_4" TEXT,
    "su128_3_3" TEXT,
    "su128_2_5" TEXT,
    "su128_1_4" INTEGER,
    "su128_3_4" INTEGER,
    "su128_1_5" INTEGER,
    "su128_2_6" INTEGER,
    "su128_1_6" INTEGER
);
COMMENT ON TABLE "fab10"."setup_matrix_implant_gas" IS 'SMT2020 General Data sheet: Setup_Matrix_Implant_Gas; header row: 7';

CREATE TABLE IF NOT EXISTS "fab10"."transport" (
    source_row_id BIGSERIAL PRIMARY KEY,
    "from_location" TEXT,
    "to_location" TEXT,
    "transporttime_distribution" TEXT,
    "mean" NUMERIC,
    "offset" NUMERIC,
    "tt_units" TEXT
);
COMMENT ON TABLE "fab10"."transport" IS 'SMT2020 General Data sheet: Transport; header row: 1';

-- dataset 2 -> fab11: SMT_2020 - Final/General Data/dataset 2/SMT_2020_Model_Data_-_LVHM.xlsx
CREATE SCHEMA IF NOT EXISTS "fab11";

CREATE TABLE IF NOT EXISTS "fab11"."toolgroups" (
    source_row_id BIGSERIAL PRIMARY KEY,
    "area" TEXT,
    "toolgroup" TEXT,
    "number_of_tools" INTEGER,
    "cascadingtool" TEXT,
    "bacthingtool" TEXT,
    "batchcriterion" TEXT,
    "batching_unit" TEXT,
    "loadingtime" INTEGER,
    "lt_units" TEXT,
    "unloadingtime" INTEGER,
    "ult_units" TEXT,
    "toolgrouplocation" TEXT,
    "dispatching" TEXT,
    "ranking_1" TEXT,
    "ranking_2" TEXT,
    "ranking_3" TEXT,
    "tool_wake_up_ranking" TEXT
);
COMMENT ON TABLE "fab11"."toolgroups" IS 'SMT2020 General Data sheet: Toolgroups; header row: 1';

CREATE TABLE IF NOT EXISTS "fab11"."pm" (
    source_row_id BIGSERIAL PRIMARY KEY,
    "pm_event_name" TEXT,
    "pm_event_valid_for_type" TEXT,
    "type_name" TEXT,
    "pm_type" TEXT,
    "mtbeforepm" INTEGER,
    "mtbpm_units" TEXT,
    "ttr_distribution" TEXT,
    "mean" NUMERIC,
    "offset" NUMERIC,
    "ttr_units" TEXT,
    "first_one_at_distribution" TEXT,
    "foa" NUMERIC,
    "foa_units" TEXT
);
COMMENT ON TABLE "fab11"."pm" IS 'SMT2020 General Data sheet: PM; header row: 1';

CREATE TABLE IF NOT EXISTS "fab11"."breakdown" (
    source_row_id BIGSERIAL PRIMARY KEY,
    "down_event_name" TEXT,
    "down_event_valid_for_type" TEXT,
    "type_name" TEXT,
    "down_type" TEXT,
    "ttf_distribution" TEXT,
    "mttf" INTEGER,
    "mttf_units" TEXT,
    "ttr_distribution" TEXT,
    "mttr" NUMERIC,
    "mttr_units" TEXT,
    "first_one_at_distribution" TEXT,
    "foa" INTEGER,
    "foa_units" TEXT
);
COMMENT ON TABLE "fab11"."breakdown" IS 'SMT2020 General Data sheet: Breakdown; header row: 1';

CREATE TABLE IF NOT EXISTS "fab11"."setups" (
    source_row_id BIGSERIAL PRIMARY KEY,
    "setup_group_name" TEXT,
    "current_setup" TEXT,
    "new_setup" TEXT,
    "setup_time" INTEGER,
    "st_units" TEXT,
    "minmal_number_of_runs" INTEGER
);
COMMENT ON TABLE "fab11"."setups" IS 'SMT2020 General Data sheet: Setups; header row: 1';

CREATE TABLE IF NOT EXISTS "fab11"."transport" (
    source_row_id BIGSERIAL PRIMARY KEY,
    "from_location" TEXT,
    "to_location" TEXT,
    "transporttime_distribution" TEXT,
    "mean" NUMERIC,
    "offset" NUMERIC,
    "tt_units" TEXT
);
COMMENT ON TABLE "fab11"."transport" IS 'SMT2020 General Data sheet: Transport; header row: 1';

CREATE TABLE IF NOT EXISTS "fab11"."lotrelease" (
    source_row_id BIGSERIAL PRIMARY KEY,
    "product_name" TEXT,
    "route_name" TEXT,
    "lot_name_type" TEXT,
    "priority" INTEGER,
    "superhotlot" TEXT,
    "wafers_per_lot" INTEGER,
    "start_date" TIMESTAMP,
    "release_distribution" TEXT,
    "release_interval" NUMERIC,
    "r_units" TEXT,
    "lots_per_release" INTEGER,
    "due_date" TIMESTAMP,
    "release_scenario" TEXT
);
COMMENT ON TABLE "fab11"."lotrelease" IS 'SMT2020 General Data sheet: Lotrelease; header row: 1';

CREATE TABLE IF NOT EXISTS "fab11"."lotrelease_variable_due_dates" (
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
COMMENT ON TABLE "fab11"."lotrelease_variable_due_dates" IS 'SMT2020 General Data sheet: Lotrelease - variable due dates; header row: 1';

CREATE TABLE IF NOT EXISTS "fab11"."route_product_1" (
    source_row_id BIGSERIAL PRIMARY KEY,
    "route" TEXT,
    "step" TEXT,
    "step_description" TEXT,
    "area" TEXT,
    "toolgroup" TEXT,
    "processing_unit" TEXT,
    "processingtime_distribution" TEXT,
    "mean" NUMERIC,
    "offset" NUMERIC,
    "pt_units" TEXT,
    "cascading_interval" NUMERIC,
    "c_units" TEXT,
    "batch_minimum" INTEGER,
    "batch_maximum" INTEGER,
    "setup" TEXT,
    "when" TEXT,
    "setup_distribution" TEXT,
    "setup_time" INTEGER,
    "offset_2" TEXT,
    "st_units" TEXT,
    "step_for_ltl_dedication" TEXT,
    "rework_probability_in_percent" NUMERIC,
    "r_unit" TEXT,
    "step_for_rework" TEXT,
    "processing_probability_in_percent_sampling" INTEGER,
    "step_for_critical_queue_time" TEXT,
    "cqt" INTEGER,
    "cqtunits" TEXT
);
COMMENT ON TABLE "fab11"."route_product_1" IS 'SMT2020 General Data sheet: Route_Product_1; header row: 1';

CREATE TABLE IF NOT EXISTS "fab11"."route_product_2" (
    source_row_id BIGSERIAL PRIMARY KEY,
    "route" TEXT,
    "step" TEXT,
    "step_description" TEXT,
    "area" TEXT,
    "toolgroup" TEXT,
    "processing_unit" TEXT,
    "processingtime_distribution" TEXT,
    "mean" NUMERIC,
    "offset" NUMERIC,
    "pt_units" TEXT,
    "cascading_interval" NUMERIC,
    "c_units" TEXT,
    "batch_minimum" INTEGER,
    "batch_maximum" INTEGER,
    "setup" TEXT,
    "when" TEXT,
    "setup_distribution" TEXT,
    "setup_time" INTEGER,
    "offset_2" TEXT,
    "st_units" TEXT,
    "step_for_ltl_dedication" TEXT,
    "rework_probability_in_percent" NUMERIC,
    "r_unit" TEXT,
    "step_for_rework" TEXT,
    "processing_probability_in_percent_sampling" INTEGER,
    "step_for_critical_queue_time" TEXT,
    "cqt" INTEGER,
    "cqtunits" TEXT
);
COMMENT ON TABLE "fab11"."route_product_2" IS 'SMT2020 General Data sheet: Route_Product_2; header row: 1';

CREATE TABLE IF NOT EXISTS "fab11"."route_product_3" (
    source_row_id BIGSERIAL PRIMARY KEY,
    "route" TEXT,
    "step" TEXT,
    "step_description" TEXT,
    "area" TEXT,
    "toolgroup" TEXT,
    "processing_unit" TEXT,
    "processingtime_distribution" TEXT,
    "mean" NUMERIC,
    "offset" NUMERIC,
    "pt_units" TEXT,
    "cascading_interval" NUMERIC,
    "c_units" TEXT,
    "batch_minimum" INTEGER,
    "batch_maximum" INTEGER,
    "setup" TEXT,
    "when" TEXT,
    "setup_distribution" TEXT,
    "setup_time" INTEGER,
    "offset_2" TEXT,
    "st_units" TEXT,
    "step_for_ltl_dedication" TEXT,
    "rework_probability_in_percent" NUMERIC,
    "r_unit" TEXT,
    "step_for_rework" TEXT,
    "processing_probability_in_percent_sampling" INTEGER,
    "step_for_critical_queue_time" TEXT,
    "cqt" INTEGER,
    "cqtunits" TEXT
);
COMMENT ON TABLE "fab11"."route_product_3" IS 'SMT2020 General Data sheet: Route_Product_3; header row: 1';

CREATE TABLE IF NOT EXISTS "fab11"."route_product_4" (
    source_row_id BIGSERIAL PRIMARY KEY,
    "route" TEXT,
    "step" TEXT,
    "step_description" TEXT,
    "area" TEXT,
    "toolgroup" TEXT,
    "processing_unit" TEXT,
    "processingtime_distribution" TEXT,
    "mean" NUMERIC,
    "offset" NUMERIC,
    "pt_units" TEXT,
    "cascading_interval" NUMERIC,
    "c_units" TEXT,
    "batch_minimum" INTEGER,
    "batch_maximum" INTEGER,
    "setup" TEXT,
    "when" TEXT,
    "setup_distribution" TEXT,
    "setup_time" INTEGER,
    "offset_2" TEXT,
    "st_units" TEXT,
    "step_for_ltl_dedication" TEXT,
    "rework_probability_in_percent" NUMERIC,
    "r_unit" TEXT,
    "step_for_rework" TEXT,
    "processing_probability_in_percent_sampling" INTEGER,
    "step_for_critical_queue_time" TEXT,
    "cqt" INTEGER,
    "cqtunits" TEXT
);
COMMENT ON TABLE "fab11"."route_product_4" IS 'SMT2020 General Data sheet: Route_Product_4; header row: 1';

CREATE TABLE IF NOT EXISTS "fab11"."route_product_5" (
    source_row_id BIGSERIAL PRIMARY KEY,
    "route" TEXT,
    "step" TEXT,
    "step_description" TEXT,
    "area" TEXT,
    "toolgroup" TEXT,
    "processing_unit" TEXT,
    "processingtime_distribution" TEXT,
    "mean" NUMERIC,
    "offset" NUMERIC,
    "pt_units" TEXT,
    "cascading_interval" NUMERIC,
    "c_units" TEXT,
    "batch_minimum" INTEGER,
    "batch_maximum" INTEGER,
    "setup" TEXT,
    "when" TEXT,
    "setup_distribution" TEXT,
    "setup_time" INTEGER,
    "offset_2" TEXT,
    "st_units" TEXT,
    "step_for_ltl_dedication" TEXT,
    "rework_probability_in_percent" NUMERIC,
    "r_unit" TEXT,
    "step_for_rework" TEXT,
    "processing_probability_in_percent_sampling" INTEGER,
    "step_for_critical_queue_time" TEXT,
    "cqt" INTEGER,
    "cqtunits" TEXT
);
COMMENT ON TABLE "fab11"."route_product_5" IS 'SMT2020 General Data sheet: Route_Product_5; header row: 1';

CREATE TABLE IF NOT EXISTS "fab11"."route_product_6" (
    source_row_id BIGSERIAL PRIMARY KEY,
    "route" TEXT,
    "step" TEXT,
    "step_description" TEXT,
    "area" TEXT,
    "toolgroup" TEXT,
    "processing_unit" TEXT,
    "processingtime_distribution" TEXT,
    "mean" NUMERIC,
    "offset" NUMERIC,
    "pt_units" TEXT,
    "cascading_interval" NUMERIC,
    "c_units" TEXT,
    "batch_minimum" INTEGER,
    "batch_maximum" INTEGER,
    "setup" TEXT,
    "when" TEXT,
    "setup_distribution" TEXT,
    "setup_time" INTEGER,
    "offset_2" TEXT,
    "st_units" TEXT,
    "step_for_ltl_dedication" TEXT,
    "rework_probability_in_percent" NUMERIC,
    "r_unit" TEXT,
    "step_for_rework" TEXT,
    "processing_probability_in_percent_sampling" INTEGER,
    "step_for_critical_queue_time" TEXT,
    "cqt" INTEGER,
    "cqtunits" TEXT
);
COMMENT ON TABLE "fab11"."route_product_6" IS 'SMT2020 General Data sheet: Route_Product_6; header row: 1';

CREATE TABLE IF NOT EXISTS "fab11"."route_product_7" (
    source_row_id BIGSERIAL PRIMARY KEY,
    "route" TEXT,
    "step" TEXT,
    "step_description" TEXT,
    "area" TEXT,
    "toolgroup" TEXT,
    "processing_unit" TEXT,
    "processingtime_distribution" TEXT,
    "mean" NUMERIC,
    "offset" NUMERIC,
    "pt_units" TEXT,
    "cascading_interval" NUMERIC,
    "c_units" TEXT,
    "batch_minimum" INTEGER,
    "batch_maximum" INTEGER,
    "setup" TEXT,
    "when" TEXT,
    "setup_distribution" TEXT,
    "setup_time" INTEGER,
    "offset_2" TEXT,
    "st_units" TEXT,
    "step_for_ltl_dedication" TEXT,
    "rework_probability_in_percent" NUMERIC,
    "r_unit" TEXT,
    "step_for_rework" TEXT,
    "processing_probability_in_percent_sampling" INTEGER,
    "step_for_critical_queue_time" TEXT,
    "cqt" INTEGER,
    "cqtunits" TEXT
);
COMMENT ON TABLE "fab11"."route_product_7" IS 'SMT2020 General Data sheet: Route_Product_7; header row: 1';

CREATE TABLE IF NOT EXISTS "fab11"."route_product_8" (
    source_row_id BIGSERIAL PRIMARY KEY,
    "route" TEXT,
    "step" TEXT,
    "step_description" TEXT,
    "area" TEXT,
    "toolgroup" TEXT,
    "processing_unit" TEXT,
    "processingtime_distribution" TEXT,
    "mean" NUMERIC,
    "offset" NUMERIC,
    "pt_units" TEXT,
    "cascading_interval" NUMERIC,
    "c_units" TEXT,
    "batch_minimum" INTEGER,
    "batch_maximum" INTEGER,
    "setup" TEXT,
    "when" TEXT,
    "setup_distribution" TEXT,
    "setup_time" INTEGER,
    "offset_2" TEXT,
    "st_units" TEXT,
    "step_for_ltl_dedication" TEXT,
    "rework_probability_in_percent" NUMERIC,
    "r_unit" TEXT,
    "step_for_rework" TEXT,
    "processing_probability_in_percent_sampling" INTEGER,
    "step_for_critical_queue_time" TEXT,
    "cqt" INTEGER,
    "cqtunits" TEXT
);
COMMENT ON TABLE "fab11"."route_product_8" IS 'SMT2020 General Data sheet: Route_Product_8; header row: 1';

CREATE TABLE IF NOT EXISTS "fab11"."route_product_9" (
    source_row_id BIGSERIAL PRIMARY KEY,
    "route" TEXT,
    "step" TEXT,
    "step_description" TEXT,
    "area" TEXT,
    "toolgroup" TEXT,
    "processing_unit" TEXT,
    "processingtime_distribution" TEXT,
    "mean" NUMERIC,
    "offset" NUMERIC,
    "pt_units" TEXT,
    "cascading_interval" NUMERIC,
    "c_units" TEXT,
    "batch_minimum" INTEGER,
    "batch_maximum" INTEGER,
    "setup" TEXT,
    "when" TEXT,
    "setup_distribution" TEXT,
    "setup_time" INTEGER,
    "offset_2" TEXT,
    "st_units" TEXT,
    "step_for_ltl_dedication" TEXT,
    "rework_probability_in_percent" INTEGER,
    "r_unit" TEXT,
    "step_for_rework" TEXT,
    "processing_probability_in_percent_sampling" INTEGER,
    "step_for_critical_queue_time" TEXT,
    "cqt" INTEGER,
    "cqtunits" TEXT
);
COMMENT ON TABLE "fab11"."route_product_9" IS 'SMT2020 General Data sheet: Route_Product_9; header row: 1';

CREATE TABLE IF NOT EXISTS "fab11"."route_product_10" (
    source_row_id BIGSERIAL PRIMARY KEY,
    "route" TEXT,
    "step" TEXT,
    "step_description" TEXT,
    "area" TEXT,
    "toolgroup" TEXT,
    "processing_unit" TEXT,
    "processingtime_distribution" TEXT,
    "mean" NUMERIC,
    "offset" NUMERIC,
    "pt_units" TEXT,
    "cascading_interval" NUMERIC,
    "c_units" TEXT,
    "batch_minimum" INTEGER,
    "batch_maximum" INTEGER,
    "setup" TEXT,
    "when" TEXT,
    "setup_distribution" TEXT,
    "setup_time" INTEGER,
    "offset_2" TEXT,
    "st_units" TEXT,
    "step_for_ltl_dedication" TEXT,
    "rework_probability_in_percent" NUMERIC,
    "r_unit" TEXT,
    "step_for_rework" TEXT,
    "processing_probability_in_percent_sampling" INTEGER,
    "step_for_critical_queue_time" TEXT,
    "cqt" INTEGER,
    "cqtunits" TEXT
);
COMMENT ON TABLE "fab11"."route_product_10" IS 'SMT2020 General Data sheet: Route_Product_10; header row: 1';

-- dataset 3 -> fab12: SMT_2020 - Final/General Data/dataset 3/SMT_2020_Model_Data_-_HVLM_E.xlsx
CREATE SCHEMA IF NOT EXISTS "fab12";

CREATE TABLE IF NOT EXISTS "fab12"."toolgroups" (
    source_row_id BIGSERIAL PRIMARY KEY,
    "area" TEXT,
    "toolgroup" TEXT,
    "number_of_tools" INTEGER,
    "cascadingtool" TEXT,
    "bacthingtool" TEXT,
    "batchcriterion" TEXT,
    "batching_unit" TEXT,
    "loadingtime" INTEGER,
    "lt_units" TEXT,
    "unloadingtime" INTEGER,
    "ult_units" TEXT,
    "toolgrouplocation" TEXT,
    "dispatching" TEXT,
    "ranking_1" TEXT,
    "ranking_2" TEXT,
    "ranking_3" TEXT,
    "tool_wake_up_ranking" TEXT
);
COMMENT ON TABLE "fab12"."toolgroups" IS 'SMT2020 General Data sheet: Toolgroups; header row: 1';

CREATE TABLE IF NOT EXISTS "fab12"."pm" (
    source_row_id BIGSERIAL PRIMARY KEY,
    "pm_event_name" TEXT,
    "pm_event_valid_for_type" TEXT,
    "type_name" TEXT,
    "pm_type" TEXT,
    "mtbeforepm" INTEGER,
    "mtbpm_units" TEXT,
    "ttr_distribution" TEXT,
    "mean" NUMERIC,
    "offset" NUMERIC,
    "ttr_units" TEXT,
    "first_one_at_distribution" TEXT,
    "foa" NUMERIC,
    "foa_units" TEXT
);
COMMENT ON TABLE "fab12"."pm" IS 'SMT2020 General Data sheet: PM; header row: 1';

CREATE TABLE IF NOT EXISTS "fab12"."breakdown" (
    source_row_id BIGSERIAL PRIMARY KEY,
    "down_event_name" TEXT,
    "down_event_valid_for_type" TEXT,
    "type_name" TEXT,
    "down_type" TEXT,
    "ttf_distribution" TEXT,
    "mttf" INTEGER,
    "mttf_units" TEXT,
    "ttr_distribution" TEXT,
    "mttr" NUMERIC,
    "mttr_units" TEXT,
    "first_one_at_distribution" TEXT,
    "foa" INTEGER,
    "foa_units" TEXT
);
COMMENT ON TABLE "fab12"."breakdown" IS 'SMT2020 General Data sheet: Breakdown; header row: 1';

CREATE TABLE IF NOT EXISTS "fab12"."route_product_e3" (
    source_row_id BIGSERIAL PRIMARY KEY,
    "route" TEXT,
    "step" TEXT,
    "step_description" TEXT,
    "area" TEXT,
    "toolgroup" TEXT,
    "processing_unit" TEXT,
    "processingtime_distribution" TEXT,
    "mean" NUMERIC,
    "offset" NUMERIC,
    "pt_units" TEXT,
    "cascading_interval" NUMERIC,
    "c_units" TEXT,
    "batch_minimum" INTEGER,
    "batch_maximum" INTEGER,
    "setup" TEXT,
    "when" TEXT,
    "setup_distribution" TEXT,
    "setup_time" NUMERIC,
    "offset_2" NUMERIC,
    "st_units" TEXT,
    "step_for_ltl_dedication" TEXT,
    "rework_probability_in_percent" NUMERIC,
    "r_unit" TEXT,
    "step_for_rework" TEXT,
    "processing_probability_in_percent_sampling" INTEGER,
    "step_for_critical_queue_time" TEXT,
    "cqt" INTEGER,
    "cqtunits" TEXT
);
COMMENT ON TABLE "fab12"."route_product_e3" IS 'SMT2020 General Data sheet: Route_Product_E3; header row: 1';

CREATE TABLE IF NOT EXISTS "fab12"."route_product_3" (
    source_row_id BIGSERIAL PRIMARY KEY,
    "route" TEXT,
    "step" TEXT,
    "step_description" TEXT,
    "area" TEXT,
    "toolgroup" TEXT,
    "processing_unit" TEXT,
    "processingtime_distribution" TEXT,
    "mean" NUMERIC,
    "offset" NUMERIC,
    "pt_units" TEXT,
    "cascading_interval" NUMERIC,
    "c_units" TEXT,
    "batch_minimum" INTEGER,
    "batch_maximum" INTEGER,
    "setup" TEXT,
    "when" TEXT,
    "setup_distribution" TEXT,
    "setup_time" INTEGER,
    "offset_2" TEXT,
    "st_units" TEXT,
    "step_for_ltl_dedication" TEXT,
    "rework_probability_in_percent" NUMERIC,
    "r_unit" TEXT,
    "step_for_rework" TEXT,
    "processing_probability_in_percent_sampling" INTEGER,
    "step_for_critical_queue_time" TEXT,
    "cqt" INTEGER,
    "cqtunits" TEXT
);
COMMENT ON TABLE "fab12"."route_product_3" IS 'SMT2020 General Data sheet: Route_Product_3; header row: 1';

CREATE TABLE IF NOT EXISTS "fab12"."route_product_4" (
    source_row_id BIGSERIAL PRIMARY KEY,
    "route" TEXT,
    "step" TEXT,
    "step_description" TEXT,
    "area" TEXT,
    "toolgroup" TEXT,
    "processing_unit" TEXT,
    "processingtime_distribution" TEXT,
    "mean" NUMERIC,
    "offset" NUMERIC,
    "pt_units" TEXT,
    "cascading_interval" NUMERIC,
    "c_units" TEXT,
    "batch_minimum" INTEGER,
    "batch_maximum" INTEGER,
    "setup" TEXT,
    "when" TEXT,
    "setup_distribution" TEXT,
    "setup_time" INTEGER,
    "offset_2" TEXT,
    "st_units" TEXT,
    "step_for_ltl_dedication" TEXT,
    "rework_probability_in_percent" NUMERIC,
    "r_unit" TEXT,
    "step_for_rework" TEXT,
    "processing_probability_in_percent_sampling" INTEGER,
    "step_for_critical_queue_time" TEXT,
    "cqt" INTEGER,
    "cqtunits" TEXT
);
COMMENT ON TABLE "fab12"."route_product_4" IS 'SMT2020 General Data sheet: Route_Product_4; header row: 1';

CREATE TABLE IF NOT EXISTS "fab12"."lotrelease" (
    source_row_id BIGSERIAL PRIMARY KEY,
    "product_name" TEXT,
    "route_name" TEXT,
    "lot_name_type" TEXT,
    "priority" INTEGER,
    "superhotlot" TEXT,
    "wafers_per_lot" INTEGER,
    "start_date" TIMESTAMP,
    "release_distribution" TEXT,
    "release_interval" NUMERIC,
    "r_units" TEXT,
    "lots_per_release" INTEGER,
    "due_date" TIMESTAMP,
    "release_scenario" TEXT
);
COMMENT ON TABLE "fab12"."lotrelease" IS 'SMT2020 General Data sheet: Lotrelease; header row: 1';

CREATE TABLE IF NOT EXISTS "fab12"."lotrelease_engineering" (
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
COMMENT ON TABLE "fab12"."lotrelease_engineering" IS 'SMT2020 General Data sheet: Lotrelease Engineering; header row: 1';

CREATE TABLE IF NOT EXISTS "fab12"."setups" (
    source_row_id BIGSERIAL PRIMARY KEY,
    "setup_group_name" TEXT,
    "current_setup" TEXT,
    "new_setup" TEXT,
    "setup_time" INTEGER,
    "st_units" TEXT,
    "minmal_number_of_runs" INTEGER
);
COMMENT ON TABLE "fab12"."setups" IS 'SMT2020 General Data sheet: Setups; header row: 1';

CREATE TABLE IF NOT EXISTS "fab12"."setup_matrix_implant_gas" (
    source_row_id BIGSERIAL PRIMARY KEY,
    "col_001" TEXT,
    "product_route" TEXT,
    "step" TEXT,
    "setup" TEXT,
    "su128_3" TEXT,
    "su128_1" TEXT,
    "su128_1_2" TEXT,
    "su128_2" TEXT,
    "su128_1_3" TEXT,
    "su128_2_2" TEXT,
    "su128_2_3" TEXT,
    "su128_3_2" TEXT,
    "su128_2_4" TEXT,
    "su128_3_3" TEXT,
    "su128_2_5" TEXT,
    "su128_1_4" INTEGER,
    "su128_3_4" INTEGER,
    "su128_1_5" INTEGER,
    "su128_2_6" INTEGER,
    "su128_1_6" INTEGER
);
COMMENT ON TABLE "fab12"."setup_matrix_implant_gas" IS 'SMT2020 General Data sheet: Setup_Matrix_Implant_Gas; header row: 7';

CREATE TABLE IF NOT EXISTS "fab12"."transport" (
    source_row_id BIGSERIAL PRIMARY KEY,
    "from_location" TEXT,
    "to_location" TEXT,
    "transporttime_distribution" TEXT,
    "mean" NUMERIC,
    "offset" NUMERIC,
    "tt_units" TEXT
);
COMMENT ON TABLE "fab12"."transport" IS 'SMT2020 General Data sheet: Transport; header row: 1';

-- dataset 4 -> fab13: SMT_2020 - Final/General Data/dataset 4/SMT_2020_Model_Data_-_LVHM_E.xlsx
CREATE SCHEMA IF NOT EXISTS "fab13";

CREATE TABLE IF NOT EXISTS "fab13"."toolgroups" (
    source_row_id BIGSERIAL PRIMARY KEY,
    "area" TEXT,
    "toolgroup" TEXT,
    "number_of_tools" INTEGER,
    "cascadingtool" TEXT,
    "bacthingtool" TEXT,
    "batchcriterion" TEXT,
    "batching_unit" TEXT,
    "loadingtime" INTEGER,
    "lt_units" TEXT,
    "unloadingtime" INTEGER,
    "ult_units" TEXT,
    "toolgrouplocation" TEXT,
    "dispatching" TEXT,
    "ranking_1" TEXT,
    "ranking_2" TEXT,
    "ranking_3" TEXT,
    "tool_wake_up_ranking" TEXT
);
COMMENT ON TABLE "fab13"."toolgroups" IS 'SMT2020 General Data sheet: Toolgroups; header row: 1';

CREATE TABLE IF NOT EXISTS "fab13"."pm" (
    source_row_id BIGSERIAL PRIMARY KEY,
    "pm_event_name" TEXT,
    "pm_event_valid_for_type" TEXT,
    "type_name" TEXT,
    "pm_type" TEXT,
    "mtbeforepm" INTEGER,
    "mtbpm_units" TEXT,
    "ttr_distribution" TEXT,
    "mean" NUMERIC,
    "offset" NUMERIC,
    "ttr_units" TEXT,
    "first_one_at_distribution" TEXT,
    "foa" NUMERIC,
    "foa_units" TEXT
);
COMMENT ON TABLE "fab13"."pm" IS 'SMT2020 General Data sheet: PM; header row: 1';

CREATE TABLE IF NOT EXISTS "fab13"."breakdown" (
    source_row_id BIGSERIAL PRIMARY KEY,
    "down_event_name" TEXT,
    "down_event_valid_for_type" TEXT,
    "type_name" TEXT,
    "down_type" TEXT,
    "ttf_distribution" TEXT,
    "mttf" INTEGER,
    "mttf_units" TEXT,
    "ttr_distribution" TEXT,
    "mttr" NUMERIC,
    "mttr_units" TEXT,
    "first_one_at_distribution" TEXT,
    "foa" INTEGER,
    "foa_units" TEXT
);
COMMENT ON TABLE "fab13"."breakdown" IS 'SMT2020 General Data sheet: Breakdown; header row: 1';

CREATE TABLE IF NOT EXISTS "fab13"."setups" (
    source_row_id BIGSERIAL PRIMARY KEY,
    "setup_group_name" TEXT,
    "current_setup" TEXT,
    "new_setup" TEXT,
    "setup_time" INTEGER,
    "st_units" TEXT,
    "minmal_number_of_runs" INTEGER
);
COMMENT ON TABLE "fab13"."setups" IS 'SMT2020 General Data sheet: Setups; header row: 1';

CREATE TABLE IF NOT EXISTS "fab13"."transport" (
    source_row_id BIGSERIAL PRIMARY KEY,
    "from_location" TEXT,
    "to_location" TEXT,
    "transporttime_distribution" TEXT,
    "mean" NUMERIC,
    "offset" NUMERIC,
    "tt_units" TEXT
);
COMMENT ON TABLE "fab13"."transport" IS 'SMT2020 General Data sheet: Transport; header row: 1';

CREATE TABLE IF NOT EXISTS "fab13"."lotrelease" (
    source_row_id BIGSERIAL PRIMARY KEY,
    "product_name" TEXT,
    "route_name" TEXT,
    "lot_name_type" TEXT,
    "priority" INTEGER,
    "superhotlot" TEXT,
    "wafers_per_lot" INTEGER,
    "start_date" TIMESTAMP,
    "release_distribution" TEXT,
    "release_interval" NUMERIC,
    "r_units" TEXT,
    "lots_per_release" INTEGER,
    "due_date" TIMESTAMP,
    "release_scenario" TEXT
);
COMMENT ON TABLE "fab13"."lotrelease" IS 'SMT2020 General Data sheet: Lotrelease; header row: 1';

CREATE TABLE IF NOT EXISTS "fab13"."lotrelease_variable_due_dates" (
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
COMMENT ON TABLE "fab13"."lotrelease_variable_due_dates" IS 'SMT2020 General Data sheet: Lotrelease - variable due dates; header row: 1';

CREATE TABLE IF NOT EXISTS "fab13"."lotrelease_engineering" (
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
COMMENT ON TABLE "fab13"."lotrelease_engineering" IS 'SMT2020 General Data sheet: Lotrelease - Engineering; header row: 1';

CREATE TABLE IF NOT EXISTS "fab13"."route_product_e1" (
    source_row_id BIGSERIAL PRIMARY KEY,
    "route" TEXT,
    "step" TEXT,
    "step_description" TEXT,
    "area" TEXT,
    "toolgroup" TEXT,
    "processing_unit" TEXT,
    "processingtime_distribution" TEXT,
    "mean" NUMERIC,
    "offset" NUMERIC,
    "pt_units" TEXT,
    "cascading_interval" NUMERIC,
    "c_units" TEXT,
    "batch_minimum" INTEGER,
    "batch_maximum" INTEGER,
    "setup" TEXT,
    "when" TEXT,
    "setup_distribution" TEXT,
    "setup_time" NUMERIC,
    "offset_2" NUMERIC,
    "st_units" TEXT,
    "step_for_ltl_dedication" TEXT,
    "rework_probability_in_percent" NUMERIC,
    "r_unit" TEXT,
    "step_for_rework" TEXT,
    "processing_probability_in_percent_sampling" INTEGER,
    "step_for_critical_queue_time" TEXT,
    "cqt" INTEGER,
    "cqtunits" TEXT
);
COMMENT ON TABLE "fab13"."route_product_e1" IS 'SMT2020 General Data sheet: Route_Product_E1; header row: 1';

CREATE TABLE IF NOT EXISTS "fab13"."route_product_e2" (
    source_row_id BIGSERIAL PRIMARY KEY,
    "route" TEXT,
    "step" TEXT,
    "step_description" TEXT,
    "area" TEXT,
    "toolgroup" TEXT,
    "processing_unit" TEXT,
    "processingtime_distribution" TEXT,
    "mean" NUMERIC,
    "offset" NUMERIC,
    "pt_units" TEXT,
    "cascading_interval" NUMERIC,
    "c_units" TEXT,
    "batch_minimum" INTEGER,
    "batch_maximum" INTEGER,
    "setup" TEXT,
    "when" TEXT,
    "setup_distribution" TEXT,
    "setup_time" NUMERIC,
    "offset_2" NUMERIC,
    "st_units" TEXT,
    "step_for_ltl_dedication" TEXT,
    "rework_probability_in_percent" NUMERIC,
    "r_unit" TEXT,
    "step_for_rework" TEXT,
    "processing_probability_in_percent_sampling" INTEGER,
    "step_for_critical_queue_time" TEXT,
    "cqt" INTEGER,
    "cqtunits" TEXT
);
COMMENT ON TABLE "fab13"."route_product_e2" IS 'SMT2020 General Data sheet: Route_Product_E2; header row: 1';

CREATE TABLE IF NOT EXISTS "fab13"."route_product_e3" (
    source_row_id BIGSERIAL PRIMARY KEY,
    "route" TEXT,
    "step" TEXT,
    "step_description" TEXT,
    "area" TEXT,
    "toolgroup" TEXT,
    "processing_unit" TEXT,
    "processingtime_distribution" TEXT,
    "mean" NUMERIC,
    "offset" NUMERIC,
    "pt_units" TEXT,
    "cascading_interval" NUMERIC,
    "c_units" TEXT,
    "batch_minimum" INTEGER,
    "batch_maximum" INTEGER,
    "setup" TEXT,
    "when" TEXT,
    "setup_distribution" TEXT,
    "setup_time" NUMERIC,
    "offset_2" NUMERIC,
    "st_units" TEXT,
    "step_for_ltl_dedication" TEXT,
    "rework_probability_in_percent" NUMERIC,
    "r_unit" TEXT,
    "step_for_rework" TEXT,
    "processing_probability_in_percent_sampling" INTEGER,
    "step_for_critical_queue_time" TEXT,
    "cqt" INTEGER,
    "cqtunits" TEXT
);
COMMENT ON TABLE "fab13"."route_product_e3" IS 'SMT2020 General Data sheet: Route_Product_E3; header row: 1';

CREATE TABLE IF NOT EXISTS "fab13"."route_product_1" (
    source_row_id BIGSERIAL PRIMARY KEY,
    "route" TEXT,
    "step" TEXT,
    "step_description" TEXT,
    "area" TEXT,
    "toolgroup" TEXT,
    "processing_unit" TEXT,
    "processingtime_distribution" TEXT,
    "mean" NUMERIC,
    "offset" NUMERIC,
    "pt_units" TEXT,
    "cascading_interval" NUMERIC,
    "c_units" TEXT,
    "batch_minimum" INTEGER,
    "batch_maximum" INTEGER,
    "setup" TEXT,
    "when" TEXT,
    "setup_distribution" TEXT,
    "setup_time" INTEGER,
    "offset_2" TEXT,
    "st_units" TEXT,
    "step_for_ltl_dedication" TEXT,
    "rework_probability_in_percent" NUMERIC,
    "r_unit" TEXT,
    "step_for_rework" TEXT,
    "processing_probability_in_percent_sampling" INTEGER,
    "step_for_critical_queue_time" TEXT,
    "cqt" INTEGER,
    "cqtunits" TEXT
);
COMMENT ON TABLE "fab13"."route_product_1" IS 'SMT2020 General Data sheet: Route_Product_1; header row: 1';

CREATE TABLE IF NOT EXISTS "fab13"."route_product_2" (
    source_row_id BIGSERIAL PRIMARY KEY,
    "route" TEXT,
    "step" TEXT,
    "step_description" TEXT,
    "area" TEXT,
    "toolgroup" TEXT,
    "processing_unit" TEXT,
    "processingtime_distribution" TEXT,
    "mean" NUMERIC,
    "offset" NUMERIC,
    "pt_units" TEXT,
    "cascading_interval" NUMERIC,
    "c_units" TEXT,
    "batch_minimum" INTEGER,
    "batch_maximum" INTEGER,
    "setup" TEXT,
    "when" TEXT,
    "setup_distribution" TEXT,
    "setup_time" INTEGER,
    "offset_2" TEXT,
    "st_units" TEXT,
    "step_for_ltl_dedication" TEXT,
    "rework_probability_in_percent" NUMERIC,
    "r_unit" TEXT,
    "step_for_rework" TEXT,
    "processing_probability_in_percent_sampling" INTEGER,
    "step_for_critical_queue_time" TEXT,
    "cqt" INTEGER,
    "cqtunits" TEXT
);
COMMENT ON TABLE "fab13"."route_product_2" IS 'SMT2020 General Data sheet: Route_Product_2; header row: 1';

CREATE TABLE IF NOT EXISTS "fab13"."route_product_3" (
    source_row_id BIGSERIAL PRIMARY KEY,
    "route" TEXT,
    "step" TEXT,
    "step_description" TEXT,
    "area" TEXT,
    "toolgroup" TEXT,
    "processing_unit" TEXT,
    "processingtime_distribution" TEXT,
    "mean" NUMERIC,
    "offset" NUMERIC,
    "pt_units" TEXT,
    "cascading_interval" NUMERIC,
    "c_units" TEXT,
    "batch_minimum" INTEGER,
    "batch_maximum" INTEGER,
    "setup" TEXT,
    "when" TEXT,
    "setup_distribution" TEXT,
    "setup_time" INTEGER,
    "offset_2" TEXT,
    "st_units" TEXT,
    "step_for_ltl_dedication" TEXT,
    "rework_probability_in_percent" NUMERIC,
    "r_unit" TEXT,
    "step_for_rework" TEXT,
    "processing_probability_in_percent_sampling" INTEGER,
    "step_for_critical_queue_time" TEXT,
    "cqt" INTEGER,
    "cqtunits" TEXT
);
COMMENT ON TABLE "fab13"."route_product_3" IS 'SMT2020 General Data sheet: Route_Product_3; header row: 1';

CREATE TABLE IF NOT EXISTS "fab13"."route_product_4" (
    source_row_id BIGSERIAL PRIMARY KEY,
    "route" TEXT,
    "step" TEXT,
    "step_description" TEXT,
    "area" TEXT,
    "toolgroup" TEXT,
    "processing_unit" TEXT,
    "processingtime_distribution" TEXT,
    "mean" NUMERIC,
    "offset" NUMERIC,
    "pt_units" TEXT,
    "cascading_interval" NUMERIC,
    "c_units" TEXT,
    "batch_minimum" INTEGER,
    "batch_maximum" INTEGER,
    "setup" TEXT,
    "when" TEXT,
    "setup_distribution" TEXT,
    "setup_time" INTEGER,
    "offset_2" TEXT,
    "st_units" TEXT,
    "step_for_ltl_dedication" TEXT,
    "rework_probability_in_percent" NUMERIC,
    "r_unit" TEXT,
    "step_for_rework" TEXT,
    "processing_probability_in_percent_sampling" INTEGER,
    "step_for_critical_queue_time" TEXT,
    "cqt" INTEGER,
    "cqtunits" TEXT
);
COMMENT ON TABLE "fab13"."route_product_4" IS 'SMT2020 General Data sheet: Route_Product_4; header row: 1';

CREATE TABLE IF NOT EXISTS "fab13"."route_product_5" (
    source_row_id BIGSERIAL PRIMARY KEY,
    "route" TEXT,
    "step" TEXT,
    "step_description" TEXT,
    "area" TEXT,
    "toolgroup" TEXT,
    "processing_unit" TEXT,
    "processingtime_distribution" TEXT,
    "mean" NUMERIC,
    "offset" NUMERIC,
    "pt_units" TEXT,
    "cascading_interval" NUMERIC,
    "c_units" TEXT,
    "batch_minimum" INTEGER,
    "batch_maximum" INTEGER,
    "setup" TEXT,
    "when" TEXT,
    "setup_distribution" TEXT,
    "setup_time" INTEGER,
    "offset_2" TEXT,
    "st_units" TEXT,
    "step_for_ltl_dedication" TEXT,
    "rework_probability_in_percent" NUMERIC,
    "r_unit" TEXT,
    "step_for_rework" TEXT,
    "processing_probability_in_percent_sampling" INTEGER,
    "step_for_critical_queue_time" TEXT,
    "cqt" INTEGER,
    "cqtunits" TEXT
);
COMMENT ON TABLE "fab13"."route_product_5" IS 'SMT2020 General Data sheet: Route_Product_5; header row: 1';

CREATE TABLE IF NOT EXISTS "fab13"."route_product_6" (
    source_row_id BIGSERIAL PRIMARY KEY,
    "route" TEXT,
    "step" TEXT,
    "step_description" TEXT,
    "area" TEXT,
    "toolgroup" TEXT,
    "processing_unit" TEXT,
    "processingtime_distribution" TEXT,
    "mean" NUMERIC,
    "offset" NUMERIC,
    "pt_units" TEXT,
    "cascading_interval" NUMERIC,
    "c_units" TEXT,
    "batch_minimum" INTEGER,
    "batch_maximum" INTEGER,
    "setup" TEXT,
    "when" TEXT,
    "setup_distribution" TEXT,
    "setup_time" INTEGER,
    "offset_2" TEXT,
    "st_units" TEXT,
    "step_for_ltl_dedication" TEXT,
    "rework_probability_in_percent" NUMERIC,
    "r_unit" TEXT,
    "step_for_rework" TEXT,
    "processing_probability_in_percent_sampling" INTEGER,
    "step_for_critical_queue_time" TEXT,
    "cqt" INTEGER,
    "cqtunits" TEXT
);
COMMENT ON TABLE "fab13"."route_product_6" IS 'SMT2020 General Data sheet: Route_Product_6; header row: 1';

CREATE TABLE IF NOT EXISTS "fab13"."route_product_7" (
    source_row_id BIGSERIAL PRIMARY KEY,
    "route" TEXT,
    "step" TEXT,
    "step_description" TEXT,
    "area" TEXT,
    "toolgroup" TEXT,
    "processing_unit" TEXT,
    "processingtime_distribution" TEXT,
    "mean" NUMERIC,
    "offset" NUMERIC,
    "pt_units" TEXT,
    "cascading_interval" NUMERIC,
    "c_units" TEXT,
    "batch_minimum" INTEGER,
    "batch_maximum" INTEGER,
    "setup" TEXT,
    "when" TEXT,
    "setup_distribution" TEXT,
    "setup_time" INTEGER,
    "offset_2" TEXT,
    "st_units" TEXT,
    "step_for_ltl_dedication" TEXT,
    "rework_probability_in_percent" NUMERIC,
    "r_unit" TEXT,
    "step_for_rework" TEXT,
    "processing_probability_in_percent_sampling" INTEGER,
    "step_for_critical_queue_time" TEXT,
    "cqt" INTEGER,
    "cqtunits" TEXT
);
COMMENT ON TABLE "fab13"."route_product_7" IS 'SMT2020 General Data sheet: Route_Product_7; header row: 1';

CREATE TABLE IF NOT EXISTS "fab13"."route_product_8" (
    source_row_id BIGSERIAL PRIMARY KEY,
    "route" TEXT,
    "step" TEXT,
    "step_description" TEXT,
    "area" TEXT,
    "toolgroup" TEXT,
    "processing_unit" TEXT,
    "processingtime_distribution" TEXT,
    "mean" NUMERIC,
    "offset" NUMERIC,
    "pt_units" TEXT,
    "cascading_interval" NUMERIC,
    "c_units" TEXT,
    "batch_minimum" INTEGER,
    "batch_maximum" INTEGER,
    "setup" TEXT,
    "when" TEXT,
    "setup_distribution" TEXT,
    "setup_time" INTEGER,
    "offset_2" TEXT,
    "st_units" TEXT,
    "step_for_ltl_dedication" TEXT,
    "rework_probability_in_percent" NUMERIC,
    "r_unit" TEXT,
    "step_for_rework" TEXT,
    "processing_probability_in_percent_sampling" INTEGER,
    "step_for_critical_queue_time" TEXT,
    "cqt" INTEGER,
    "cqtunits" TEXT
);
COMMENT ON TABLE "fab13"."route_product_8" IS 'SMT2020 General Data sheet: Route_Product_8; header row: 1';

CREATE TABLE IF NOT EXISTS "fab13"."route_product_9" (
    source_row_id BIGSERIAL PRIMARY KEY,
    "route" TEXT,
    "step" TEXT,
    "step_description" TEXT,
    "area" TEXT,
    "toolgroup" TEXT,
    "processing_unit" TEXT,
    "processingtime_distribution" TEXT,
    "mean" NUMERIC,
    "offset" NUMERIC,
    "pt_units" TEXT,
    "cascading_interval" NUMERIC,
    "c_units" TEXT,
    "batch_minimum" INTEGER,
    "batch_maximum" INTEGER,
    "setup" TEXT,
    "when" TEXT,
    "setup_distribution" TEXT,
    "setup_time" INTEGER,
    "offset_2" TEXT,
    "st_units" TEXT,
    "step_for_ltl_dedication" TEXT,
    "rework_probability_in_percent" INTEGER,
    "r_unit" TEXT,
    "step_for_rework" TEXT,
    "processing_probability_in_percent_sampling" INTEGER,
    "step_for_critical_queue_time" TEXT,
    "cqt" INTEGER,
    "cqtunits" TEXT
);
COMMENT ON TABLE "fab13"."route_product_9" IS 'SMT2020 General Data sheet: Route_Product_9; header row: 1';

CREATE TABLE IF NOT EXISTS "fab13"."route_product_10" (
    source_row_id BIGSERIAL PRIMARY KEY,
    "route" TEXT,
    "step" TEXT,
    "step_description" TEXT,
    "area" TEXT,
    "toolgroup" TEXT,
    "processing_unit" TEXT,
    "processingtime_distribution" TEXT,
    "mean" NUMERIC,
    "offset" NUMERIC,
    "pt_units" TEXT,
    "cascading_interval" NUMERIC,
    "c_units" TEXT,
    "batch_minimum" INTEGER,
    "batch_maximum" INTEGER,
    "setup" TEXT,
    "when" TEXT,
    "setup_distribution" TEXT,
    "setup_time" INTEGER,
    "offset_2" TEXT,
    "st_units" TEXT,
    "step_for_ltl_dedication" TEXT,
    "rework_probability_in_percent" NUMERIC,
    "r_unit" TEXT,
    "step_for_rework" TEXT,
    "processing_probability_in_percent_sampling" INTEGER,
    "step_for_critical_queue_time" TEXT,
    "cqt" INTEGER,
    "cqtunits" TEXT
);
COMMENT ON TABLE "fab13"."route_product_10" IS 'SMT2020 General Data sheet: Route_Product_10; header row: 1';
