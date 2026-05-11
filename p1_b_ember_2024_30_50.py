# 6/30/2025 at work

# ==================================================================================================
# SCRIPT PURPOSE: Future Energy Projections (2030 & 2050)
# ==================================================================================================
#
# WHAT THIS CODE DOES:
# This script projects future electricity generation and capacity for 2030 and 2050 for all countries.
# It integrates data from multiple sources to build a comprehensive scenario based on:
# 1. UN Population Projections: To estimate future energy demand growth.
# 2. Ember & GEM Data (from p1_a): Baseline 2024 capacity and generation.
# 3. National Determined Contributions (NDCs): Official country targets for 2030.
# 4. IEA World Energy Outlook: Growth rates and pledges for 2050 and for countries without specific NDCs.
#
# KEY STEPS:
# 1. Calculate population growth factors (2024->2030->2050).
# 2. Merge with baseline energy data.
# 3. Process 2030 renewable energy targets (NDCs), disaggregating general targets into specific technologies
#    (Solar, Wind, Hydro, etc.) based on current (2024) proportions.
# 4. Project Fossil and Nuclear generation using IEA pledge growth rates.
# 5. Fill gaps for countries without NDCs using regional benchmarks (MWh per capita).
# 6. Extrapolate to 2050 using IEA Net Zero/Pledge growth rates.
# 7. Enforce "Minimum Rule": Future generation shouldn't be less than current/previous periods unless specified.
#
# OUTPUT:
# A comprehensive dataset (Excel) containing 2024 baseline, 2030 targets, and 2050 projections
# for capacity and generation across all major fuel types.
# ==================================================================================================

import pandas as pd
import itertools

# Task 1
# Load the first dataset 
# UN Population data is used to calculate growth factors, which serve as a proxy for energy demand growth
file_path1 = "data_pop_un/WPP2024_TotalPopulationBySex2025-11-23.csv" # updated November 23, 2025 
# https://population.un.org/wpp/downloads?folder=Standard%20Projections&group=CSV%20format
# Standard Projections -> CSV format --> Population -> 1950-2011, all scenarios (gz)

df1 = pd.read_csv(file_path1, low_memory=False)

# Filter the first dataset for "Medium" variant and years 2024, 2030, and 2050
filtered_df1 = df1[(df1["Variant"] == "Medium") & (df1["Time"].isin([2024, 2030, 2050]))]

# Select required columns from the first dataset
selected_columns1 = ["ISO3_code", "Time", "PopTotal"]
filtered_df1 = filtered_df1[selected_columns1]

# Remove rows with missing ISO codes from the first dataset
filtered_df1 = filtered_df1.dropna(subset="ISO3_code")

# Pivot the first dataset to have years as columns
pivot_df1 = filtered_df1.pivot(index="ISO3_code", columns="Time", values="PopTotal").reset_index()

# Rename columns for clarity in the first dataset
pivot_df1.columns = ["ISO3_code", "PopTotal_2024", "PopTotal_2030", "PopTotal_2050"]

# Calculate the population growth factor from 2024 to 2030 and 2050
# These factors are used to scale energy demand for countries without specific targets
pivot_df1["Growth_Factor_2024_2030"] = pivot_df1["PopTotal_2030"] / pivot_df1["PopTotal_2024"]
pivot_df1["Growth_Factor_2024_2050"] = pivot_df1["PopTotal_2050"] / pivot_df1["PopTotal_2024"]


### Task 2
# Load the processed data from a_ember_gem_2024.py (# 2nd dataset)
# This provides the harmonized baseline (2024) capacity and generation data
p1_a_output_path = r"outputs_processed_data\p1_a_ember_gem_2024.xlsx"
grouped_df = pd.read_excel(p1_a_output_path, sheet_name="Grouped_cur")

# Extract relevant columns for merging
granular_columns = (
    ["Country Name", "Country Code"] +
    [col for col in grouped_df.columns if "_Larger_MW" in col] +
    [col for col in grouped_df.columns if "_Potential_MWh" in col]
)

grouped_df = grouped_df[granular_columns]

# Rename columns for clarity
grouped_df.rename(columns={"Country Code": "ISO3_code"}, inplace=True)
grouped_df.rename(columns={"Total_Potential_MWh": "Total_MWh_2024"}, inplace=True)

# Rename columns to follow the {type}_2024 naming convention
grouped_df.rename(columns={col: col.replace("_Larger_MW", "_2024") for col in grouped_df.columns if "_Larger_MW" in col}, inplace=True)
grouped_df.rename(columns={col: col.replace("_Potential_MWh", "_2024_MWh") for col in grouped_df.columns if "_Potential_MWh" in col}, inplace=True)


# Merge the two datasets by "ISO3_code"
merged_df = pd.merge(pivot_df1, grouped_df, on="ISO3_code", how="inner")

# Print the number of unique country codes in merged_df
print("Number of unique country codes in merged_df:", merged_df["ISO3_code"].nunique())

### Task 3
# Load the third dataset
# Ember targets dataset contains country-specific renewable energy targets (NDCs)
file_path3 = "data_energy_ember/targets_download2025-11-23.xlsx" # updated November 23, 2025
# Source: https://ember-energy.org/data/global-renewable-power-sector-targets-2030/
df3_original = pd.read_excel(file_path3, sheet_name="capacity_target_wide")

