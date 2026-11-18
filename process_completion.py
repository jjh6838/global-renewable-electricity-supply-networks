#!/usr/bin/env python3
"""
Finalize per-country outputs by removing countries with no usable spatial results
and updating countries_list.txt accordingly.

The default run writes an Excel audit without changing country outputs.
Use --fix-excel-names for safe legacy summary renames, or --apply for cleanup.
"""

from __future__ import annotations

import argparse
import os
import re
from pathlib import Path
from typing import Dict, List, Set, Tuple

import pandas as pd
import geopandas as gpd

from config import get_bigdata_path, get_supply_scenario_name


DEFAULT_SCENARIOS = [
    get_supply_scenario_name(
        year, 1.0, f"p1_b_ember_2024_30_50_{scenario}.xlsx"
    ) + ("_add_v2" if year != 2024 else "")
    for year in (2024, 2030, 2050)
    for scenario in ("100pct", "80pct", "60pct")
]

NAMING_SCENARIOS = [
    get_supply_scenario_name(year, 1.0, f"p1_b_ember_2024_30_50_{scenario}.xlsx") + suffix
    for year in (2024, 2030, 2050)
    for scenario in ("100pct", "80pct", "60pct")
    for suffix in (("", "_add_v2") if year != 2024 else ("",))
]
PARQUET_LAYER_PATTERN = re.compile(r"^(?P<layer>.+)_(?P<iso3>[A-Z]{3})(?:_add_v2)?\.parquet$")
SCENARIO_PATTERN = re.compile(
    r"^(?P<year>2024|2030|2050)_supply_100%_(?P<scenario>100pct|80pct|60pct)(?P<suffix>_add_v2)?$"
)
SUMMARY_PATTERN = re.compile(
    r"^(?P<year>2024|2030|2050)_supply_100%_(?:(?P<scenario>100pct|80pct|60pct)_)?"
    r"(?P<iso3>[A-Z]{3})(?P<suffix>_add_v2)?\.xlsx$"
)


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

    scenario_match = SCENARIO_PATTERN.fullmatch(scenario_name)
    if not scenario_match:
        return countries
    for xlsx in scenario_dir.glob("*.xlsx"):
        match = SUMMARY_PATTERN.fullmatch(xlsx.name)
        if (match and match.group("year") == scenario_match.group("year")
                and match.group("scenario") in (None, scenario_match.group("scenario"))
                and match.group("suffix") == scenario_match.group("suffix")):
            countries.add(match.group("iso3"))

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
    demand_file = Path(os.environ.get("P1_B_SCENARIO_FILE", "outputs_processed_data/p1_b_ember_2024_30_50_100pct.xlsx"))
    gadm_file = Path(get_bigdata_path("bigdata_gadm")) / "gadm_410-levels.gpkg"

    if not demand_file.exists() or not gadm_file.exists():
        print("[WARN] Master country universe unavailable: demand workbook or GADM file missing.")
        return set()

    demand_df = pd.read_excel(demand_file)
    if "ISO3_code" not in demand_df.columns:
        raise ValueError(f"Missing ISO3_code column in {demand_file}")
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


def build_country_audit(
    countries: List[str],
    scenarios: List[str],
    required_layer: str,
    missing_by_country: Dict[str, List[str]],
    deletion_plan: Dict[str, List[Path]],
    expected_layers_by_scenario: Dict[str, Set[str]],
    country_layers_by_scenario: Dict[str, Dict[str, Set[str]]],
) -> pd.DataFrame:
    rows = []
    for iso3 in countries:
        missing = missing_by_country.get(iso3, [])
        missing_layer_items = []
        missing_by_scenario_items = []
        for scenario in scenarios:
            expected_layers = expected_layers_by_scenario.get(scenario, set())
            country_layers = country_layers_by_scenario.get(scenario, {}).get(iso3, set())
            scenario_missing_layers = sorted(expected_layers - country_layers)
            missing_layer_items.extend(f"{scenario}:{layer}" for layer in scenario_missing_layers)
            if scenario_missing_layers:
                joined = ";".join(scenario_missing_layers)
                missing_by_scenario_items.append(f"{scenario}=[{joined}]")
        rows.append({
            "iso3": iso3,
            "status": "excluded" if missing else "kept",
            "required_layer": required_layer,
            "missing_scenarios": ";".join(missing),
            "missing_layers": ";".join(missing_layer_items),
            "missing_layers_by_scenario": " | ".join(missing_by_scenario_items),
            "files_matched_for_removal": len(deletion_plan.get(iso3, [])),
        })
    return pd.DataFrame(rows, columns=[
        "iso3", "status", "required_layer", "missing_scenarios", "missing_layers",
        "missing_layers_by_scenario", "files_matched_for_removal",
    ])


