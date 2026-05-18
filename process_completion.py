#!/usr/bin/env python3
"""
Finalize per-country outputs by removing countries with no usable spatial results
and updating countries_list.txt accordingly.

Default behavior is dry-run (no deletions). Use --apply to make changes.
"""

from __future__ import annotations

import argparse
import csv
import re
from pathlib import Path
from typing import Dict, List, Set, Tuple

import pandas as pd
import geopandas as gpd

from config import get_bigdata_path


DEFAULT_SCENARIOS = [
    "2024_supply_100%",
    "2030_supply_100%_add_v2",
    "2050_supply_100%_add_v2",
]

PARQUET_LAYER_PATTERN = re.compile(r"^(?P<layer>.+)_(?P<iso3>[A-Z]{3})(?:_add_v2)?\.parquet$")


def read_country_list(path: Path) -> List[str]:
    if not path.exists():
        return []
    countries: List[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        token = line.strip().upper()
        if token:
            countries.append(token)
    return sorted(set(countries))


def discover_summary_countries(scenario_dir: Path, scenario_name: str) -> Set[str]:
    """Parse ISO3 country codes from scenario summary xlsx files."""
    countries: Set[str] = set()
    if not scenario_dir.exists():
        return countries

    prefix = f"{scenario_name}_"
    for xlsx in scenario_dir.glob("*.xlsx"):
        name = xlsx.name
        if not name.startswith(prefix):
            continue

        # Remove scenario prefix and extension, then strip optional add_v2 suffix.
        stem = name[len(prefix):]
        if not stem.endswith(".xlsx"):
            continue
        stem = stem[:-5]
        if stem.endswith("_add_v2"):
            stem = stem[:-7]

        if re.fullmatch(r"[A-Z]{3}", stem):
            countries.add(stem)

    return countries


def has_required_layer(scenario_dir: Path, country_iso3: str, required_layer: str) -> bool:
    """Check for required parquet layer for one scenario/country."""
    candidates = [
        scenario_dir / f"{required_layer}_{country_iso3}.parquet",
        scenario_dir / f"{required_layer}_{country_iso3}_add_v2.parquet",
    ]
    return any(path.exists() for path in candidates)


def parse_layer_and_iso3(file_name: str) -> Tuple[str, str] | None:
    match = PARQUET_LAYER_PATTERN.fullmatch(file_name)
    if not match:
        return None
    return match.group("layer"), match.group("iso3")


def discover_expected_layers(scenario_dir: Path) -> Set[str]:
    """Discover all parquet layer names present in a scenario directory."""
    layers: Set[str] = set()
    if not scenario_dir.exists():
        return layers

    for parquet_path in scenario_dir.glob("*.parquet"):
        parsed = parse_layer_and_iso3(parquet_path.name)
        if parsed is None:
            continue
        layer_name, _ = parsed
        layers.add(layer_name)
    return layers


def discover_country_layers(scenario_dir: Path, iso3: str) -> Set[str]:
    """Discover parquet layers present for one country in a scenario directory."""
    layers: Set[str] = set()
    if not scenario_dir.exists():
        return layers

    for parquet_path in scenario_dir.glob(f"*_{iso3}*.parquet"):
        parsed = parse_layer_and_iso3(parquet_path.name)
        if parsed is None:
            continue
        layer_name, file_iso3 = parsed
        if file_iso3 == iso3:
            layers.add(layer_name)
    return layers


def classify_countries(
    output_root: Path,
    scenarios: List[str],
    countries_to_check: List[str],
    required_layer: str,
) -> Tuple[List[str], List[str], Dict[str, List[str]]]:
    """
    Return (valid_countries, invalid_countries, missing_by_country).

    A country is valid only if required_layer parquet exists in every scenario folder.
    """
    valid: List[str] = []
    invalid: List[str] = []
    missing_by_country: Dict[str, List[str]] = {}

    for iso3 in sorted(set(countries_to_check)):
        missing_scenarios: List[str] = []
        for scenario in scenarios:
            scenario_dir = output_root / scenario
            if not has_required_layer(scenario_dir, iso3, required_layer):
                missing_scenarios.append(scenario)

        if missing_scenarios:
            invalid.append(iso3)
            missing_by_country[iso3] = missing_scenarios
        else:
            valid.append(iso3)

    return valid, invalid, missing_by_country


def files_to_remove_for_country(scenario_dir: Path, iso3: str) -> List[Path]:
    """Collect per-country outputs in one scenario folder to remove."""
    patterns = [
        f"*_{iso3}.parquet",
        f"*_{iso3}_add_v2.parquet",
        f"*_{iso3}.xlsx",
        f"*_{iso3}_add_v2.xlsx",
    ]

    files: List[Path] = []
    for pattern in patterns:
        files.extend(scenario_dir.glob(pattern))

    # De-duplicate while preserving order.
    seen = set()
    unique_files: List[Path] = []
    for file_path in files:
        if file_path not in seen and file_path.is_file():
            unique_files.append(file_path)
            seen.add(file_path)

    return unique_files


def write_country_list(path: Path, countries: List[str]) -> None:
    text = "\n".join(countries)
    if text:
        text += "\n"
    path.write_text(text, encoding="utf-8")


def load_master_country_universe() -> Set[str]:
    """
    Load the canonical processable country universe from demand and GADM intersection.
    This keeps excluded countries visible in audit even after countries_list.txt is pruned.
    """
    demand_file = Path("outputs_processed_data/p1_b_ember_2024_30_50.xlsx")
    gadm_file = Path(get_bigdata_path("bigdata_gadm")) / "gadm_410-levels.gpkg"

    if not demand_file.exists() or not gadm_file.exists():
        return set()

    try:
        demand_df = pd.read_excel(demand_file)
        if "ISO3_code" not in demand_df.columns:
            return set()
        demand_countries = {
            str(c).strip().upper()
            for c in demand_df["ISO3_code"].dropna().unique()
            if str(c).strip()
        }

        admin_df = gpd.read_file(gadm_file, layer="ADM_0", columns=["GID_0"])
        gadm_countries = {
            str(c).strip().upper()
            for c in admin_df["GID_0"].dropna().unique()
            if str(c).strip()
        }
        return demand_countries & gadm_countries
    except Exception:
        return set()


def write_audit_csv(
    path: Path,
    countries: List[str],
    scenarios: List[str],
    required_layer: str,
    missing_by_country: Dict[str, List[str]],
    deletion_plan: Dict[str, List[Path]],
    expected_layers_by_scenario: Dict[str, Set[str]],
    country_layers_by_scenario: Dict[str, Dict[str, Set[str]]],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "iso3",
                "status",
                "required_layer",
                "missing_scenarios",
                "missing_layers",
                "missing_layers_by_scenario",
                "files_matched_for_removal",
            ]
        )
        for iso3 in countries:
            missing = missing_by_country.get(iso3, [])
            status = "excluded" if missing else "kept"

            missing_layer_items: List[str] = []
            missing_by_scenario_items: List[str] = []
            for scenario in scenarios:
                expected_layers = expected_layers_by_scenario.get(scenario, set())
                country_layers = country_layers_by_scenario.get(scenario, {}).get(iso3, set())
                scenario_missing_layers = sorted(expected_layers - country_layers)
                for layer_name in scenario_missing_layers:
                    missing_layer_items.append(f"{scenario}:{layer_name}")
                if scenario_missing_layers:
                    joined = ";".join(scenario_missing_layers)
                    missing_by_scenario_items.append(f"{scenario}=[{joined}]")

            writer.writerow(
                [
                    iso3,
                    status,
                    required_layer,
                    ";".join(missing),
                    ";".join(missing_layer_items),
                    " | ".join(missing_by_scenario_items),
                    len(deletion_plan.get(iso3, [])),
                ]
            )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Remove no-data country outputs from selected scenario folders and "
            "rewrite countries_list.txt based on required parquet layer presence."
        )
    )
    parser.add_argument(
        "--output-root",
        default="outputs_per_country/parquet",
        help="Root directory containing scenario folders (default: outputs_per_country/parquet)",
    )
    parser.add_argument(
        "--countries-file",
        default="countries_list.txt",
        help="Country list file to update (default: countries_list.txt)",
    )
    parser.add_argument(
        "--required-layer",
        default="centroids",
        help="Layer required in each scenario to keep a country (default: centroids)",
    )
    parser.add_argument(
        "--scenario",
        action="append",
        dest="scenarios",
        help=(
            "Scenario folder name to check. Repeat for multiple values. "
            "Defaults: 2024_supply_100%%, 2030_supply_100%%_add_v2, 2050_supply_100%%_add_v2"
        ),
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply deletions and rewrite countries file (default is dry-run)",
    )
    parser.add_argument(
        "--audit-csv",
        default="process_completion_audit.csv",
        help="Path to write CSV audit report (default: process_completion_audit.csv)",
    )
    args = parser.parse_args()

    output_root = Path(args.output_root)
    countries_file = Path(args.countries_file)
    scenarios = args.scenarios if args.scenarios else DEFAULT_SCENARIOS

    # Start from existing countries_list if present; otherwise infer from summary xlsx files.
    file_countries = read_country_list(countries_file)
    discovered_by_scenario: Dict[str, Set[str]] = {}
    discovered_union: Set[str] = set()

    for scenario in scenarios:
        scenario_dir = output_root / scenario
        discovered = discover_summary_countries(scenario_dir, scenario)
        discovered_by_scenario[scenario] = discovered
        discovered_union.update(discovered)

    if file_countries:
        countries_to_check = sorted(set(file_countries).union(discovered_union))
    else:
        countries_to_check = sorted(discovered_union)

    # For comprehensive auditing, include canonical universe even if countries were
    # already removed from countries_list.txt and output files.
    master_universe = load_master_country_universe()
    if master_universe:
        countries_to_check = sorted(set(countries_to_check).union(master_universe))

    if not countries_to_check:
        print("No countries found in countries list or scenario summaries. Nothing to do.")
        return 0

    valid, invalid, missing_by_country = classify_countries(
        output_root=output_root,
        scenarios=scenarios,
        countries_to_check=countries_to_check,
        required_layer=args.required_layer,
    )

    # Build deletion plan for invalid countries.
    deletion_plan: Dict[str, List[Path]] = {}
    for iso3 in invalid:
        files: List[Path] = []
        for scenario in scenarios:
            scenario_dir = output_root / scenario
            files.extend(files_to_remove_for_country(scenario_dir, iso3))
        deletion_plan[iso3] = files

    # Build full audit list in stable order.
    audit_countries = sorted(set(countries_to_check))

    expected_layers_by_scenario: Dict[str, Set[str]] = {}
    country_layers_by_scenario: Dict[str, Dict[str, Set[str]]] = {}
    for scenario in scenarios:
        scenario_dir = output_root / scenario
        expected_layers_by_scenario[scenario] = discover_expected_layers(scenario_dir)
        country_layers_by_scenario[scenario] = {}
        for iso3 in audit_countries:
            country_layers_by_scenario[scenario][iso3] = discover_country_layers(scenario_dir, iso3)

    audit_path = Path(args.audit_csv)
    write_audit_csv(
        path=audit_path,
        countries=audit_countries,
        scenarios=scenarios,
        required_layer=args.required_layer,
        missing_by_country=missing_by_country,
        deletion_plan=deletion_plan,
        expected_layers_by_scenario=expected_layers_by_scenario,
        country_layers_by_scenario=country_layers_by_scenario,
    )

    print("=" * 72)
    print("PROCESS COMPLETION REPORT")
    print("=" * 72)
    print(f"Scenarios checked: {', '.join(scenarios)}")
    print(f"Required layer: {args.required_layer}")
    print(f"Countries checked: {len(countries_to_check)}")
    print(f"Countries kept: {len(valid)}")
    print(f"Countries excluded: {len(invalid)}")

    if invalid:
        print("\nExcluded countries and missing scenarios:")
        for iso3 in invalid:
            missing = ", ".join(missing_by_country.get(iso3, []))
            print(f"  - {iso3}: missing {args.required_layer} in [{missing}]")

    total_files = sum(len(v) for v in deletion_plan.values())
    print(f"\nFiles matched for removal: {total_files}")
    print(f"Audit CSV written: {audit_path}")

    if not args.apply:
        print("\nDry-run mode (no files deleted, countries list not rewritten).")
        print("Re-run with --apply to execute.")
        return 0

    # Apply deletions.
    deleted_count = 0
    for iso3, files in deletion_plan.items():
        if not files:
            continue
        for file_path in files:
            try:
                file_path.unlink(missing_ok=True)
                deleted_count += 1
            except Exception as exc:
                print(f"[WARN] Could not delete {file_path}: {exc}")

    # Rewrite countries list with valid countries.
    write_country_list(countries_file, valid)

    print("\nApplied changes:")
    print(f"  Deleted files: {deleted_count}")
    print(f"  Updated countries list: {countries_file}")
    print(f"  Final country count: {len(valid)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