# Duplicate the sheet to create "capacity_target_wide_2"
df3 = df3_original.copy()

# Clean up the data in "capacity_target_wide_2"
# 1. Add "Offshore Wind" and "Onshore Wind" values to the existing "Wind" column
df3["Wind"] = df3.get("Wind", 0).fillna(0) + df3.get("Offshore Wind", 0).fillna(0) + df3.get("Onshore Wind", 0).fillna(0)

# 2. Merge "Bioenergy" and "Other Renewables" into "Other Renewables"
df3["Other Renewables"] = df3.get("Bioenergy", 0).fillna(0) + df3.get("Other Renewables", 0).fillna(0)

# 3. Drop "Offshore Wind", "Onshore Wind", and "Bioenergy" columns
df3.drop(columns=["Offshore Wind", "Onshore Wind", "Bioenergy"], inplace=True)

df3 = df3[df3["country_code"] != "EU"]

# Create a new column "Category" based on the specified conditions
# Categories define the granularity of the available target data (e.g., some countries only target "Renewables", others specific "Wind" or "Solar")
def determine_category(row):
    if row.get("Renewables", 0) > 0:
        return 5
    elif row.get("Hydro, bio and other renewables", 0) > 0:
        return 3
    elif row.get("Rest of renewables", 0) > 0 and row.get("Hybdro", 0) > 0:
        return 1
    elif row.get("Rest of renewables", 0) > 0 and row.get("Wind", 0) > 0:
        return 4
    else:
        return 2

df3["Category"] = df3.apply(determine_category, axis=1)

# Print the number of unique country codes in df3
print("Number of unique country codes in df3:", df3["country_code"].nunique())

# Define a function to disaggregate based on 2024 data: only for 80+ countries with 2030 NDCs targets 
# If a country has a broad target (e.g., "Renewables"), we break it down into Solar/Wind/Hydro
# based on their current (2024) share of the renewable mix.
def disaggregate(row, merged_df, categories, column_name):
    """
    Disaggregate the values in the specified column into the given categories based on proportions derived from 2024 data.

    Args:
        row (pd.Series): A row from the dataframe containing the data to be disaggregated.
        merged_df (pd.DataFrame): The dataframe containing the reference data for proportions.
        categories (list): List of category names to disaggregate into.
        column_name (str): The name of the column to disaggregate.

    Returns:
        dict: A dictionary with the disaggregated values for each category.
    """
    # Ensure required columns exist in merged_df
    missing_columns = [f"{cat}_2024" for cat in categories if f"{cat}_2024" not in merged_df.columns]
    if missing_columns:
        print(f"Missing columns in merged_df: {missing_columns}")

    # Extract the country code from the row
    country_code = row["country_code"]

    # Filter merged_df for the specific country code
    filtered_merged_df = merged_df[merged_df["ISO3_code"] == country_code]

    # Check if the country code exists in merged_df
    if filtered_merged_df.empty:
        print(f"Country code {country_code} not found in merged_df")
        return {cat: 0 for cat in categories}

    # Calculate the total 2024 Ember capacity for the categories
    total_2024 = filtered_merged_df[[f"{cat}_2024" for cat in categories]].fillna(0).sum(axis=1).values[0]

    # Handle cases where total_2024 is zero
    if total_2024 == 0:
        print(f"Total 2024 for country code {country_code} is 0")
        return {cat: 0 for cat in categories}

    # Calculate proportions for each category based on 2024 Ember capacity data
    proportions = {
        cat: filtered_merged_df[f"{cat}_2024"].fillna(0).values[0] / total_2024
        for cat in categories
    }

    # Disaggregate the values in the specified column based on the calculated proportions
    return {cat: row[column_name] * proportions[cat] for cat in categories}


# Disaggregate [Rest of renewables] for Category 1
df3.loc[df3["Category"] == 1, ["Solar", "Wind", "Other Renewables"]] = df3[df3["Category"] == 1].apply(
    lambda row: pd.Series(disaggregate(row, merged_df, ["Solar", "Wind", "Other Renewables"], "Rest of renewables")), axis=1
) 

# Disaggregate [Rest of renewables] for Category 4
df3.loc[df3["Category"] == 4, ["Solar", "Hydro", "Other Renewables"]] = df3[df3["Category"] == 4].apply(
    lambda row: pd.Series(disaggregate(row, merged_df, ["Solar", "Hydro", "Other Renewables"], "Rest of renewables")), axis=1
) 

# Disaggregate [Hydro, bio and other renewables] for Category 3
df3.loc[df3["Category"] == 3, ["Hydro", "Other Renewables"]] = df3[df3["Category"] == 3].apply(
    lambda row: pd.Series(disaggregate(row, merged_df, ["Hydro", "Other Renewables"], "Hydro, bio and other renewables")), axis=1
) 

# Disaggregate [Renewables] for Category 5
df3.loc[df3["Category"] == 5, ["Solar", "Wind", "Hydro", "Other Renewables"]] = df3[df3["Category"] == 5].apply(
    lambda row: pd.Series(disaggregate(row, merged_df, ["Solar", "Wind", "Hydro", "Other Renewables"], "Renewables")), axis=1
) 
# Drop unnecessary columns
df3.drop(columns=["Rest of renewables", "Hydro, bio and other renewables", "Renewables"], inplace=True)

