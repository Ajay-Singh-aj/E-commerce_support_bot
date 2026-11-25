"""Package the agent skill for Agensi / any SKILL.md marketplace.

Copies the app's demo data and policies into skills/ecommerce-support-desk (single source of truth),
then writes dist/ecommerce-support-desk.zip with SKILL.md at the zip root.

Usage: python tools/build_skill.py
"""

import shutil
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "ecommerce-support-desk"
DIST = ROOT / "dist"
ZIP = DIST / "ecommerce-support-desk.zip"
SKIP = {"__pycache__", ".DS_Store"}


def sync():
    (SKILL / "data").mkdir(exist_ok=True)
    shutil.copyfile(ROOT / "ecommerce_setup.sql", SKILL / "data" / "demo_store.sql")
    pol = SKILL / "policies"
    pol.mkdir(exist_ok=True)
    for src in sorted((ROOT / "policies").glob("*.txt")):
        shutil.copyfile(src, pol / (src.stem + ".md"))
    shutil.copyfile(ROOT / "LICENSE", SKILL / "LICENSE")


def build():
    DIST.mkdir(exist_ok=True)
    with zipfile.ZipFile(ZIP, "w", zipfile.ZIP_DEFLATED) as z:
        for path in sorted(SKILL.rglob("*")):
            if path.is_file() and not (SKIP & set(path.parts)) and path.suffix != ".pyc":
                z.write(path, path.relative_to(SKILL).as_posix())
    size_kb = ZIP.stat().st_size / 1024
    print(f"Built {ZIP} ({size_kb:.1f} KB)")


if __name__ == "__main__":
    sync()
    build()
