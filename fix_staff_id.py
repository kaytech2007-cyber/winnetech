"""Fix Staff ID format: WTI-<programme>/<year>-<num> -> WTI-<programme>/<num>-<year>"""
import re
from pathlib import Path

path = Path("core/models.py")
content = path.read_text(encoding="utf-8")
Path("core/models.py.bak").write_text(content, encoding="utf-8")

# Change 1: add suffix, change prefix
old_prefix = "prefix = f'WTI-{programme_code}/{year}-'"
new_prefix = "prefix = f'WTI-{programme_code}/'\n    suffix = f'-{year}'"

if old_prefix not in content:
    print("❌ Could not find prefix line. Already changed? Aborting.")
    raise SystemExit(1)
content = content.replace(old_prefix, new_prefix, 1)

# Change 2: append suffix to the staff_id f-string
old_id = "staff_id = f'{prefix}{sequence:02d}'"
new_id = "staff_id = f'{prefix}{sequence:02d}{suffix}'"

if old_id not in content:
    print("❌ Could not find staff_id line. Already changed? Aborting.")
    raise SystemExit(1)
content = content.replace(old_id, new_id, 1)

path.write_text(content, encoding="utf-8")
print("✅ Done. Format now: WTI-IT/01-2026")
print("✅ Backup: core/models.py.bak")