# Multiply 2030 target columns by 1000 to convert from GW to MW (if needed)
for col in ["res_capacity_target", "Hydro", "Other Renewables", "Solar", "Wind"]:
    if col in df3.columns:
        df3[col] = df3[col] * 1000

# Rename columns for 2030 targets
df3.rename(columns={
    "Hydro": "Hydro_2030",
    "Other Renewables": "Other Renewables_2030",
    "Solar": "Solar_2030",
    "Wind": "Wind_2030"
}, inplace=True)


# Merge the third dataset with the previously merged dataset, pulling only the needed columns from df3
columns_to_pull = [
    "country_code", "res_capacity_target", "res_share_target", 
    "Hydro_2030", "Solar_2030", "Wind_2030", "Other Renewables_2030", "Category"
]

# Preserve the original NDC % before we later backfill missing res_share_target
# values with regional benchmarks. Downstream (Task 7) we check whether
# res_share_target_org is non-null to decide if a country had its own target or
# if we should rely on benchmark-derived per-capita values.
df3["res_share_target_org"] = df3["res_share_target"]
columns_to_pull.append("res_share_target_org")

final_merged_df = pd.merge(
    merged_df,
    df3[columns_to_pull],
    left_on="ISO3_code",
    right_on="country_code",
    how="left"
)

# Safeguard: For countries with valid NDC categories, backfill blank 2030
# technology targets using their own 2024 values. Countries without a category
# keep NaNs so they can be handled later via regional benchmarks.
columns_to_fill = ["Hydro_2030", "Solar_2030", "Wind_2030"]
for col in columns_to_fill:
    corresponding_2024_col = col.replace("_2030", "_2024")
    if corresponding_2024_col in final_merged_df.columns:
        mask = (final_merged_df[col].isnull()) & (final_merged_df["Category"].notnull())
        final_merged_df.loc[mask, col] = final_merged_df.loc[mask, corresponding_2024_col]


# Task 4 - Calculate Fossil and Nuclear generation (MWh) for 2030 and 2050 based on IEA Stated Policies CAAGRs
# Projections for non-renewable sources are based on global/regional growth rates from IEA scenarios.

# check: data_energy_projections_iea\WEO2025_Free_Dataset.xlsx (Sheet name: "World Electricity"), updated on November 23, 2025
# Source: https://www.iea.org/data-and-statistics/data-product/world-energy-outlook-2025-free-dataset
iea_pledge_caagr_2050 = {"Fossil": -1.6 * 0.01, "Nuclear": 2.6 * 0.01}  # 2024-2050 Compound Annual Growth Rate for renewable energy generation
# Calculate Fossil_2030_MWh and Nuclear_2030_MWh based on the growth rates (IEA Pledges)
final_merged_df["Fossil_2030_MWh"] = final_merged_df["Fossil_2024_MWh"] * (1 + iea_pledge_caagr_2050["Fossil"]) ** 6  # 6 years growth from 2024 to 2030
final_merged_df["Nuclear_2030_MWh"] = final_merged_df["Nuclear_2024_MWh"] * (1 + iea_pledge_caagr_2050["Nuclear"]) ** 6  # 6 years growth from 2024 to 2030

# Calculate Fossil_2050_MWh and Nuclear_2050_MWh based on the growth rates (IEA Pledges)
final_merged_df["Fossil_2050_MWh"] = final_merged_df["Fossil_2024_MWh"] * (1 + iea_pledge_caagr_2050["Fossil"]) ** 26  # 26 years growth from 2024 to 2050
final_merged_df["Nuclear_2050_MWh"] = final_merged_df["Nuclear_2024_MWh"] * (1 + iea_pledge_caagr_2050["Nuclear"]) ** 26  # 26 years growth from 2024 to 2050


# Task 5: Calculate renewable generation (MWh) for 2030 based on the capacity conversion factors
# We convert the targeted Capacity (MW) to Generation (MWh) using the conversion rates derived in script p1_a.
# Load the capacity conversion factors
capacity_conversion_factors_df = pd.read_excel(p1_a_output_path, sheet_name="Grouped_cur")

# Calculate global mean conversion rates for each type
global_mean_conv_rates = capacity_conversion_factors_df[
    ["Hydro_ConvRate", "Solar_ConvRate", "Wind_ConvRate", "Other Renewables_ConvRate"]
].mean()

# Fill NA or 0 with global mean
def fill_missing_or_zero(row):
    return row.mask((row.isna()) | (row == 0), global_mean_conv_rates)

# Apply the replacement
capacity_conversion_factors = (
    capacity_conversion_factors_df
    .set_index("Country Code")[["Hydro_ConvRate", "Solar_ConvRate", "Wind_ConvRate", "Other Renewables_ConvRate"]]
    .apply(fill_missing_or_zero, axis=1)
    .to_dict(orient="index")
)

