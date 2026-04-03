#!/usr/bin/env python3
"""
Bulk fix script to replace PostgreSQL-specific syntax with database-agnostic code
in all raw_repository.py files.
"""
import os
import re
from pathlib import Path

BASE_DIR = Path('/home/abbbose/projects/protouch/weel-backend/apps')

def fix_file(filepath):
    """Fix PostgreSQL-specific syntax in a single file."""
    with open(filepath, 'r') as f:
        content = f.read()
    
    original = content
    
    # 1. Replace public. schema prefix with get_table_name()
    # Pattern: "public.table_name" -> get_table_name("table_name")
    content = re.sub(
        r'public\.(\w+)',
        r'{get_table_name("\1")}',
        content
    )
    
    # 2. Replace ::type casts with empty string for SQLite compatibility
    # Pattern: column::type -> column (for SQLite, we'll handle in compat layer)
    content = re.sub(
        r"::(text|numeric|int|integer|bigint|boolean)",
        r'',
        content
    )
    
    # 3. Replace ILIKE with LIKE COLLATE NOCASE for SQLite
    # This is trickier - we need to add conditional logic
    # For now, replace with LIKE (will need manual fix for case-insensitivity)
    content = re.sub(
        r'\bILIKE\b',
        'LIKE',  # Will need COLLATE NOCASE added manually
        content
    )
    
    # 4. Replace = ANY(%s) with IN clause placeholder marker
    # Pattern: column = ANY(%s) -> __ANY_MARKER__
    content = re.sub(
        r'= ANY\(%s\)',
        '= __ANY_MARKER__(%s)',
        content
    )
    
    # 5. Replace RETURNING * with conditional
    content = re.sub(
        r'\bRETURNING \*',
        '__RETURNING_MARKER__',
        content
    )
    
    # 6. Replace COUNT(*) FILTER with SUM(CASE WHEN)
    content = re.sub(
        r'COUNT\(\*\) FILTER \(WHERE ([^)]+)\)',
        r'SUM(CASE WHEN \1 THEN 1 ELSE 0 END)',
        content
    )
    
    # 7. Replace AS exists with AS exists_flag (exists is reserved in some SQL)
    content = re.sub(
        r'\bAS exists\b',
        'AS exists_flag',
        content
    )
    content = re.sub(
        r'row\["exists"\]',
        'row["exists_flag"]',
        content
    )
    
    if content != original:
        with open(filepath, 'w') as f:
            f.write(content)
        print(f"✓ Fixed: {filepath}")
        return True
    return False

def main():
    files_fixed = []
    
    # Find all raw_repository.py and raw_*.py files
    for root, dirs, files in os.walk(BASE_DIR):
        for filename in files:
            if filename.startswith('raw_') and filename.endswith('.py'):
                filepath = os.path.join(root, filename)
                if fix_file(filepath):
                    files_fixed.append(filepath)
    
    print(f"\nTotal files fixed: {len(files_fixed)}")
    for f in files_fixed:
        print(f"  - {f}")

if __name__ == '__main__':
    main()
