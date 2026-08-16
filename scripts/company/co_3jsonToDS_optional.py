
    # Test script: Loads extracted rows from JSON and writes to Datastore.
import json
from pathlib import Path
from co_2data import update_Datastore

def prompt_for_folder():
    folder = input("Enter company folder name (under scripts/company): ").strip().strip('"').strip("'")
    if not folder:
        raise ValueError("Folder name is required")
    return folder

def main():
    folder = prompt_for_folder()
    company_dir = Path(__file__).resolve().parent / folder
    json_files = list(company_dir.glob("*_extracted.json"))
    if not json_files:
        print(f"No *_extracted.json files found in {company_dir}")
        return
    print("Found extracted JSON files:")
    for idx, fpath in enumerate(json_files, 1):
        print(f"  {idx}. {fpath.name}")
    pick = input(f"Pick file (1-{len(json_files)}): ").strip()
    try:
        pick_idx = int(pick) - 1
        target_file = json_files[pick_idx]
    except Exception:
        print("Invalid selection")
        return

    with target_file.open("r", encoding="utf-8") as f:
        rows = json.load(f)

    print(f"Loaded {len(rows)} rows from {target_file}")

    # Prompt for required info
    granularity = input("Granularity (e.g. Yearly): ").strip() or "Yearly"
    scriptID = input("ScriptID: ").strip() or folder
    URL = input("Source URL (press Enter to skip): ").strip() or ""
    dataName = input("Data Name (default: <folder> Annual Report): ").strip() or f"{folder} Annual Report"
    kind = input("Kind (default: StagingData_v1): ").strip() or "StagingData_v1"

    update_Datastore(
        rows,
        granularity,
        scriptID,
        URL,
        dataName,
        kind,
    )
    print("Datastore update complete.")

if __name__ == "__main__":
    main()