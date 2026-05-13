"""
Satori — Google Drive to BigQuery Sync
Reads the Account Coverage Plan Excel from a shared Google Drive folder,
parses all sheets, and loads structured data into BigQuery tables.

Usage:
  python drive_sync.py                    # Full sync (download + parse + load)
  python drive_sync.py --parse-only       # Parse local file only (skip Drive download)
  python drive_sync.py --dry-run          # Parse and print schemas, don't load to BQ
  python drive_sync.py --local            # Sync from local data/ folder (no Drive)

Authentication (in priority order):
  1. Service account key: place a .json key file in satori-chatbot/service-account/
  2. gcloud ADC: gcloud auth application-default login (with Drive scope)
  3. Local mode: --local flag, reads from data/ folder

Requires:
  pip install google-api-python-client google-auth pandas google-cloud-bigquery openpyxl
"""

import os, sys, json, io, re
import pandas as pd
from datetime import datetime

# ── Config ──
DRIVE_FOLDER_ID = "1FGUUad_F_LFUU_JVjG_yuRQ_TvFeQiPM"
FILE_NAME_PATTERN = "Account Coverage Plan"
ATTENDANCE_FILE_PATTERN = "Attendance"
LOCAL_CACHE = os.path.join(os.path.dirname(__file__), "data", "account_coverage_plan.xlsx")
ATTENDANCE_CACHE = os.path.join(os.path.dirname(__file__), "data", "attendance.xlsx")
BQ_PROJECT = "ai-vertex-mahad"
BQ_DATASET = "Satori_Project"
SETTINGS_PATH = os.path.join(os.path.dirname(__file__), "settings.json")
SERVICE_ACCOUNT_DIR = os.path.join(os.path.dirname(__file__), "service-account")


def get_drive_credentials():
    """Get Google credentials with Drive read scope.
    Priority: 1) Service account key  2) Application Default Credentials"""
    from google.oauth2 import service_account as sa_module
    from google.auth import default

    # 1. Check for service account key file
    if os.path.isdir(SERVICE_ACCOUNT_DIR):
        for f in os.listdir(SERVICE_ACCOUNT_DIR):
            if f.endswith(".json"):
                key_path = os.path.join(SERVICE_ACCOUNT_DIR, f)
                print(f"Using service account: {f}")
                creds = sa_module.Credentials.from_service_account_file(
                    key_path, scopes=["https://www.googleapis.com/auth/drive.readonly"]
                )
                return creds

    # 2. Fall back to Application Default Credentials
    try:
        creds, _ = default(scopes=["https://www.googleapis.com/auth/drive.readonly"])
        return creds
    except Exception:
        raise PermissionError(
            "No valid Google credentials found.\n\n"
            "  Option A — Service Account (recommended for Workspace orgs):\n"
            "    1. Go to GCP Console > IAM > Service Accounts\n"
            "    2. Create a service account (or use an existing one)\n"
            "    3. Create a JSON key and save it to:\n"
            f"       {SERVICE_ACCOUNT_DIR}/\n"
            "    4. Share your Drive folder with the service account email\n"
            "       (the email looks like: name@project.iam.gserviceaccount.com)\n\n"
            "  Option B — Local mode (no credentials needed):\n"
            "    Download files manually and run: python drive_sync.py --local"
        )


