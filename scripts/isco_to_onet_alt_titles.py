"""
Map ISCO-08 codes → SOC 2010 → SOC 2018 → O*NET-SOC alternate titles.

Downloads three public datasets, chains the crosswalks, and outputs a CSV with
columns: iscoGroup, onetSocCode, alternateTitle.

Data sources:
  1. BLS ISCO-08 → SOC 2010 crosswalk (.xls)
  2. BLS SOC 2010 → SOC 2018 crosswalk (.xlsx)
  3. O*NET 30.2 Alternate Titles (.txt, tab-delimited)

Usage:
    python scripts/isco_to_onet_alt_titles.py

Output:
    scripts/output/isco_onet_alternate_titles.csv
"""

from __future__ import annotations

import logging
import re
import sys
from pathlib import Path
from urllib.request import Request, urlopen

import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

SCRIPT_DIR = Path(__file__).resolve().parent
DATA_DIR = SCRIPT_DIR / "data"
OUTPUT_DIR = SCRIPT_DIR / "output"

# ---------------------------------------------------------------------------
# Data source URLs
# ---------------------------------------------------------------------------
ISCO_SOC_2010_URL = "https://www.bls.gov/soc/ISCO_SOC_Crosswalk.xls"
SOC_2010_TO_2018_URL = "https://www.bls.gov/soc/2018/soc_2010_to_2018_crosswalk.xlsx"
ONET_ALT_TITLES_URL = (
    "https://www.onetcenter.org/dl_files/database/db_30_2_text/Alternate%20Titles.txt"
)

# Local filenames for cached downloads
ISCO_SOC_FILE = DATA_DIR / "ISCO_SOC_Crosswalk.xls"
SOC_2010_2018_FILE = DATA_DIR / "soc_2010_to_2018_crosswalk.xlsx"
ALT_TITLES_FILE = DATA_DIR / "Alternate_Titles.txt"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def download_file(url: str, dest: Path) -> None:
    """Download a file if it doesn't already exist locally."""
    if dest.exists():
        logger.info("Using cached %s", dest.name)
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    logger.info("Downloading %s ...", url)
    req = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(req, timeout=60) as resp, open(dest, "wb") as f:  # noqa: S310
        while chunk := resp.read(1 << 16):
            f.write(chunk)
    logger.info("  → saved %s (%d bytes)", dest.name, dest.stat().st_size)


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def normalise_soc_code(raw: str) -> str:
    """Normalise a SOC code to the canonical 'NN-NNNN' 7-char form."""
    s = str(raw).strip().replace("\u2011", "-").replace("\u2013", "-")
    s = re.sub(r"[^\d-]", "", s)
    if re.match(r"^\d{2}-\d{4}$", s):
        return s
    return s


def load_isco_to_soc2010(path: Path) -> pd.DataFrame:
    """
    Parse the BLS ISCO-08 → SOC 2010 crosswalk XLS.

    Returns DataFrame with columns: isco08, soc2010
    """
    # Header row is at row 6 (0-indexed), columns:
    # ISCO-08 Code | ISCO-08 Title EN | part | 2010 SOC Code | 2010 SOC Title | Comment
    df = pd.read_excel(path, engine="xlrd", header=6)
    logger.info("ISCO→SOC2010 columns: %s", list(df.columns))

    isco_col = "ISCO-08 Code"
    soc_col = "2010 SOC Code"

    result = df[[isco_col, soc_col]].copy()
    result.columns = ["isco08", "soc2010"]
    result = result.dropna(subset=["isco08", "soc2010"])

    # Clean ISCO codes → 4-digit strings
    result["isco08"] = result["isco08"].astype(str).str.strip().str.zfill(4)
    # Remove any rows where isco08 doesn't look like a valid 4-digit code
    result = result[result["isco08"].str.match(r"^\d{4}$")]

    # Clean SOC codes
    result["soc2010"] = result["soc2010"].apply(normalise_soc_code)
    result = result[result["soc2010"].str.match(r"^\d{2}-\d{4}$")]

    result = result.drop_duplicates()
    logger.info("  → %d ISCO→SOC2010 mappings", len(result))
    return result


def load_soc2010_to_soc2018(path: Path) -> pd.DataFrame:
    """
    Parse the BLS SOC 2010 → SOC 2018 crosswalk XLSX.

    Returns DataFrame with columns: soc2010, soc2018
    """
    # Header row is at row 8 (0-indexed), columns:
    # 2010 SOC Code | 2010 SOC Title | 2018 SOC Code | 2018 SOC Title
    df = pd.read_excel(path, engine="openpyxl", header=8)
    logger.info("SOC2010→SOC2018 columns: %s", list(df.columns))

    soc2010_col = "2010 SOC Code"
    soc2018_col = "2018 SOC Code"

    result = df[[soc2010_col, soc2018_col]].copy()
    result.columns = ["soc2010", "soc2018"]
    result = result.dropna(subset=["soc2010", "soc2018"])

    result["soc2010"] = result["soc2010"].apply(normalise_soc_code)
    result["soc2018"] = result["soc2018"].apply(normalise_soc_code)
    result = result[
        result["soc2010"].str.match(r"^\d{2}-\d{4}$")
        & result["soc2018"].str.match(r"^\d{2}-\d{4}$")
    ]
    result = result.drop_duplicates()
    logger.info("  → %d SOC2010→SOC2018 mappings", len(result))
    return result


