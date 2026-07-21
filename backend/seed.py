"""Retired destructive demo seeder.

Schema changes use checksum-verified migrations. Test fixtures belong only in
explicitly disposable databases.
"""

raise SystemExit(
    "The destructive demo seeder is retired. Use backend.scripts.migrate and explicit provisioning."
)