def download_from_drive(folder_id, file_pattern, output_path):
    """Download the latest matching file from Google Drive."""
    try:
        from googleapiclient.discovery import build
        from googleapiclient.errors import HttpError
    except ImportError:
        raise ImportError(
            "Google API libraries not installed.\n"
            "Run: pip install google-api-python-client google-auth"
        )

    creds = get_drive_credentials()

    try:
        service = build("drive", "v3", credentials=creds)
        query = f"'{folder_id}' in parents and name contains '{file_pattern}' and trashed = false"
        results = service.files().list(q=query, orderBy="modifiedTime desc", pageSize=1,
                                       fields="files(id, name, modifiedTime, mimeType)").execute()
    except HttpError as e:
        if "insufficientPermissions" in str(e) or "403" in str(e):
            raise PermissionError(
                "Google Drive access denied.\n\n"
                "  If using a service account, make sure you shared the Drive folder\n"
                "  with the service account's email address.\n\n"
                "  Or place files in the 'data/' folder and run: python drive_sync.py --local"
            )
        raise

    files = results.get("files", [])
    if not files:
        raise FileNotFoundError(f"No file matching '{file_pattern}' found in folder {folder_id}")

    file_info = files[0]
    print(f"Found: {file_info['name']} (modified: {file_info['modifiedTime']})")

    # Download
    request = service.files().get_media(fileId=file_info["id"])
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "wb") as f:
        from googleapiclient.http import MediaIoBaseDownload
        downloader = MediaIoBaseDownload(f, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()

    print(f"Downloaded to: {output_path}")
    return file_info


def parse_accounts(xls):
    """Parse the Accounts sheet — 338 active accounts with visit data."""
    df = pd.read_excel(xls, sheet_name="Accounts", header=0)
    df.columns = ["VP", "AM", "Location", "Account", "Tier", "Dormant", "Jan_Visits", "Feb_Visits", "Mar_Visits", "Q1_Visits", "Zero_Visit"]
    df = df.dropna(subset=["Account"])
    df["Dormant"] = df["Dormant"].fillna("No").apply(lambda x: "Yes" if str(x).strip().lower() not in ("no", "nan", "") else "No")
    for col in ["Jan_Visits", "Feb_Visits", "Mar_Visits", "Q1_Visits"]:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)
    df["Zero_Visit"] = df["Zero_Visit"].fillna("OK")
    return df


def parse_am_scorecard(xls):
    """Parse AM Scorecard — per-AM book size, targets, pipeline, win rate."""
    df = pd.read_excel(xls, sheet_name="AM Scorecard", header=2)
    df = df.dropna(subset=["AM"])
    # Drop total/notes rows
    df = df[~df["VP"].astype(str).str.contains("Total|Notes|•", na=False)]
    df = df[df["AM"].notna() & (df["AM"] != "")]
    cols_rename = {}
    for c in df.columns:
        clean = re.sub(r'[^a-zA-Z0-9]', '_', str(c)).strip('_')
        clean = re.sub(r'_+', '_', clean)
        cols_rename[c] = clean
    df = df.rename(columns=cols_rename)
    # Ensure numeric columns
    num_cols = ["A", "B", "C", "Active_Book", "Dormant", "Q1_Visits", "Zero_Visit",
                "_2026_Target____", "Q1_ACH____", "Open_Pipeline____", "Hist__Win_Rate"]
    for col in num_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def parse_workload_feasibility(xls):
    """Parse Workload Feasibility — AM workload analysis."""
    df = pd.read_excel(xls, sheet_name="Workload Feasibility", header=None)
    # Find the data table (starts after the policy rows)
    # Look for the row with "AM" header
    header_row = None
    for i, row in df.iterrows():
        if any(str(v).strip() == "AM" for v in row.values):
            header_row = i
            break
    if header_row is None:
        return pd.DataFrame()
    df = pd.read_excel(xls, sheet_name="Workload Feasibility", header=header_row)
    df = df.dropna(subset=[df.columns[0]])
    # Drop rows that are notes
    df = df[~df.iloc[:, 0].astype(str).str.contains("Total|Notes|•|Cadence|Tier|Field|Visit", na=False)]
    return df


def parse_plan_vs_pipeline(xls):
    """Parse Plan vs Pipeline — revenue plan vs achievement vs CRM pipeline."""
    df = pd.read_excel(xls, sheet_name="Plan vs Pipeline", header=2)
    df = df.dropna(subset=["AM"])
    df = df[~df["AM"].astype(str).str.contains("Total|Notes|•", na=False)]
    num_cols = ["2026 Target ($)", "Q1 Target ($)", "Q1 ACH ($)", "Q1 % of Plan",
                "Remaining 2026 ($)", "CRM Pipeline ($)", "Coverage Ratio"]
    for col in num_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def parse_hunting_gap(xls):
    """Parse Hunting Gap — NNL quotas and recommendations."""
    df = pd.read_excel(xls, sheet_name="Hunting Gap", header=4)
    df = df.dropna(subset=["AM"])
    df = df[~df["AM"].astype(str).str.contains("Total|Notes|•|Diagnosis", na=False)]
    return df