# Calculate MWh for 2030 based on the conversion factors (This is for countries with valid NDCs)
for index, row in final_merged_df.iterrows():
    country_code = row["ISO3_code"]
    if country_code in capacity_conversion_factors:
        conversion_factors = capacity_conversion_factors[country_code]
        
        # Calculate MWh for each energy type
        hydro_mwh = row["Hydro_2030"] * conversion_factors["Hydro_ConvRate"]
        solar_mwh = row["Solar_2030"] * conversion_factors["Solar_ConvRate"]
        wind_mwh = row["Wind_2030"] * conversion_factors["Wind_ConvRate"]
        other_renewables_mwh = row["Other Renewables_2030"] * conversion_factors["Other Renewables_ConvRate"]
        
        # Assign individual MWh values to the dataframe
        final_merged_df.at[index, "Hydro_2030_MWh"] = hydro_mwh
        final_merged_df.at[index, "Solar_2030_MWh"] = solar_mwh
        final_merged_df.at[index, "Wind_2030_MWh"] = wind_mwh
        final_merged_df.at[index, "Other Renewables_2030_MWh"] = other_renewables_mwh
        
# Compute RE generation MWh per capita for 2030
final_merged_df["Hydro_2030_MWh_per_capita"] = final_merged_df["Hydro_2030_MWh"] / final_merged_df["PopTotal_2030"]
final_merged_df["Solar_2030_MWh_per_capita"] = final_merged_df["Solar_2030_MWh"] / final_merged_df["PopTotal_2030"]
final_merged_df["Wind_2030_MWh_per_capita"] = final_merged_df["Wind_2030_MWh"] / final_merged_df["PopTotal_2030"]
final_merged_df["Other Renewables_2030_MWh_per_capita"] = final_merged_df["Other Renewables_2030_MWh"] / final_merged_df["PopTotal_2030"]


### Task 6 - Extrapolate the 2030 renewable targets MWh for countries without NDCs using regional benchmarks (i.e. MWh per capita)
# For countries that didn't provide specific targets, we assume they will follow the average path of their Region and Income Group.
# Step 6.1. First calculate MWh per capita for 2030
# Load/add WB Country Class (https://datahelpdesk.worldbank.org/knowledgebase/articles/906519-world-bank-country-and-lending-groups)
# Download the current classification by income in XLSX format.
wb_country_class_path = "data_country_class_wb/CLASS_2025_10_07.xlsx" # updated on November 23, 2025
wb_country_class_df = pd.read_excel(wb_country_class_path, sheet_name="List of economies")

# Merge the WB Country Class data with the final merged dataset and drop the "Code" column in one step
final_merged_df = pd.merge(
    final_merged_df,
    wb_country_class_df[["Code", "Region", "Income group"]],
    left_on="ISO3_code",
    right_on="Code",
    how="left"
).drop(columns=["Code"])

# Drop rows with missing values in Region or Income group
final_merged_df.dropna(subset=["Region", "Income group"], inplace=True)

# Fill 2030 Capacity Percentage targets for countries without values in the "res_share_target" column

# Calculate regional and income group benchmarks for "res_share_target"
res_share_benchmarks = (
    final_merged_df[final_merged_df["res_share_target"].notnull()]
    .groupby(["Region", "Income group"])["res_share_target"]
    .mean()
    .reset_index()
)

# Compute global mean for "res_share_target"
global_res_share_mean = final_merged_df["res_share_target"].mean()

# Merge regional benchmarks back into the dataset
final_merged_df = pd.merge(
    final_merged_df,
    res_share_benchmarks,
    on=["Region", "Income group"],
    how="left",
    suffixes=("", "_R_Benchmark")
)

# Fill missing "res_share_target" values
final_merged_df["res_share_target"] = final_merged_df["res_share_target"].fillna(
    final_merged_df["res_share_target_R_Benchmark"]
).fillna(global_res_share_mean)

# Calculate and fill MWh per capita for 2030
# Define the energy types to be used for regional benchmarks
energy_types = ["Hydro_2030_MWh_per_capita", "Solar_2030_MWh_per_capita", "Wind_2030_MWh_per_capita", "Other Renewables_2030_MWh_per_capita"]

# Filter the dataset to include only countries with valid NDCs (non-null "Category")
# Filter only countries with valid NDCs
valid_ndc_df = final_merged_df[final_merged_df["Category"].notnull()]

# Melt the dataframe for per capita calculations
melted_df = valid_ndc_df.melt(
    id_vars=["Region", "Income group"],
    value_vars=energy_types,
    var_name="Energy_Type",
    value_name="MWh_Per_Capita"
)
# Calculate regional benchmarks (mean per capita by Region, Income group, and Energy Type)
regional_benchmarks = (
    melted_df
    .groupby(["Region", "Income group", "Energy_Type"])["MWh_Per_Capita"]
    .mean()
    .reset_index()
)
# Create a full index of all combinations
regions = valid_ndc_df["Region"].dropna().unique()
income_groups = valid_ndc_df["Income group"].dropna().unique()
energy_types_unique = melted_df["Energy_Type"].dropna().unique()

full_index = pd.DataFrame(
    list(itertools.product(regions, income_groups, energy_types_unique)),
    columns=["Region", "Income group", "Energy_Type"]
)

# Merge full index with actual data
regional_benchmarks = full_index.merge(
    regional_benchmarks,
    on=["Region", "Income group", "Energy_Type"],
    how="left"
)

