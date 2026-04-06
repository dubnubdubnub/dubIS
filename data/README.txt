dubIS Data Directory
====================

CSV files are the source of truth — do not edit manually, use the app.

  purchase_ledger.csv   Raw purchase import history (append-only)
  adjustments.csv       Stock adjustment history (append-only)

cache.db is a derived SQLite cache. It can be safely deleted — it will
be rebuilt from the CSV files on next app startup.

  preferences.json      User configuration (thresholds, directories)
  constants.json        Shared schema (field names, section order)
