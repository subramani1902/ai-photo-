# Camera arrival and person groups

Run commands from the project root. The local operator has access to the event
database and original photos; the importer is not a public API.

Start the camera importer:

```powershell
.\s-venv\Scripts\python.exe -m app.services.camera_importer --folder sample_photos --event-id 2
```

Use either this importer or the older HTTP folder watcher, not both for the
same folder. The importer waits for a file to stop changing for five seconds,
then uses the normal ingestion pipeline: duplicate check, face detection,
saved face vectors, person assignments, and an event search-index rebuild.
Keep the online relay worker running to answer website searches.

New tables are created at backend startup without altering existing tables.
Each person group is local to one event and has an anonymous numeric label.
Group photographs link independently to each detected person's group.
Assignments use a fixed exemplar, similarity threshold 0.65 and ambiguity
margin 0.05. These are initial settings, not measured accuracy guarantees.
Uncertain matches start separate groups. Two faces in the same photograph
cannot enter the same group. Invalid vectors remain ungrouped and are retried
by the next sync. Existing assignments are not automatically merged.

Admin endpoints (event ownership required):

- POST /api/events/{event_id}/people/sync — group previously indexed faces.
- GET /api/events/{event_id}/people — person IDs and linked-photo counts.
- GET /api/events/{event_id}/people/{person_id}/photos — linked photos.

Guest results continue to require a direct face match; they are not expanded
to all photos in a person group. Restarting the importer scans the folder
again, but duplicate photos are skipped without running face detection again.
An index-rebuild failure is logged; index recovery remains an operator task.

Verification:

```powershell
.\s-venv\Scripts\python.exe -m utilities.test_person_groups
```
