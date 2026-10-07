#!/usr/bin/env python3
import os
import shutil
import sys
from pathlib import Path

# Configuration
DATA_DIR = Path("data")
DB_FILE = DATA_DIR / "app.db"
DIRECTORIES_TO_CLEAN = [
    DATA_DIR / "frames",
    DATA_DIR / "reconstructions",
    DATA_DIR / "segments",
    DATA_DIR / "uploads",
]
DIRECTORIES_TO_PRESERVE = [
    DATA_DIR / "models",
]

def clean_directory(directory: Path):
    """Removes all files and subdirectories in the given directory."""
    if not directory.exists():
        print(f"Skipping {directory} (does not exist)")
        return

    print(f"Cleaning {directory}...")
    for item in directory.iterdir():
        try:
            if item.is_file() or item.is_symlink():
                item.unlink()
            elif item.is_dir():
                shutil.rmtree(item)
        except Exception as e:
            print(f"Failed to delete {item}: {e}")

def main():
    # Warning and Confirmation
    print("WARNING: This script will delete all data in the following locations:")
    print(f"  - Database: {DB_FILE}")
    for d in DIRECTORIES_TO_CLEAN:
        print(f"  - Directory contents: {d}")
    print("\nThe following will be PRESERVED:")
    for d in DIRECTORIES_TO_PRESERVE:
        print(f"  - {d}")
    
    confirm = input("\nAre you sure you want to continue? (yes/no): ").lower()
    if confirm != "yes":
        print("Operation cancelled.")
        sys.exit(0)

    # 1. Remove Database
    if DB_FILE.exists():
        try:
            print(f"Removing database file: {DB_FILE}")
            DB_FILE.unlink()
        except Exception as e:
            print(f"Error removing database: {e}")
    else:
        print(f"Database file {DB_FILE} not found.")

    # 2. Clean Directories
    for directory in DIRECTORIES_TO_CLEAN:
        clean_directory(directory)

    print("\nData cleanup complete.")
    print("Please restart the backend service to re-initialize the database.")

if __name__ == "__main__":
    if not DATA_DIR.exists():
        print(f"Error: Data directory '{DATA_DIR}' not found in current path.")
        print("Please run this script from the project root.")
        sys.exit(1)
    
    main()