# Compute global means by Income group and Energy Type
global_means_by_income = (
    valid_ndc_df
    .melt(
        id_vars=["Income group"],
        value_vars=energy_types,
        var_name="Energy_Type",
        value_name="MWh_Per_Capita"
    )
    .groupby(["Income group", "Energy_Type"])["MWh_Per_Capita"]
    .mean()
    .to_dict()
)

# Fill missing or zero values with global means
regional_benchmarks["MWh_Per_Capita"] = regional_benchmarks.apply(
    lambda row: global_means_by_income.get((row["Income group"], row["Energy_Type"]), 0)
    if pd.isna(row["MWh_Per_Capita"]) or row["MWh_Per_Capita"] == 0 else row["MWh_Per_Capita"],
    axis=1
)

# Pivot the table to wide format
regional_benchmarks_pivot = regional_benchmarks.pivot_table(
    index=["Region", "Income group"],
    columns="Energy_Type",
    values="MWh_Per_Capita",
    aggfunc="mean"
).reset_index()

# Rename columns to indicate these are regional benchmarks
regional_benchmarks_pivot.columns = [
    "Region", "Income group"
] + [f"{col}_R_Benchmark" for col in regional_benchmarks_pivot.columns[2:]]

# Merge the region/income lookup so every non-NDC country can borrow the
# average per-capita values computed above.
final_merged_df = pd.merge(
    final_merged_df,
    regional_benchmarks_pivot,
    on=["Region", "Income group"],
    how="left"
)

# For each per-capita metric, drop in the regional benchmark only where a
# country lacked its own 2030 NDC Category (i.e., it needs a benchmark fill).
for energy_type in energy_types:
    benchmark_col = energy_type + "_R_Benchmark"
    final_merged_df.loc[final_merged_df["Category"].isnull(), energy_type] = (
        final_merged_df.loc[final_merged_df["Category"].isnull(), benchmark_col]
    ).fillna(0)  # Fill any missing values with 0

# 6.2. Fill in missing generation data (MWh) for 2030 using regional benchmarks (per capita MWh):
# Calculate missing generation for Hydro_2030, Solar_2030, Wind_2030, and Other Renewables_2030 for countries without valid NDCs
# This is done by multiplying the regional benchmark per capita (MWh per capita) * the 2030 population 

energy_types2 = [
    "Hydro_2030_MWh", "Solar_2030_MWh",
    "Wind_2030_MWh", "Other Renewables_2030_MWh"
]

# Reuse the same ordering for 2050 columns without maintaining a second manual list.
energy_types_2050 = [col.replace("_2030_MWh", "_2050_MWh") for col in energy_types2]

for energy_type2 in energy_types2:
    # Get the corresponding regional benchmark column for MWh per capita
    benchmark_col = energy_type2 + "_per_capita_R_Benchmark"
    # Calculate the missing generation for countries without valid NDCs
    final_merged_df.loc[final_merged_df['Category'].isnull(), energy_type2] = (
        final_merged_df.loc[final_merged_df['Category'].isnull(), benchmark_col]
        * final_merged_df.loc[final_merged_df['Category'].isnull(), "PopTotal_2030"]
    ).fillna(0)  # Fill any remaining missing values with 0

# 6.3. Adjust the renewable generation of each type for 2030 proportionally to meet the following formula - i.e., Minimum rule!:
# post-calculation total renewable generations / post-calculation total generation = res_share_target / 100
# Adjust the generation to match the res_share_target (i.e. renewable generation share target %) - Based on NDCs extrapolation for 2030
# Note: Ensure renewable generation values are at least equal to 2024 values for ALL countries,
# then apply regional benchmarks if needed, and finally adjust proportions to meet the target renewable share (res_share_target).

for index, row in final_merged_df.iterrows():
    fossil = row.get("Fossil_2030_MWh", 0)
    nuclear = row.get("Nuclear_2030_MWh", 0)
    target_share = row.get("res_share_target", 0) / 100 if pd.notnull(row.get("res_share_target", 0)) else 0
    renewables_2024 = {energy_type: row.get(energy_type.replace("_2030_MWh", "_2024_MWh"), 0) for energy_type in energy_types2}
    renewables_2030 = {energy_type: row.get(energy_type, 0) for energy_type in energy_types2}
    renewables_sum = sum(renewables_2030.values())
    total_gen_2030 = renewables_sum + fossil + nuclear
    
    # ALWAYS enforce minimum rule first: 2030 >= 2024 for each energy type (applies to ALL countries)
    renewables_2030 = {k: max(renewables_2030[k], renewables_2024[k]) for k in energy_types2}
    renewables_sum = sum(renewables_2030.values())
    
    if total_gen_2030 > 0 and target_share > 0 and renewables_sum > 0:
        if fossil + nuclear == 0 or target_share >= 0.99:
            desired_renewable_total = renewables_sum
        else:
            desired_renewable_total = target_share * (fossil + nuclear) / (1 - target_share)
        
        # Initial scaling
        scaling_factor = desired_renewable_total / renewables_sum
        scaled = {k: v * scaling_factor for k, v in renewables_2030.items()}
        
        # Enforce minimum = 2024 value
        fixed = {k: max(scaled[k], renewables_2024[k]) for k in energy_types2}
        
        # If sum is now above target, re-scale only those not fixed at 2024 value
        fixed_sum = sum(v for k, v in fixed.items() if v == renewables_2024[k])
        to_scale = [k for k in energy_types2 if fixed[k] > renewables_2024[k]]
        if to_scale:
            remaining = desired_renewable_total - fixed_sum
            if remaining < 0:
                # If fixed_sum already exceeds target, set all to 2024 value
                for k in energy_types2:
                    final_merged_df.at[index, k] = renewables_2024[k]
            else:
                scale_sum = sum(fixed[k] for k in to_scale)
                if scale_sum > 0:
                    rescale_factor = remaining / scale_sum
                    for k in to_scale:
                        final_merged_df.at[index, k] = fixed[k] * rescale_factor
                    for k in energy_types2:
                        if fixed[k] == renewables_2024[k]:
                            final_merged_df.at[index, k] = renewables_2024[k]
        else:
            for k in energy_types2:
                final_merged_df.at[index, k] = fixed[k]
    else:
        # If not enough data for share-based adjustment, still enforce minimum rule
        for k in energy_types2:
            final_merged_df.at[index, k] = max(renewables_2030[k], renewables_2024[k])

