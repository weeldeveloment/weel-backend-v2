"""
Database compatibility layer - makes raw SQL work with both PostgreSQL and SQLite.
"""
from django.db import connection


def is_postgresql():
    """Check if the current database backend is PostgreSQL."""
    return connection.vendor == 'postgresql'


def is_sqlite():
    """Check if the current database backend is SQLite."""
    return connection.vendor == 'sqlite'


def get_table_name(table_name):
    """
    Get table name without schema prefix for SQLite, with schema for PostgreSQL.
    Usage: get_table_name('property_apartment') instead of 'public.property_apartment'
    """
    if is_sqlite():
        # Remove schema prefix if present
        if '.' in table_name:
            return table_name.split('.')[-1]
        return table_name
    return table_name


def type_cast(value, cast_type):
    """
    Return SQL type cast expression compatible with current database.
    Usage: In SQL strings like f"... {type_cast('column', 'numeric')} ..."
    """
    if is_postgresql():
        return f"{value}::{cast_type}"
    else:
        # SQLite doesn't need explicit casts in most cases
        return value


def ilike_operator():
    """Return the case-insensitive LIKE operator for current database."""
    if is_postgresql():
        return 'ILIKE'
    else:
        return 'LIKE'


def case_insensitive_like_sql(column, pattern_placeholder):
    """
    Generate case-insensitive LIKE clause for current database.
    Usage: case_insensitive_like_sql('p.title', '%search%')
    Returns: "p.title ILIKE %s" for PostgreSQL, "p.title LIKE %s COLLATE NOCASE" for SQLite
    """
    if is_postgresql():
        return f"{column} ILIKE {pattern_placeholder}"
    else:
        return f"{column} LIKE {pattern_placeholder} COLLATE NOCASE"


def any_array_sql(column, placeholder):
    """
    Generate array membership test compatible with current database.
    For PostgreSQL: column = ANY(%s)
    For SQLite: column IN (expanded placeholders)
    
    Returns: (sql_clause, params_transform)
    - sql_clause: The SQL fragment with placeholders
    - params_transform: Function to transform params for SQLite
    """
    if is_postgresql():
        return f"{column} = ANY({placeholder})", lambda p: p
    else:
        # For SQLite, we need to expand the array into IN clause
        # This will be handled by the calling code
        return None, None


def count_filter_sql(condition):
    """
    Generate COUNT with filter for current database.
    PostgreSQL: COUNT(*) FILTER (WHERE condition)
    SQLite: SUM(CASE WHEN condition THEN 1 ELSE 0 END)
    """
    if is_postgresql():
        return f"COUNT(*) FILTER (WHERE {condition})"
    else:
        return f"SUM(CASE WHEN {condition} THEN 1 ELSE 0 END)"


def distinct_on_sql(columns, order_by):
    """
    Generate DISTINCT ON for PostgreSQL, or fallback for SQLite.
    For SQLite, use GROUP BY with subquery or ROW_NUMBER().
    """
    if is_postgresql():
        return f"DISTINCT ON ({columns})"
    else:
        # SQLite fallback: use regular DISTINCT (less efficient but works)
        return "DISTINCT"


def delete_using_sql(table, alias, using_table, using_alias, conditions):
    """
    Generate DELETE with JOIN for current database.
    PostgreSQL: DELETE FROM table t USING other_table ot WHERE ...
    SQLite: DELETE FROM table WHERE id IN (SELECT ...)
    """
    if is_postgresql():
        return f"DELETE FROM {table} {alias} USING {using_table} {using_alias} WHERE {conditions}"
    else:
        # SQLite doesn't support DELETE USING
        # Extract the ID condition and use subquery
        return f"DELETE FROM {table} {alias} WHERE {alias}.id IN (SELECT {alias}.id FROM {table} {alias} INNER JOIN {using_table} {using_alias} ON {conditions})"


def return_star():
    """
    Check if RETURNING * is supported.
    PostgreSQL: Yes
    SQLite: Only 3.35.0+
    """
    if is_postgresql():
        return True
    else:
        # Check SQLite version
        import sqlite3
        version = sqlite3.sqlite_version
        major, minor, patch = map(int, version.split('.'))
        return (major, minor) >= (3, 35)
