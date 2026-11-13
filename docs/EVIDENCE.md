# Acceptance evidence — Build My Holiday

Generated 2026-11-13 11:45 UTC by `tests/platform/smoke/evidence_report.py` from an actual run of `python -m pytest -q` (real domain on the original catalog + PostgreSQL test DB; TEST-ONLY fakes only where named `[fake]`).

**Suite:** 727 passed, 2 skipped in 101.02s (0:01:41)

**Plan IDs:** 159 — PASS: 159

IDs are mapped to every test function that references them (formal tests and earlier groundwork tests both count). Passed = passing test runs / mapped test runs (parametrized and `[fake]`/`[real]` variants each count).

| ID | Owner | Priority | Status | Passed | Tests |
|---|---|---|---|---|---|
| `BK-14` | Tanish | P0 | PASS | 1/1 | `test_phase2_packages.py::test_booked_export_uses_immutable_snapshot` |
| `CT-01` | Tanish | P0 | PASS | 2/2 | `test_phase3_planner.py::test_CT01_three_nights_only_single_city_plans`, `test_phase3_validator.py::test_CT01_CT03_city_with_too_few_nights` |
| `CT-02` | Tanish | P0 | PASS | 1/1 | `test_phase3_planner.py::test_CT02_four_nights_two_by_two_is_legal` |
| `CT-03` | Tanish | P0 | PASS | 2/2 | `test_phase3_planner.py::test_CT03_no_segment_shorter_than_min_nights`, `test_phase3_validator.py::test_CT01_CT03_city_with_too_few_nights` |
| `CT-04` | Tanish | P0 | PASS | 2/2 | `test_phase3_planner.py::test_CT04_city_never_repeated`, `test_phase3_validator.py::test_CT04_HT08_same_city_twice_rejected` |
| `CT-05` | Tanish | P0 | PASS | 4/4 | `test_catalog_loader.py::test_transfer_indexes_are_directed`, `test_phase3_planner.py::test_CT05_links_are_directed`, `test_phase3_planner.py::test_CT05_reverse_route_needs_its_own_link` (+1) |
| `CT-06` | Tanish | P0 | PASS | 1/1 | `test_phase3_validator.py::test_CT06_city_of_another_country_rejected` |
| `CT-07` | Tanish | P0 | PASS | 1/1 | `test_phase3_validator.py::test_CT07_missing_intercity_on_change_day` |
| `CT-08` | Tanish | P0 | PASS | 2/2 | `test_phase3_validator.py::test_CT08_hotel_night_gap`, `test_phase3_validator.py::test_CT08_overlap` |
| `CT-09` | Tanish | P1 | PASS | 2/2 | `test_phase3_planner.py::test_CT09_attraction_only_city_never_planned`, `test_phase3_validator.py::test_CT09_nara_attraction_rejected` |
| `CT-10` | Tanish | P1 | PASS | 1/1 | `test_phase3_planner.py::test_CT10_no_eligible_path_gives_reasoned_no_package` |
| `ED-01` | Tanish | P0 | PASS | 1/1 | `test_phase5_edits.py::test_ED01_new_hotel_area_without_airport_transfer` |
| `ED-02` | Tanish | P0 | PASS | 1/1 | `test_phase5_edits.py::test_ED02_room_capacity_or_one_night_unavailable` |
| `ED-03` | Tanish | P0 | PASS | 3/3 | `test_phase5_edits.py::test_ED03_bad_arrival_flight` |
| `ED-04` | Tanish | P0 | PASS | 1/1 | `test_phase5_edits.py::test_ED04_return_flight_too_early_for_180_minute_margin` |
| `ED-05` | Tanish | P0 | PASS | 7/7 | `test_phase5_api_edits.py::test_rejected_change_leaves_package_unchanged`, `test_phase5_edits.py::test_ED05_attraction_wrong_city_or_closed` |
| `ED-06` | Tanish | P0 | PASS | 1/1 | `test_phase5_edits.py::test_ED06_overlapping_attraction` |
| `ED-07` | Tanish | P0 | PASS | 1/1 | `test_phase5_edits.py::test_ED07_duplicate_attraction` |
| `ED-08` | Tanish | P0 | PASS | 4/4 | `test_phase5_api_edits.py::test_rejected_change_leaves_package_unchanged`, `test_phase5_edits.py::test_ED08_remove_missing_or_non_attraction_item` |
| `FL-01` | Tanish | P0 | PASS | 2/2 | `test_catalog_loader.py::test_flight_indexes_use_local_dates`, `test_phase3_planner.py::test_FL01_overnight_outbound_departing_day_before_is_valid` |
| `FL-02` | Tanish | P0 | PASS | 2/2 | `test_phase3_planner.py::test_FL02_arrival_on_wrong_local_date_not_used`, `test_phase3_validator.py::test_FL02_arrival_local_date_after_start` |
| `FL-03` | Tanish | P0 | PASS | 2/2 | `test_phase3_planner.py::test_FL03_return_departing_day_before_end_not_used`, `test_phase3_validator.py::test_FL03_return_departs_day_before_end` |
| `FL-04` | Tanish | P0 | PASS | 1/1 | `test_phase3_planner.py::test_FL04_seats_equal_travelers_is_eligible` |
| `FL-05` | Tanish | P0 | PASS | 2/2 | `test_phase3_planner.py::test_FL05_insufficient_outbound_seats_regardless_of_return`, `test_phase3_validator.py::test_FL05_insufficient_seats` |
| `FL-06` | Tanish | P0 | PASS | 1/1 | `test_phase3_planner.py::test_FL06_one_stop_flight_allowed` |
| `FL-07` | Tanish | P0 | PASS | 1/1 | `test_phase3_planner.py::test_FL07_airport_serving_city_without_own_airport` |
| `FL-08` | Tanish | P0 | PASS | 2/2 | `test_phase3_planner.py::test_FL08_country_airport_not_serving_first_city_rejected`, `test_phase3_validator.py::test_FL08_arrival_airport_not_serving_first_city` |
| `FL-09` | Tanish | P0 | PASS | 2/2 | `test_phase3_planner.py::test_FL09_return_must_land_at_home_airport`, `test_phase3_validator.py::test_FL09_return_to_other_airport` |
| `FL-10` | Tanish | P1 | PASS | 1/1 | `test_phase3_planner.py::test_FL10_different_arrival_and_return_airports` |
| `FL-11` | Tanish | P1 | PASS | 3/3 | `test_phase2_time.py::test_FL11_all_official_flights_chronological`, `test_phase2_time.py::test_FL11_duration_from_time_zones_not_naive_subtraction`, `test_phase2_time.py::test_FL11_inconsistent_flight_detected` |
| `FL-12` | Tanish | P1 | PASS | 1/1 | `test_phase2_pricing.py::test_FL12_infant_needs_a_paid_seat` |
| `HT-01` | Tanish | P0 | PASS | 1/1 | `test_phase2_hotels.py::test_HT01_one_zero_night_rejects_whole_stay` |
| `HT-02` | Tanish | P0 | PASS | 1/1 | `test_phase2_hotels.py::test_HT02_missing_row_is_unavailable` |
| `HT-03` | Tanish | P0 | PASS | 1/1 | `test_phase2_hotels.py::test_HT03_minimum_night_stock_below_rooms_needed` |
| `HT-04` | Tanish | P0 | PASS | 1/1 | `test_phase2_hotels.py::test_HT04_exact_stock_equal_to_rooms_is_eligible` |
| `HT-05` | Tanish | P0 | PASS | 2/2 | `test_phase2_hotels.py::test_HT05_one_room_total_capacity_exceeded`, `test_phase2_hotels.py::test_HT05_validator_rejects_capacity` |
| `HT-06` | Tanish | P0 | PASS | 2/2 | `test_phase2_hotels.py::test_HT06_each_room_needs_an_adult`, `test_phase2_hotels.py::test_HT06_one_adult_three_children_one_family_room` |
| `HT-07` | Tanish | P0 | PASS | 1/1 | `test_phase2_hotels.py::test_HT07_two_small_rooms_hold_family` |
| `HT-08` | Tanish | P0 | PASS | 2/2 | `test_phase3_planner.py::test_HT08_HT09_one_hotel_and_room_type_per_city`, `test_phase3_validator.py::test_CT04_HT08_same_city_twice_rejected` |
| `HT-09` | Tanish | P0 | PASS | 2/2 | `test_phase3_planner.py::test_HT08_HT09_one_hotel_and_room_type_per_city`, `test_phase3_validator.py::test_HT09_room_type_change_mid_city_rejected` |
| `HT-10` | Tanish | P0 | PASS | 2/2 | `test_phase3_planner.py::test_HT10_hotel_always_in_segment_city`, `test_phase3_validator.py::test_HT10_hotel_from_other_city` |
| `HT-11` | Tanish | P0 | PASS | 1/1 | `test_phase2_hotels.py::test_HT11_checkout_night_is_not_required` |
| `HT-12` | Tanish | P1 | PASS | 1/1 | `test_phase2_hotels.py::test_HT12_display_checkin_time_does_not_reject` |
| `HT-13` | Tanish | P1 | PASS | 1/1 | `test_phase2_hotels.py::test_HT13_room_of_another_hotel_rejected` |
| `HT-14` | Tanish | P1 | PASS | 1/1 | `test_phase2_hotels.py::test_HT14_exact_night_coverage_not_row_counts` |
| `PR-01` | Tanish | P0 | PASS | 1/1 | `test_phase2_pricing.py::test_PR01_flights_per_seat_times_travelers` |
| `PR-02` | Tanish | P0 | PASS | 1/1 | `test_phase2_pricing.py::test_PR02_hotel_per_room_night` |
| `PR-03` | Tanish | P0 | PASS | 1/1 | `test_phase2_pricing.py::test_PR03_per_person_transfer_with_child_price` |
| `PR-04` | Tanish | P0 | PASS | 1/1 | `test_phase2_pricing.py::test_PR04_missing_child_price_charges_adult_price` |
| `PR-05` | Tanish | P0 | PASS | 1/1 | `test_phase2_pricing.py::test_PR05_per_vehicle_rounds_up` |
| `PR-06` | Tanish | P0 | PASS | 1/1 | `test_phase2_pricing.py::test_PR06_attraction_adult_and_child` |
| `PR-07` | Tanish | P0 | PASS | 1/1 | `test_phase2_pricing.py::test_PR07_combined_total_and_item_sum` |
| `PR-08` | Tanish | P0 | PASS | 3/3 | `test_contract.py::test_over_budget_contract`, `test_phase3_planner.py::test_PR08_budget_equal_to_cheapest_is_built`, `test_phase4_integration.py::test_real_over_budget_reports_exact_cheapest_and_persists_nothing` |
| `PR-09` | Tanish | P0 | PASS | 2/2 | `test_contract.py::test_over_budget_contract`, `test_phase3_planner.py::test_PR09_one_rupee_short_is_over_budget` |
| `PR-10` | Tanish | P0 | PASS | 1/1 | `test_phase3_planner.py::test_PR10_cheapest_flight_without_compatible_transfer_skipped` |
| `PR-11` | Tanish | P0 | PASS | 1/1 | `test_phase3_planner.py::test_PR11_cheapest_room_missing_stock_one_night_excluded` |
| `PR-12` | Tanish | P0 | PASS | 1/1 | `test_phase3_planner.py::test_PR12_cheaper_sixth_candidate_is_found` |
| `PR-13` | Tanish | P0 | PASS | 2/2 | `test_phase3_planner.py::test_PR13_feasible_but_none_in_budget_is_over_budget_not_no_package`, `test_phase3_planner.py::test_PR13_zero_budget_is_over_budget` |
| `PR-14` | Tanish | P0 | PASS | 2/2 | `test_contract.py::test_no_package_contract`, `test_phase3_planner.py::test_PR14_no_legal_solution_is_no_package_without_price` |
| `PR-15` | Tanish | P1 | PASS | 1/1 | `test_phase3_planner.py::test_PR15_status_monotonic_in_budget` |
| `PR-16` | Tanish | P1 | PASS | 1/1 | `test_phase5_edits.py::test_PR16_vehicle_ceiling_and_full_reprice_after_change` |
| `PR-17` | Tanish | P1 | PASS | 1/1 | `test_phase2_pricing.py::test_PR17_free_attraction_priced_zero_not_missing` |
| `SY-01` | Tanish | P0 | PASS | 2/2 | `test_catalog_sy.py::test_sy01_no_catalog_sql_tables_or_importer`, `test_catalog_sy.py::test_sy01_official_catalog_loads_with_expected_counts` |
| `SY-02` | Tanish | P0 | PASS | 1/1 | `test_catalog_sy.py::test_sy02_reload_is_stable_and_has_no_duplicates` |
| `SY-03` | Tanish | P0 | PASS | 2/2 | `test_catalog_sy.py::test_sy03_hashes_unchanged_after_domain_operations`, `test_catalog_sy.py::test_sy03_official_files_match_phase0_baseline` |
| `SY-04` | Tanish | P0 | PASS | 2/2 | `test_catalog_sy.py::test_sy04_invalid_catalog_cannot_produce_a_domain`, `test_catalog_sy.py::test_sy04_room_with_unknown_hotel_fails_clearly` |
| `SY-05` | Tanish | P0 | PASS | 2/2 | `test_catalog_sy.py::test_sy05_nara_attractions_tolerated_but_not_plannable`, `test_catalog_sy.py::test_sy05_synthetic_orphan_attraction` |
| `SY-11` | Tanish | P0 | PASS | 8/8 | `test_contract_review.py::test_booked_trip_rejects`, `test_phase2_packages.py::test_booked_without_snapshot_or_mixed_snapshot_refused` |
| `SY-14` | Tanish | P0 | PASS | 3/3 | `test_contract_review.py::test_built_end_date_must_be_start_plus_nights`, `test_phase3_planner.py::test_SY14_end_date_is_start_plus_nights`, `test_phase3_validator.py::test_SY14_end_date_minus_one_rejected` |
| `SY-17` | Tanish | P1 | PASS | 8/8 | `test_phase3_planner.py::test_SY17_many_official_requests_are_consistent` |
| `TR-01` | Tanish | P0 | PASS | 2/2 | `test_phase3_planner.py::test_TR01_pickup_exactly_60_minutes_after_landing`, `test_phase3_validator.py::test_TR01_TR02_sixty_minute_boundary` |
| `TR-02` | Tanish | P0 | PASS | 1/1 | `test_phase3_validator.py::test_TR01_TR02_sixty_minute_boundary` |
| `TR-03` | Tanish | P0 | PASS | 3/3 | `test_phase3_planner.py::test_TR03_closed_at_night_waits_for_morning_service`, `test_phase3_planner.py::test_TR03_late_landing_uses_next_day_service`, `test_phase3_validator.py::test_TR03_overnight_service_window_validated` |
| `TR-04` | Tanish | P0 | PASS | 2/2 | `test_phase3_planner.py::test_TR04_departure_pickup_exactly_180_minutes_boundary`, `test_phase3_validator.py::test_TR04_TR05_TR06_180_minute_boundary` |
| `TR-05` | Tanish | P0 | PASS | 1/1 | `test_phase3_validator.py::test_TR04_TR05_TR06_180_minute_boundary` |
| `TR-06` | Tanish | P0 | PASS | 1/1 | `test_phase3_validator.py::test_TR04_TR05_TR06_180_minute_boundary` |
| `TR-07` | Tanish | P0 | PASS | 3/3 | `test_phase3_planner.py::test_TR07_area_specific_transfer_not_used_for_other_area`, `test_phase3_planner.py::test_TR07_planner_picks_hotel_area_with_a_transfer`, `test_phase3_validator.py::test_TR07_wrong_area_transfer` |
| `TR-08` | Tanish | P0 | PASS | 2/2 | `test_phase3_planner.py::test_TR08_area_null_serves_every_area`, `test_phase3_validator.py::test_TR08_TR15_null_area_used_in_reverse` |
| `TR-09` | Tanish | P0 | PASS | 2/2 | `test_phase2_time.py::test_TR09_official_kix_shuttle`, `test_phase2_time.py::test_TR09_overnight_window_pickup_after_midnight` |
| `TR-10` | Tanish | P0 | PASS | 2/2 | `test_phase2_time.py::test_TR10_official_24h_transfers_do_not_crash`, `test_phase2_time.py::test_TR10_service_until_2400_is_end_of_day_midnight` |
| `TR-11` | Tanish | P0 | PASS | 2/2 | `test_phase3_planner.py::test_TR11_TR12_only_linked_directed_intercity_ids_used`, `test_phase3_validator.py::test_TR11_unlinked_intercity_id` |
| `TR-12` | Tanish | P0 | PASS | 2/2 | `test_phase3_planner.py::test_TR11_TR12_only_linked_directed_intercity_ids_used`, `test_phase3_validator.py::test_TR12_wrong_direction_intercity` |
| `TR-13` | Tanish | P0 | PASS | 5/5 | `test_phase2_time.py::test_TR13_frequency_defines_discrete_departures`, `test_phase2_time.py::test_TR13_next_departure`, `test_phase2_time.py::test_TR13_official_train_schedule` (+2) |
| `TR-14` | Tanish | P0 | PASS | 1/1 | `test_phase3_validator.py::test_TR14_attraction_during_intercity_transfer` |
| `TR-15` | Tanish | P1 | PASS | 2/2 | `test_phase3_planner.py::test_TR15_airport_record_used_in_reverse_for_return`, `test_phase3_validator.py::test_TR08_TR15_null_area_used_in_reverse` |
| `TR-16` | Tanish | P1 | PASS | 1/1 | `test_phase2_pricing.py::test_TR16_vehicle_quantity_seven_travelers_capacity_four` |
| `TR-17` | Tanish | P1 | PASS | 2/2 | `test_phase3_planner.py::test_TR17_station_transfer_without_optional_city_taxi`, `test_phase3_validator.py::test_TR17_optional_city_taxi_validated` |
| `AT-01` | Sandeep | P0 | PASS | 2/2 | `test_contract_review.py::test_full_day_minimum_only_in_initial`, `test_oracle_attractions.py::test_AT_01_full_day_without_attraction_rejected_initially` |
| `AT-02` | Sandeep | P0 | PASS | 4/4 | `test_contract_review.py::test_max_two_per_day_every_mode`, `test_oracle_attractions.py::test_AT_02_three_on_a_full_day_rejected` |
| `AT-03` | Sandeep | P0 | PASS | 3/3 | `test_contract_review.py::test_arrival_and_change_days_allow_zero_to_two`, `test_oracle_attractions.py::test_AT_03_arrival_and_change_days_may_be_empty` |
| `AT-04` | Sandeep | P0 | PASS | 4/4 | `test_contract_review.py::test_return_day_zero_every_mode`, `test_oracle_attractions.py::test_AT_04_return_day_attraction_rejected` |
| `AT-05` | Sandeep | P0 | PASS | 2/2 | `test_oracle_attractions.py::test_AT_05_nara_during_kyoto_night_rejected`, `test_oracle_attractions.py::test_AT_05_tokyo_attraction_on_kyoto_night_rejected` |
| `AT-06` | Sandeep | P0 | PASS | 1/1 | `test_oracle_attractions.py::test_AT_06_closed_date_overrides_weekly_schedule` |
| `AT-07` | Sandeep | P0 | PASS | 1/1 | `test_oracle_attractions.py::test_AT_07_wrong_weekday_rejected` |
| `AT-08` | Sandeep | P0 | PASS | 1/1 | `test_oracle_attractions.py::test_AT_08_start_before_opening_rejected` |
| `AT-09` | Sandeep | P0 | PASS | 1/1 | `test_oracle_attractions.py::test_AT_09_visit_ending_exactly_at_close_allowed` |
| `AT-10` | Sandeep | P0 | PASS | 1/1 | `test_oracle_attractions.py::test_AT_10_visit_ending_after_close_rejected` |
| `AT-11` | Sandeep | P0 | PASS | 1/1 | `test_oracle_attractions.py::test_AT_11_overlap_rejected` |
| `AT-12` | Sandeep | P0 | PASS | 1/1 | `test_oracle_attractions.py::test_AT_12_adjacent_visits_allowed` |
| `AT-13` | Sandeep | P0 | PASS | 1/1 | `test_oracle_attractions.py::test_AT_13_same_attraction_twice_rejected` |
| `AT-14` | Sandeep | P0 | PASS | 2/2 | `test_oracle_attractions.py::test_AT_14_before_city_arrival_rejected` |
| `AT-15` | Sandeep | P0 | PASS | 3/3 | `test_contract_review.py::test_full_day_minimum_only_in_initial`, `test_oracle_attractions.py::test_AT_15_removing_sole_full_day_attraction_ok_after_edit`, `test_phase5_edits.py::test_remove_sole_attraction_of_full_day_allowed_after_build` |
| `AT-16` | Sandeep | P1 | PASS | 5/5 | `test_catalog_loader.py::test_optional_fields_normalized`, `test_oracle_attractions.py::test_AT_16_closed_dates_absent_or_null_normalized`, `test_oracle_attractions.py::test_AT_16_real_catalog_normalizes_without_crash` |
| `AT-17` | Sandeep | P1 | PASS | 1/1 | `test_oracle_attractions.py::test_AT_17_child_friendly_false_is_not_a_hard_rule` |
| `BK-01` | Sandeep | P0 | PASS | 3/3 | `test_phase3_transactions.py::test_twenty_concurrent_confirms_one_reservation`, `test_phase6_bookings.py::test_BK_01_double_click_same_key_and_new_key`, `test_phase6_bookings.py::test_BK_01_twenty_concurrent_confirms` |
| `BK-02` | Sandeep | P0 | PASS | 2/2 | `test_phase3_transactions.py::test_confirm_retries_return_same_reference`, `test_phase6_bookings.py::test_BK_02_lost_response_retry_returns_same_booking` |
| `BK-03` | Sandeep | P0 | PASS | 1/1 | `test_phase6_bookings.py::test_BK_03_retry_after_backend_restart` |
| `BK-04` | Sandeep | P0 | PASS | 2/2 | `test_phase3_transactions.py::test_two_packages_confirm_concurrently_distinct`, `test_phase6_bookings.py::test_BK_04_two_packages_confirm_concurrently` |
| `BK-05` | Sandeep | P0 | PASS | 2/2 | `test_phase3_transactions.py::test_reference_collision_retries`, `test_phase6_bookings.py::test_BK_05_duplicate_generated_reference_retries` |
| `BK-06` | Sandeep | P0 | PASS | 4/4 | `test_phase1_models.py::test_reference_text_keeps_leading_zero`, `test_phase2_packages.py::test_booked_export_uses_immutable_snapshot`, `test_phase3_transactions.py::test_reference_collision_retries` (+1) |
| `BK-07` | Sandeep | P0 | PASS | 4/4 | `test_phase3_transactions.py::test_crash_during_confirm_rolls_back_everything`, `test_phase6_bookings.py::test_BK_07_08_crash_before_commit_rolls_back` |
| `BK-08` | Sandeep | P0 | PASS | 2/2 | `test_phase3_transactions.py::test_crash_during_confirm_rolls_back_everything` |
| `BK-09` | Sandeep | P0 | PASS | 3/3 | `test_phase3_transactions.py::test_confirm_retries_return_same_reference`, `test_phase3_transactions.py::test_twenty_concurrent_confirms_one_reservation`, `test_phase6_bookings.py::test_BK_09_distinct_keys_one_reservation` |
| `BK-10` | Sandeep | P0 | PASS | 3/3 | `test_phase3_transactions.py::test_confirm_books_latest_version`, `test_phase6_bookings.py::test_BK_10_confirm_books_latest_edited_version` |
| `BK-11` | Sandeep | P0 | PASS | 4/4 | `test_phase3_transactions.py::test_confirm_rejected_by_validation`, `test_phase6_bookings.py::test_BK_11_confirm_after_rejected_edit_books_last_valid_draft`, `test_phase6_bookings.py::test_BK_11_platform_rejected_edits_never_reach_booking` |
| `BK-12` | Sandeep | P0 | PASS | 6/6 | `test_phase3_transactions.py::test_reservation_lookup_not_found`, `test_phase6_bookings.py::test_BK_12_unknown_reference_not_found` |
| `BK-13` | Sandeep | P1 | PASS | 15/15 | `test_phase3_transactions.py::test_reservation_lookup_not_found`, `test_phase6_bookings.py::test_BK_13_malformed_reference_not_found` |
| `BK-15` | Sandeep | P1 | PASS | 2/2 | `test_phase2_packages.py::test_draft_survives_app_restart`, `test_phase4_integration.py::test_BK_15_restart_keeps_real_draft` |
| `BK-16` | Sandeep | P1 | PASS | 1/1 | `test_phase6_bookings.py::test_BK_16_no_confirmable_draft_after_no_budget_fit` |
| `ED-09` | Sandeep | P0 | PASS | 2/2 | `test_phase5_edits.py::test_ED_09_valid_hotel_then_flight_change` |
| `ED-10` | Sandeep | P0 | PASS | 2/2 | `test_phase5_edits.py::test_ED_10_remove_attraction_reprices_and_versions` |
| `ED-11` | Sandeep | P0 | PASS | 6/6 | `test_phase3_transactions.py::test_rejected_change_leaves_row_identical`, `test_phase5_edits.py::test_ED_11_edit_exceeding_budget_rejected`, `test_phase5_edits.py::test_ED_11_over_budget_draft_is_never_confirmed` |
| `ED-12` | Sandeep | P0 | PASS | 4/4 | `test_phase3_transactions.py::test_stale_version_conflict_leaves_row_identical`, `test_phase3_transactions.py::test_two_tabs_same_version_one_wins`, `test_phase5_edits.py::test_ED_12_two_tabs_same_version_no_lost_update` |
| `ED-13` | Sandeep | P0 | PASS | 3/3 | `test_phase3_transactions.py::test_patch_vs_confirm_is_serializable`, `test_phase5_edits.py::test_ED_13_patch_and_confirm_serializable` |
| `ED-14` | Sandeep | P0 | PASS | 3/3 | `test_phase3_transactions.py::test_same_key_same_payload_replays_after_version_advanced`, `test_phase5_edits.py::test_ED_14_same_key_same_payload_replays_after_version_advanced` |
| `ED-15` | Sandeep | P0 | PASS | 3/3 | `test_phase3_transactions.py::test_same_key_different_payload_conflicts`, `test_phase5_edits.py::test_ED_15_same_key_different_payload_conflicts` |
| `ED-16` | Sandeep | P0 | PASS | 3/3 | `test_phase3_transactions.py::test_crash_between_update_and_idempotency_insert_rolls_back`, `test_phase5_edits.py::test_ED_16_crash_between_update_and_idempotency_insert` |
| `ED-17` | Sandeep | P1 | PASS | 2/2 | `test_phase5_edits.py::test_ED_17_two_keys_sequential_edits_both_persist` |
| `ED-18` | Sandeep | P1 | PASS | 3/3 | `test_phase3_transactions.py::test_patch_after_booked_rejected`, `test_phase5_edits.py::test_ED_18_patch_after_confirmed_rejected_snapshot_unchanged` |
| `ED-19` | Sandeep | P1 | PASS | 3/3 | `test_phase3_transactions.py::test_stale_version_conflict_leaves_row_identical`, `test_phase5_edits.py::test_ED_19_stale_version_rechecked_inside_row_lock` |
| `IN-01` | Sandeep | P0 | PASS | 1/1 | `test_phase1_input_validation.py::test_IN_01_nights_below_minimum` |
| `IN-02` | Sandeep | P0 | PASS | 1/1 | `test_phase1_input_validation.py::test_IN_02_nights_above_maximum` |
| `IN-03` | Sandeep | P0 | PASS | 5/5 | `test_phase1_input_validation.py::test_IN_03_nights_boundaries_accepted`, `test_phase4_integration.py::test_real_valid_inputs_accepted` |
| `IN-04` | Sandeep | P0 | PASS | 3/3 | `test_contract_review.py::test_outside_window_is_no_package`, `test_phase1_input_validation.py::test_IN_04_start_before_first_night` |
| `IN-05` | Sandeep | P0 | PASS | 3/3 | `test_contract_review.py::test_outside_window_is_no_package`, `test_phase1_input_validation.py::test_IN_05_return_after_last_departure` |
| `IN-06` | Sandeep | P0 | PASS | 5/5 | `test_contract_review.py::test_window_boundary_is_buildable`, `test_phase1_input_validation.py::test_IN_06_window_boundary_valid`, `test_phase4_integration.py::test_real_valid_inputs_accepted` |
| `IN-07` | Sandeep | P0 | PASS | 2/2 | `test_phase1_input_validation.py::test_IN_07_unknown_departure_city`, `test_phase4_integration.py::test_real_invalid_input_rejected_before_domain` |
| `IN-08` | Sandeep | P0 | PASS | 1/1 | `test_phase1_input_validation.py::test_IN_08_unknown_country` |
| `IN-09` | Sandeep | P0 | PASS | 4/4 | `test_phase1_input_validation.py::test_IN_09_invalid_child_ages` |
| `IN-10` | Sandeep | P0 | PASS | 4/4 | `test_phase1_input_validation.py::test_IN_10_child_age_boundaries_counted`, `test_phase4_integration.py::test_real_valid_inputs_accepted` |
| `IN-11` | Sandeep | P0 | PASS | 2/2 | `test_phase1_input_validation.py::test_IN_11_adults_must_be_positive` |
| `IN-12` | Sandeep | P0 | PASS | 1/1 | `test_phase1_input_validation.py::test_IN_12_negative_budget` |
| `IN-13` | Sandeep | P1 | PASS | 3/3 | `test_contract.py::test_fake_build_three_outcomes`, `test_phase1_input_validation.py::test_IN_13_zero_budget_is_over_budget_not_no_package`, `test_phase4_integration.py::test_real_over_budget_reports_exact_cheapest_and_persists_nothing` |
| `IN-14` | Sandeep | P1 | PASS | 17/17 | `test_phase1_input_validation.py::test_IN_14_type_errors_never_coerced` |
| `IN-15` | Sandeep | P1 | PASS | 18/18 | `test_phase1_input_validation.py::test_IN_15_client_injected_fields_rejected`, `test_phase1_input_validation.py::test_IN_15_empty_children_array_is_valid`, `test_phase1_input_validation.py::test_IN_15_empty_or_malformed_body` (+1) |
| `IN-16` | Sandeep | P2 | PASS | 10/10 | `test_phase1_input_validation.py::test_IN_16_hostile_values`, `test_phase1_input_validation.py::test_IN_16_oversized_body_rejected_early`, `test_phase1_input_validation.py::test_IN_16_very_large_valid_budget_ok` |
| `PR-18` | Sandeep | P1 | PASS | 12/12 | `test_phase5_edits.py::test_PR_18_client_amounts_in_patch_rejected`, `test_phase5_edits.py::test_PR_18_totals_after_edit_are_server_derived` |
| `SY-06` | Sandeep | P0 | PASS | 2/2 | `test_phase2_packages.py::test_draft_export_full_and_judge_shaped`, `test_phase4_integration.py::test_SY_06_two_item_sample_export_fails` |
| `SY-07` | Sandeep | P0 | PASS | 1/1 | `test_phase4_integration.py::test_SY_07_hotel_without_room_id_fails` |
| `SY-08` | Sandeep | P0 | PASS | 2/2 | `test_phase4_integration.py::test_SY_08_flight_wrong_quantity_or_price_fails` |
| `SY-09` | Sandeep | P0 | PASS | 1/1 | `test_phase4_integration.py::test_SY_09_per_vehicle_transfer_with_people_quantity_fails` |
| `SY-10` | Sandeep | P0 | PASS | 2/2 | `test_contract_review.py::test_draft_export_with_booked_item_rejected`, `test_phase4_integration.py::test_SY_10_draft_with_booked_item_fails_and_export_normalizes` |
| `SY-12` | Sandeep | P0 | PASS | 1/1 | `test_phase4_integration.py::test_SY_12_missing_sources_fails` |
| `SY-13` | Sandeep | P0 | PASS | 4/4 | `test_phase4_integration.py::test_SY_13_invented_catalog_id_fails` |
| `SY-15` | Sandeep | P1 | PASS | 2/2 | `test_phase2_itinerary_js.py::test_cross_zone_order_kept_from_domain`, `test_phase4_integration.py::test_SY_15_cross_zone_timeline_in_real_time` |
| `SY-16` | Sandeep | P1 | PASS | 1/1 | `test_phase6_bookings.py::test_SY_16_refresh_after_booking_shows_persisted_read_only_state` |
| `SY-18` | Sandeep | P1 | PASS | 3/4 | `test_phase5_edits.py::test_SY_18_forged_hidden_ids_rejected_on_patch`, `test_phase5_edits.py::test_SY_18_tampered_stored_price_caught_at_confirm` |
| `SY-19` | Sandeep | P1 | PASS | 5/5 | `test_phase1_readiness.py::test_SY_19_catalog_missing_not_ready`, `test_phase1_readiness.py::test_SY_19_database_down_app_still_starts`, `test_phase1_readiness.py::test_SY_19_ready_when_db_and_catalog_ok` (+2) |
| `SY-20` | Sandeep | P2 | PASS | 1/1 | `test_phase6_bookings.py::test_SY_20_reservation_lookup_privacy_safeguards` |

## Additional evidence (outside pytest)

- Browser (headless Chrome via DevTools, `tests/platform/smoke/browser_smoke.py`): fake mode 22/22; real mode 12/12; against the **Docker app container** 14/14 (Build -> change flight options -> remove attraction -> confirm -> refresh/Back (SY-16) -> header lookup -> Download JSON = booked export). No JS errors.
- Docker rehearsal (`tests/platform/smoke/docker_rehearsal.py`, app container on `bookingDB_test`): 10/10 — 20 concurrent confirms -> 1 reference (BK-01), 2 packages -> distinct references (BK-04), 2-tab edit -> 200/409 (ED-12), `docker compose restart app` -> reservation identical, original-key retry same reference (BK-03), edited draft + version identical (BK-15), unknown reference 404 (BK-12).
- Concurrency mutation checks: with `SELECT ... FOR UPDATE` removed, the lock-dependent concurrency tests fail (Phase 3: 4/5; Phase 6: 20-request HTTP burst).