### Task 7 - extrapolate the 2030 renewable targets for 2050 (for both countries with and without valid NDCs)
# 7.1: Bring MWh per capita for 2030 for each renewable type from above code, and calculate the 2050 MWh per capita values based on the CAAGR from 2024 to 2050
# (IEA Stated Policies, from data_energy_projections_iea\WEO2025_Free_Dataset.xlsx (Sheet name: "World Electricity")) 
# 7.2: Calculate the 2050 MWh targets (MWh) for each renewable type, by multiplying Population 2050 by the 2050 MWh per capita values.
# Please note that countries with valid NDCs will use the 2030 MWh per capita values as a base, while countries without valid NDCs will use the regional benchmarks for 2030 MWh per capita values.
# 7.3: Ensure the 2050 values are at least equal to the 2030 values (i.e., the minimum rule!), and adjust proportions to meet the target renewable share (IEA 2050 Pledges targets).

# IEA pledge targets for total 2050 renewable shares by income group 
iea_pledge_re_targets_2050 = {
    "High income": 69.0539398840614 / 100, # 69.05% target for all income groups 
    "Upper middle income": 69.0539398840614 / 100, 
    "Lower middle income": 69.0539398840614 / 100,  
    "Low income": 69.0539398840614 / 100  
}

# IEA pledge CAAGR for renewable energy types from 2024 to 2050
iea_pledge_caagr_re_2050 = {
    "Solar_2030_MWh": 9.18826975357672 / 100,  # 9.19% annual growth
    "Wind_2030_MWh": 5.8067160397028 / 100,  # 5.81% annual growth
    "Hydro_2030_MWh": 1.39924712503472 / 100,  # 1.9% annual growth
    "Other Renewables_2030_MWh": (
    ((1574.7+245.18+452.24+86.15) / (760.12+19.59+100.39+0.93)) ** (1 / (2050 - 2024)) - 1
    ), # Average CAAGR for Other Renewables # 3.80% annual growth
    # Bioenergy 760.12 in 2024 to 1574.7 in 2050; 
    # CSP 19.59 in 2024 to 245.18 in 2050; 
    # Geothermal 100.39 in 2024 to 452.24 in 2050;
    # Marine 0.93 in 2024 to 86.15 in 2050
}

# 7.1 / 7.2: Calculate 2050 MWh per capita and MWh targets
# Use the ACTUAL (post-adjustment) 2030 MWh values as the base for all countries.
# Previously, non-NDC countries used the pre-adjustment regional benchmark per-capita,
# which ignored the scaling applied in Task 6.3 and produced inflated 2050 values.
for energy_type in energy_types2:
    caagr = iea_pledge_caagr_re_2050.get(energy_type, 0)  # Default to 0% if not found
    per_capita_2050_col = energy_type.replace("_2030_MWh", "_2050_MWh") + "_per_capita"
    energy_type_2050 = energy_type.replace("_2030_MWh", "_2050_MWh")
    
    # Derive per-capita from the actual (adjusted) 2030 MWh values, then grow by CAAGR
    final_merged_df[per_capita_2050_col] = (
        (final_merged_df[energy_type] / final_merged_df["PopTotal_2030"])
        * (1 + caagr) ** 20  # 20 years from 2030 to 2050
    )
    
    # Calculate 2050 MWh = 2050 per capita * 2050 population
    final_merged_df[energy_type_2050] = (
        final_merged_df[per_capita_2050_col] * final_merged_df["PopTotal_2050"]
    )


# 7.3: Adjust renewable generation for 2050 to meet IEA target shares and enforce minimum rule
def new_func(ndc_target_share, iea_2050_target_share):
    target_share_2050 = max(ndc_target_share, iea_2050_target_share)
    return target_share_2050