def load_alternate_titles(path: Path) -> pd.DataFrame:
    """
    Parse the O*NET Alternate Titles tab-delimited file.

    Returns DataFrame with columns: onet_soc, alternate_title, soc2018
    The soc2018 column is derived by stripping the O*NET suffix (.XX).
    """
    df = pd.read_csv(path, sep="\t", dtype=str, encoding="utf-8")
    logger.info("Alternate Titles columns: %s", list(df.columns))

    onet_col = df.columns[0]  # 'O*NET-SOC Code'
    title_col = df.columns[1]  # 'Alternate Title'

    result = df[[onet_col, title_col]].copy()
    result.columns = ["onet_soc", "alternate_title"]
    result = result.dropna(subset=["onet_soc", "alternate_title"])
    result["alternate_title"] = result["alternate_title"].str.strip()
    result = result[result["alternate_title"] != ""]

    # Derive the 6-digit SOC 2018 code by stripping the O*NET suffix (.00, .01, etc.)
    result["soc2018"] = result["onet_soc"].str.replace(r"\.\d{2}$", "", regex=True)

    logger.info("  → %d alternate titles loaded", len(result))
    return result


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def build_isco_to_alt_titles() -> pd.DataFrame:
    """
    Chain: ISCO-08 → SOC 2010 → SOC 2018 → O*NET-SOC alternate titles.

    Returns DataFrame with columns: iscoGroup, onetSocCode, alternateTitle
    """
    # 1. Load all three datasets
    isco_soc2010 = load_isco_to_soc2010(ISCO_SOC_FILE)
    soc2010_2018 = load_soc2010_to_soc2018(SOC_2010_2018_FILE)
    alt_titles = load_alternate_titles(ALT_TITLES_FILE)

    # 2. Chain ISCO → SOC2010 → SOC2018
    isco_soc2018 = isco_soc2010.merge(soc2010_2018, on="soc2010", how="inner")
    logger.info("ISCO→SOC2018 after join: %d rows", len(isco_soc2018))

    # 3. Join with alternate titles on SOC2018
    merged = isco_soc2018.merge(alt_titles, on="soc2018", how="inner")
    logger.info("After joining alternate titles: %d rows", len(merged))

    # 4. Select and rename output columns
    output = merged[["isco08", "onet_soc", "alternate_title"]].copy()
    output.columns = ["iscoGroup", "onetSocCode", "alternateTitle"]
    output = output.drop_duplicates().sort_values(["iscoGroup", "onetSocCode", "alternateTitle"])
    output = output.reset_index(drop=True)

    logger.info(
        "Final output: %d rows, %d unique ISCO groups, %d unique O*NET-SOC codes",
        len(output),
        output["iscoGroup"].nunique(),
        output["onetSocCode"].nunique(),
    )
    return output


def main() -> None:
    logger.info("=" * 60)
    logger.info("ISCO-08 → O*NET Alternate Titles Mapper")
    logger.info("=" * 60)

    # Step 1: Download source files
    logger.info("\n--- Downloading source files ---")
    download_file(ISCO_SOC_2010_URL, ISCO_SOC_FILE)
    download_file(SOC_2010_TO_2018_URL, SOC_2010_2018_FILE)
    download_file(ONET_ALT_TITLES_URL, ALT_TITLES_FILE)

    # Step 2: Build the crosswalk and output
    logger.info("\n--- Processing crosswalk chain ---")
    output = build_isco_to_alt_titles()

    # Step 3: Write CSV
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUTPUT_DIR / "isco_onet_alternate_titles.csv"
    output.to_csv(out_path, index=False)
    logger.info("\nOutput written to %s", out_path)

    # Print summary stats
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"Total alternate titles mapped: {len(output):,}")
    print(f"Unique ISCO-08 groups:         {output['iscoGroup'].nunique()}")
    print(f"Unique O*NET-SOC codes:        {output['onetSocCode'].nunique()}")
    print(f"\nTop 10 ISCO groups by alternate title count:")
    top = output.groupby("iscoGroup").size().nlargest(10)
    for isco, count in top.items():
        print(f"  {isco}: {count:,} titles")
    print(f"\nSample rows:")
    print(output.head(15).to_string(index=False))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        logger.exception("Fatal error")
        sys.exit(1)