def parse_kpi_scorecard(xls):
    """Parse KPI Scorecard — KPIs with weights and targets."""
    df = pd.read_excel(xls, sheet_name="KPI Scorecard", header=2)
    df = df.rename(columns={"#": "KPI_Number", "Unnamed: 1": "Category_Group"})
    # Keep rows that have a KPI number OR have a valid KPI name (catches unnumbered rows like CRM hygiene)
    df = df[
        pd.to_numeric(df["KPI_Number"], errors="coerce").notna() |
        (df["KPI"].notna() & ~df["KPI"].astype(str).str.contains("Total|total", na=True))
    ]
    # Remove footer/notes rows
    df = df[~df["KPI_Number"].astype(str).str.contains("Governance|•|nan", na=False) | df["KPI"].notna()]
    df = df[df["KPI"].notna()]
    df = df[~df["KPI"].astype(str).str.contains("Total weight", na=False)]
    # Assign sequential KPI numbers where missing
    for i, idx in enumerate(df.index):
        if pd.isna(df.at[idx, "KPI_Number"]) or str(df.at[idx, "KPI_Number"]) == "nan":
            df.at[idx, "KPI_Number"] = int(df["KPI_Number"].max()) + 1 if pd.to_numeric(df["KPI_Number"], errors="coerce").max() > 0 else i + 1
    df["KPI_Number"] = pd.to_numeric(df["KPI_Number"], errors="coerce").astype(int)
    # Forward-fill category group
    df["Category_Group"] = df["Category_Group"].ffill()
    df["Weight"] = pd.to_numeric(df["Weight"], errors="coerce")
    return df


def parse_pipeline_health(xls):
    """Parse Pipeline Health — all salespeople including untagged."""
    df = pd.read_excel(xls, sheet_name="Pipeline Health", header=2)
    df = df.dropna(subset=["Salesperson"])
    df = df[~df["Salesperson"].astype(str).str.contains("Total|Notes|•", na=False)]
    num_cols = ["Open Pipeline ($)", "Open Deals", "Accounts", "Historical Won ($)",
                "Historical Lost ($)", "Win Rate (by $)"]
    for col in num_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def parse_dormant_cleanup(xls):
    """Parse Dormant Cleanup — accounts recommended for removal."""
    df = pd.read_excel(xls, sheet_name="Dormant Cleanup", header=2)
    df = df.dropna(subset=["Account"])
    df["Q1 Visits"] = pd.to_numeric(df["Q1 Visits"], errors="coerce").fillna(0).astype(int)
    return df


def parse_attendance(file_path):
    """Parse Attendance.xlsx — employee attendance records.
    Reads all sheets/data and normalises into a single table matching Attendance_Data schema."""
    df = pd.read_excel(file_path, header=0)
    # Clean column names to match BQ schema
    col_map = {}
    for c in df.columns:
        clean = re.sub(r'[^a-zA-Z0-9_]', '_', str(c)).strip('_')
        clean = re.sub(r'_+', '_', clean)
        col_map[c] = clean
    df = df.rename(columns=col_map)
    # Drop completely empty rows
    df = df.dropna(how='all')
    print(f"  Attendance: {len(df)} rows × {len(df.columns)} cols → {list(df.columns)}")
    return df


def clean_column_names(df):
    """Sanitise column names for BigQuery compatibility."""
    new_cols = []
    for c in df.columns:
        clean = re.sub(r'[^a-zA-Z0-9_]', '_', str(c)).strip('_')
        clean = re.sub(r'_+', '_', clean)
        if clean[0:1].isdigit():
            clean = "col_" + clean
        new_cols.append(clean)
    df.columns = new_cols
    return df


def load_to_bigquery(df, table_name, project, dataset):
    """Load a DataFrame into BigQuery, replacing the existing table."""
    from google.cloud import bigquery
    client = bigquery.Client(project=project)
    table_ref = f"{project}.{dataset}.{table_name}"
    job_config = bigquery.LoadJobConfig(write_disposition="WRITE_TRUNCATE", autodetect=True)
    job = client.load_table_from_dataframe(df, table_ref, job_config=job_config)
    job.result()
    table = client.get_table(table_ref)
    print(f"  ✓ {table_name}: {table.num_rows} rows loaded")
    return table.num_rows