for index, row in final_merged_df.iterrows():
    income_group = row.get("Income group", None)
    # Determine the target share for 2050
    # We take the higher of the country's own 2030 NDC target or the IEA Stated Policies target (earlier it was global pledge target so code variable names not consistent)
    ndc_target_share = row.get("res_share_target", 0) / 100 if pd.notnull(row.get("res_share_target")) else 0
    iea_2050_target_share = iea_pledge_re_targets_2050.get(income_group, 69.0539398840614 / 100)  # Default to 69.05% if not found
    
    # Use the greater of the NDC target or the IEA pledge target
    target_share_2050 = new_func(ndc_target_share, iea_2050_target_share)
    final_merged_df.at[index, "Target_Share_2050"] = target_share_2050

    # Use appropriate fossil and nuclear values for 2050
    fossil = row.get("Fossil_2050_MWh", 0)
    nuclear = row.get("Nuclear_2050_MWh", 0)
    
    # Get renewable values for 2030 and 2050
    renewables_2030 = {energy_type: row.get(energy_type.replace("_2050_MWh", "_2030_MWh"), 0) for energy_type in energy_types_2050}
    renewables_2050 = {energy_type: row.get(energy_type, 0) for energy_type in energy_types_2050}
    
    renewables_sum = sum(renewables_2050.values())
    total_gen_2050 = renewables_sum + fossil + nuclear
    
    # Calculate 2030 total generation for minimum rule check
    fossil_2030 = row.get("Fossil_2030_MWh", 0)
    nuclear_2030 = row.get("Nuclear_2030_MWh", 0)
    renewables_2030_sum = sum(renewables_2030.values())
    total_gen_2030 = renewables_2030_sum + fossil_2030 + nuclear_2030
    
    # ALWAYS enforce individual energy type minimum rule first (2050 >= 2030 for each type)
    renewables_2050 = {k: max(renewables_2050[k], renewables_2030[k]) for k in energy_types_2050}
    renewables_sum = sum(renewables_2050.values())
    total_gen_2050 = renewables_sum + fossil + nuclear
    
    # First, apply renewable share target scaling (if needed)
    if total_gen_2050 > 0 and target_share_2050 > 0 and renewables_sum > 0:
        if fossil + nuclear == 0 or target_share_2050 >= 0.99:
            desired_renewable_total = renewables_sum
        else:
            desired_renewable_total = target_share_2050 * (fossil + nuclear) / (1 - target_share_2050)
        
        # Initial scaling for renewable share targets
        scaling_factor = desired_renewable_total / renewables_sum
        scaled = {k: v * scaling_factor for k, v in renewables_2050.items()}
        
        # Apply individual energy type minimum rule (2050 >= 2030 for each type)
        renewables_2050 = {k: max(scaled[k], renewables_2030[k]) for k in energy_types_2050}
        
        # If sum is now above target, re-scale only those not fixed at 2030 value
        fixed_sum = sum(v for k, v in renewables_2050.items() if v == renewables_2030[k])
        to_scale = [k for k in energy_types_2050 if renewables_2050[k] > renewables_2030[k]]
        
        if to_scale:
            remaining = desired_renewable_total - fixed_sum
            if remaining < 0:
                # If fixed_sum already exceeds target, set all to 2030 value
                renewables_2050 = {k: renewables_2030[k] for k in energy_types_2050}
            else:
                scale_sum = sum(renewables_2050[k] for k in to_scale)
                if scale_sum > 0:
                    rescale_factor = remaining / scale_sum
                    for k in energy_types_2050:
                        if renewables_2050[k] == renewables_2030[k]:
                            # Keep at 2030 level (already at minimum)
                            pass
                        else:
                            # Scale down proportionally
                            renewables_2050[k] = renewables_2050[k] * rescale_factor
    
    # Update renewables_sum after individual energy type minimum rules
    renewables_sum = sum(renewables_2050.values())
    total_gen_2050 = renewables_sum + fossil + nuclear
    
    # Then, apply total generation minimum rule (after individual type minimums)
    if total_gen_2050 < total_gen_2030 and total_gen_2030 > 0:
        # Scale up renewables proportionally to meet minimum total generation
        # This applies AFTER individual energy type minimums are enforced
        # WHY: We assume total electricity generation will not decrease between 2030 and 2050.
        
        if renewables_sum > 0:
            # Calculate required increase in total generation
            required_total_increase = total_gen_2030 - total_gen_2050
            
            # Scale up renewables proportionally to make up the difference
            renewable_scaling_factor = (renewables_sum + required_total_increase) / renewables_sum
            
            # Apply reasonable bounds to scaling factor to prevent extreme values
            renewable_scaling_factor = min(renewable_scaling_factor, 5.0)  # Cap at 5x increase
            
            # Scale all renewable types proportionally (preserving their relative proportions)
            renewables_2050 = {k: v * renewable_scaling_factor for k, v in renewables_2050.items()}
        else:
            # If renewables are zero, we can't scale them up
            # In this rare case, keep values at 2030 minimum
            renewables_2050 = renewables_2030
    
    # Apply the final renewable values to the dataframe
    for k in energy_types_2050:
        final_merged_df.at[index, k] = renewables_2050[k]


# Add after Task 7 to check if targets are being met for both 2030 and 2050
print("\n=== VERIFICATION: Renewable Share Targets for 2030 and 2050 ===")
verification_results = []

