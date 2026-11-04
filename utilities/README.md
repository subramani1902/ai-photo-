# Optional utilities

Run these from the project root with the project environment activated. Use
`python -m` so the scripts can locate the `app` package and their paths remain
relative to the project root.

```powershell
.\s-venv\Scripts\Activate.ps1
python -m utilities.init_db
python -m utilities.reset_admin
python -m utilities.migrate_auth
python -m utilities.migrate_guest
python -m utilities.run_batch
python -m utilities.import_results
python -m utilities.validate_dataset
python -m utilities.save_qr
```

`init_db` creates missing tables; `reset_admin` changes an administrator
account. The migration scripts are intended for older databases. The batch,
import and validation scripts use the hard-coded `wedding_001` example paths;
they are separate from the active camera-folder watcher and online relay.