def run_sync(parse_only=False, dry_run=False, local_file=None):
    """Main sync orchestrator."""
    print("=" * 60)
    print("Satori — Drive Data Sync")
    print("=" * 60)

    tables = {}

    # ── 1. Account Coverage Plan ──
    acct_path = local_file or LOCAL_CACHE
    if not parse_only and not local_file:
        try:
            download_from_drive(DRIVE_FOLDER_ID, FILE_NAME_PATTERN, LOCAL_CACHE)
            acct_path = LOCAL_CACHE
        except Exception as e:
            print(f"Account Coverage download failed: {e}")
            if os.path.exists(LOCAL_CACHE):
                print(f"Using cached file: {LOCAL_CACHE}")
                acct_path = LOCAL_CACHE
            else:
                acct_path = None

    if acct_path and os.path.exists(acct_path):
        print(f"\nParsing Account Coverage: {acct_path}")
        xls = pd.ExcelFile(acct_path)
        tables.update({
            "Sales_Accounts": parse_accounts(xls),
            "Sales_AM_Scorecard": parse_am_scorecard(xls),
            "Sales_Plan_vs_Pipeline": parse_plan_vs_pipeline(xls),
            "Sales_Pipeline_Health": parse_pipeline_health(xls),
            "Sales_Hunting_Gap": parse_hunting_gap(xls),
            "Sales_KPI_Scorecard": parse_kpi_scorecard(xls),
            "Sales_Dormant_Accounts": parse_dormant_cleanup(xls),
        })
        try:
            wf = parse_workload_feasibility(xls)
            if not wf.empty:
                tables["Sales_Workload_Feasibility"] = wf
        except Exception as e:
            print(f"  ⚠ Workload Feasibility parse failed: {e}")

    # ── 2. Attendance ──
    att_path = ATTENDANCE_CACHE
    if not parse_only:
        try:
            download_from_drive(DRIVE_FOLDER_ID, ATTENDANCE_FILE_PATTERN, ATTENDANCE_CACHE)
            att_path = ATTENDANCE_CACHE
        except Exception as e:
            print(f"Attendance download failed: {e}")
            if os.path.exists(ATTENDANCE_CACHE):
                print(f"Using cached attendance: {ATTENDANCE_CACHE}")
            else:
                att_path = None

    if att_path and os.path.exists(att_path):
        print(f"\nParsing Attendance: {att_path}")
        try:
            att_df = parse_attendance(att_path)
            tables["Attendance_Data"] = att_df
        except Exception as e:
            print(f"  ⚠ Attendance parse failed: {e}")

    # ── Clean all column names ──
    for name in tables:
        tables[name] = clean_column_names(tables[name])

    # ── Print summary ──
    print(f"\nParsed {len(tables)} tables:")
    for name, df in tables.items():
        print(f"  {name}: {len(df)} rows × {len(df.columns)} cols → {list(df.columns)}")

    if dry_run:
        print("\n[DRY RUN] No data loaded to BigQuery.")
        return tables

    # ── Load to BigQuery ──
    if not parse_only:
        print(f"\nLoading to BigQuery: {BQ_PROJECT}.{BQ_DATASET}")
        for name, df in tables.items():
            try:
                load_to_bigquery(df, name, BQ_PROJECT, BQ_DATASET)
            except Exception as e:
                print(f"  ✗ {name}: {e}")

    # ── Record sync metadata ──
    meta = {
        "last_sync": datetime.now().astimezone().isoformat(),
        "source_files": {
            "account_coverage": os.path.basename(acct_path) if acct_path else None,
            "attendance": os.path.basename(att_path) if att_path else None,
        },
        "tables_loaded": {name: len(df) for name, df in tables.items()},
        "drive_folder_id": DRIVE_FOLDER_ID,
    }
    meta_path = os.path.join(os.path.dirname(__file__), "data", "sync_metadata.json")
    os.makedirs(os.path.dirname(meta_path), exist_ok=True)
    with open(meta_path, "w") as f:
        json.dump(meta, f, indent=2)
    print(f"\nSync metadata saved to: {meta_path}")

    print("\n✓ Sync complete!")
    return tables