for index, row in final_merged_df.iterrows():
    # Extract values for 2030
    fossil_2030 = row.get("Fossil_2030_MWh", 0)
    nuclear_2030 = row.get("Nuclear_2030_MWh", 0)
    hydro_2030 = row.get("Hydro_2030_MWh", 0)
    solar_2030 = row.get("Solar_2030_MWh", 0)
    wind_2030 = row.get("Wind_2030_MWh", 0)
    other_2030 = row.get("Other Renewables_2030_MWh", 0)
    
    total_renewables_2030 = hydro_2030 + solar_2030 + wind_2030 + other_2030
    total_generation_2030 = total_renewables_2030 + fossil_2030 + nuclear_2030
    
    # Extract values for 2050
    fossil_2050 = row.get("Fossil_2050_MWh", 0)
    nuclear_2050 = row.get("Nuclear_2050_MWh", 0)
    hydro_2050 = row.get("Hydro_2050_MWh", 0)
    solar_2050 = row.get("Solar_2050_MWh", 0)
    wind_2050 = row.get("Wind_2050_MWh", 0)
    other_2050 = row.get("Other Renewables_2050_MWh", 0)
    
    total_renewables_2050 = hydro_2050 + solar_2050 + wind_2050 + other_2050
    total_generation_2050 = total_renewables_2050 + fossil_2050 + nuclear_2050
    
    # Calculate actual shares and targets
    actual_share_2030 = total_renewables_2030 / total_generation_2030 if total_generation_2030 > 0 else 0
    actual_share_2050 = total_renewables_2050 / total_generation_2050 if total_generation_2050 > 0 else 0
    target_share_2050 = iea_pledge_re_targets_2050.get(row.get("Income group"), 0.8306)
    target_share_2030 = row.get("res_share_target", 0) / 100 if pd.notnull(row.get("res_share_target")) else 0
    
    # Check if minimum rule applied
    min_rule_applied_2030 = (
        hydro_2030 == row.get("Hydro_2024_MWh", 0) or 
        solar_2030 == row.get("Solar_2024_MWh", 0) or
        wind_2030 == row.get("Wind_2024_MWh", 0) or
        other_2030 == row.get("Other Renewables_2024_MWh", 0)
    )
    min_rule_applied_2050 = (
        hydro_2050 == row.get("Hydro_2030_MWh", 0) or 
        solar_2050 == row.get("Solar_2030_MWh", 0) or
        wind_2050 == row.get("Wind_2030_MWh", 0) or
        other_2050 == row.get("Other Renewables_2030_MWh", 0)
    )
    
    # Append results
    verification_results.append({
        "Country": row.get("Country Name"),
        "Income Group": row.get("Income group"),
        "Target_2030": target_share_2030,
        "Actual_2030": actual_share_2030,
        "Meets_Target_2030": actual_share_2030 >= target_share_2030,
        "Min Rule Applied 2030": min_rule_applied_2030,
        "Target_2050": target_share_2050,
        "Actual_2050": actual_share_2050,
        "Meets_Target_2050": actual_share_2050 >= target_share_2050,
        "Min Rule Applied 2050": min_rule_applied_2050
    })

verification_df = pd.DataFrame(verification_results)

# Calculate summary statistics
total_countries = len(verification_df)
countries_meeting_target_2030 = verification_df["Meets_Target_2030"].sum()
countries_meeting_target_2050 = verification_df["Meets_Target_2050"].sum()
countries_min_rule_2030 = verification_df["Min Rule Applied 2030"].sum()
countries_min_rule_2050 = verification_df["Min Rule Applied 2050"].sum()

print(f"Countries meeting 2030 target: {countries_meeting_target_2030}/{total_countries}")
print(f"Countries meeting 2050 target: {countries_meeting_target_2050}/{total_countries}")
print(f"Countries with min rule applied for 2030: {countries_min_rule_2030}/{total_countries}")
print(f"Countries with min rule applied for 2050: {countries_min_rule_2050}/{total_countries}")
print("=== END VERIFICATION ===\n")

### Last Task
# Clean up the column order in the final merged dataset
column_order = [
    "Country Name", "ISO3_code", "Region", "Income group", 
    "PopTotal_2024", "PopTotal_2030", "PopTotal_2050", "Growth_Factor_2024_2030", "Growth_Factor_2024_2050",
    "Hydro_2024", "Solar_2024", "Wind_2024", "Other Renewables_2024", "Nuclear_2024", "Fossil_2024",
    "Hydro_2024_MWh", "Solar_2024_MWh", "Wind_2024_MWh", "Other Renewables_2024_MWh","Nuclear_2024_MWh", "Fossil_2024_MWh",
    "res_capacity_target", "res_share_target", "Category",
    "Hydro_2030", "Solar_2030", "Wind_2030", "Other Renewables_2030", 
    "Hydro_2030_MWh", "Solar_2030_MWh", "Wind_2030_MWh", "Other Renewables_2030_MWh", "Nuclear_2030_MWh", "Fossil_2030_MWh",
    "Hydro_2050_MWh", "Solar_2050_MWh", "Wind_2050_MWh", "Other Renewables_2050_MWh", "Nuclear_2050_MWh", "Fossil_2050_MWh"
    ]
final_merged_df = final_merged_df[column_order]

print("Final number of countries: ", final_merged_df["ISO3_code"].nunique())

# Save the final merged dataset to a CSV file
final_merged_df.to_excel("outputs_processed_data/p1_b_ember_2024_30_50.xlsx", index=False)