def audit_naming(output_root: Path, scenarios: List[str], fix_names: bool) -> Tuple[pd.DataFrame, pd.DataFrame]:
    folder_rows = []
    summary_rows = []
    for scenario in scenarios:
        folder = output_root / scenario
        folder_rows.append({"folder": scenario, "status": "present" if folder.is_dir() else "missing"})
        if not folder.is_dir():
            continue
        scenario_match = SCENARIO_PATTERN.fullmatch(scenario)
        if not scenario_match or scenario not in NAMING_SCENARIOS:
            folder_rows[-1]["status"] = "invalid scenario name"
            continue
        base = scenario.removesuffix("_add_v2")
        suffix = scenario_match.group("suffix") or ""
        for file in sorted(folder.glob("*.xlsx")):
            if "_supply_" not in file.name or file.name.startswith("~$"):
                continue
            match = SUMMARY_PATTERN.fullmatch(file.name)
            expected = ""
            status = "invalid format"
            if match:
                expected = f"{base}_{match.group('iso3')}{suffix}.xlsx"
                if file.name == expected:
                    status = "ok"
                elif (match.group("year") != scenario_match.group("year")
                      or match.group("suffix") != scenario_match.group("suffix")
                      or match.group("scenario") not in (None, scenario_match.group("scenario"))):
                    status = "does not match folder"
                else:
                    status = "missing scenario type"
                    if fix_names:
                        target = folder / expected
                        try:
                            # Creating the hard link fails if the destination already exists.
                            os.link(file, target)
                            file.unlink()
                            status = "renamed"
                        except OSError as exc:
                            status = f"rename failed: {exc}"
            summary_rows.append({
                "folder": scenario, "file_name": file.name,
                "expected_name": expected, "status": status,
            })

    if output_root.is_dir():
        for folder in sorted(output_root.iterdir()):
            if folder.is_dir() and "_supply_" in folder.name and folder.name not in NAMING_SCENARIOS:
                folder_rows.append({"folder": folder.name, "status": "unexpected; review manually"})
    return (
        pd.DataFrame(folder_rows, columns=["folder", "status"]),
        pd.DataFrame(summary_rows, columns=["folder", "file_name", "expected_name", "status"]),
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Audit country completeness, scenario folders, and supply summary names. "
            "Output files are not changed unless an action flag is provided."
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
            "Defaults: nine scenarios for country completeness, all 15 for naming."
        ),
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Delete incomplete-country outputs and rewrite countries file (destructive)",
    )
    parser.add_argument(
        "--audit-xlsx",
        default="process_completion_audit_results.xlsx",
        help="Path to the Excel audit report (default: process_completion_audit_results.xlsx)",
    )
    parser.add_argument(
        "--check-excel-naming",
        action="store_true",
        help="Also check Excel naming (now checked by default)",
    )
    parser.add_argument(
        "--fix-excel-names",
        action="store_true",
        help="Rename unambiguous legacy summaries without deleting country outputs",
    )
    args = parser.parse_args()

    output_root = Path(args.output_root)
    countries_file = Path(args.countries_file)
    scenarios = args.scenarios if args.scenarios else DEFAULT_SCENARIOS
    naming_scenarios = args.scenarios if args.scenarios else NAMING_SCENARIOS
    audit_path = Path(args.audit_xlsx)
    if audit_path.suffix.lower() != ".xlsx":
        parser.error("--audit-xlsx must have an .xlsx extension")
    if args.apply:
        missing_folders = [scenario for scenario in scenarios if not (output_root / scenario).is_dir()]
        if missing_folders:
            parser.error("--apply refused because scenario folders are missing: " + ", ".join(missing_folders))

    folders_audit, excel_audit = audit_naming(output_root, naming_scenarios, args.fix_excel_names)

    # Start from existing countries_list if present; otherwise infer from summary xlsx files.
    file_countries = read_country_list(countries_file)
    discovered_union: Set[str] = set()

    for scenario in scenarios:
        scenario_dir = output_root / scenario
        discovered = discover_summary_countries(scenario_dir, scenario)
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
        print("[WARN] No countries found in the country list, scenario summaries, or master universe.")
        if args.apply:
            parser.error("--apply refused because no countries were found")

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

    country_audit = build_country_audit(
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
    print("\nFolder naming results:")
    print(folders_audit.to_string(index=False))
    print("\nSupply summary naming results:")
    if excel_audit.empty:
        print("  No supply summary files found.")
    else:
        for status, count in excel_audit["status"].value_counts().items():
            print(f"  {status}: {count}")
        issues = excel_audit[~excel_audit["status"].isin(["ok", "renamed"])]
        if not issues.empty:
            print(issues.to_string(index=False))

    audit_path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(audit_path, engine="openpyxl") as writer:
        country_audit.to_excel(writer, sheet_name="Countries", index=False)
        folders_audit.to_excel(writer, sheet_name="Folders", index=False)
        excel_audit.to_excel(writer, sheet_name="Supply summaries", index=False)
    print(f"\nAudit workbook written: {audit_path}")

    if any(str(status).startswith("rename failed:") for status in excel_audit["status"]):
        print("[ERROR] Some summary renames failed; country cleanup was not applied.")
        return 1

    if args.apply:
        deleted_count = 0
        for files in deletion_plan.values():
            for file_path in files:
                file_path.unlink()
                deleted_count += 1
        write_country_list(countries_file, valid)
        print(f"Deleted {deleted_count} files and updated {countries_file}.")
    else:
        print("Country outputs and country list not changed.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