def run_local_sync(dry_run=False):
    """Sync from local files in the data/ folder (no Google Drive needed)."""
    print("=" * 60)
    print("Satori — Local File Sync (no Drive access needed)")
    print("=" * 60)

    data_dir = os.path.join(os.path.dirname(__file__), "data")
    os.makedirs(data_dir, exist_ok=True)

    tables = {}

    # Look for Account Coverage Plan
    acct_path = None
    for f in os.listdir(data_dir):
        if "account" in f.lower() and f.endswith(".xlsx"):
            acct_path = os.path.join(data_dir, f)
            break
    if not acct_path:
        acct_path = LOCAL_CACHE

    if acct_path and os.path.exists(acct_path):
        print(f"\nParsing Account Coverage: {os.path.basename(acct_path)}")
        xls = pd.ExcelFile(acct_path)
        tables.update({
            "Sales_Accounts": parse_accounts(xls),
            "Sales_AM_Scorecard": parse_am_scorecard(xls),
            "Sales_Plan_vs_Pipeline": parse_plan_vs_pipeline(xls),
            "Sales_Pipeline_Health": parse_pipeline_health(xls),
            "Sales_Hunting_Gap": parse_hunting_gap(xls),
            "Sales_KPI_Scorecard": parse_kpi_scorecard(xls),
            "Sales_Dormant_Accounts": parse_dormant_cleanup(xls),
        })
        try:
            wf = parse_workload_feasibility(xls)
            if not wf.empty:
                tables["Sales_Workload_Feasibility"] = wf
        except Exception as e:
            print(f"  ⚠ Workload Feasibility: {e}")
    else:
        print(f"\n⚠ No Account Coverage file found in {data_dir}")
        print(f"  Place your 'Account Coverage Plan*.xlsx' file there.")

    # Look for Attendance
    att_path = None
    for f in os.listdir(data_dir):
        if "attendance" in f.lower() and f.endswith(".xlsx"):
            att_path = os.path.join(data_dir, f)
            break
    if not att_path:
        att_path = ATTENDANCE_CACHE

    if att_path and os.path.exists(att_path):
        print(f"\nParsing Attendance: {os.path.basename(att_path)}")
        try:
            att_df = parse_attendance(att_path)
            tables["Attendance_Data"] = att_df
        except Exception as e:
            print(f"  ⚠ Attendance parse failed: {e}")
    else:
        print(f"\n⚠ No Attendance file found in {data_dir}")
        print(f"  Place your 'Attendance*.xlsx' file there.")

    if not tables:
        print("\n✗ No files found to sync!")
        print(f"  Place your .xlsx files in: {data_dir}")
        return tables

    # Clean columns
    for name in tables:
        tables[name] = clean_column_names(tables[name])

    print(f"\nParsed {len(tables)} tables:")
    for name, df in tables.items():
        print(f"  {name}: {len(df)} rows × {len(df.columns)} cols")

    if dry_run:
        print("\n[DRY RUN] No data loaded to BigQuery.")
        return tables

    # Load to BigQuery
    print(f"\nLoading to BigQuery: {BQ_PROJECT}.{BQ_DATASET}")
    for name, df in tables.items():
        try:
            load_to_bigquery(df, name, BQ_PROJECT, BQ_DATASET)
        except Exception as e:
            print(f"  ✗ {name}: {e}")

    print("\n✓ Sync complete!")
    return tables


if __name__ == "__main__":
    args = sys.argv[1:]
    parse_only = "--parse-only" in args
    dry_run = "--dry-run" in args
    local_mode = "--local" in args

    # Allow passing a local file path
    local_file = None
    for a in args:
        if a.endswith(".xlsx") and os.path.exists(a):
            local_file = a
            parse_only = True

    try:
        if local_mode:
            run_local_sync(dry_run=dry_run)
        else:
            run_sync(parse_only=parse_only, dry_run=dry_run, local_file=local_file)
    except PermissionError as e:
        print(f"\n{'=' * 60}")
        print(f"ACCESS ERROR:")
        print(f"{'=' * 60}")
        print(str(e))
    except FileNotFoundError as e:
        print(f"\n✗ File not found: {e}")
    except ImportError as e:
        print(f"\n✗ Missing dependency: {e}")
        print("  Run: pip install google-api-python-client google-auth pandas google-cloud-bigquery openpyxl")
    except Exception as e:
        print(f"\n✗ Error: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()

    # Keep console window open so user can read output
    print("\n" + "=" * 60)
    input("Press Enter to close this window...")
