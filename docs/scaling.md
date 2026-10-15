# Scaling

The catalog is built to grow by adding sources and rebuilding indexes, not by loading every skill into an agent prompt or into application memory.

What is already incremental:

- Discovery and import process one repository or skill at a time and checkpoint completed items.
- The same skill path updates a single row. Instruction checksums mark duplicates without a second logical copy in search.
- Full-text indexing uses SQLite FTS5 and commits in batches. Rebuild reads one skill file at a time.
- Search uses `LIMIT` and `OFFSET`. Callers cannot request more than `search_max_limit` rows, which defaults to 100.
- Source errors and import history are append-only JSONL files.
- Synthetic pagination tests insert rows marked `is_synthetic=1` in a disposable database. Normal search hides them. They are not imported skills and are not a collection milestone.

What this version does not do:

- It does not create millions of catalog rows to demonstrate scale.
- It does not claim that 12 million skills are available from current public sources. Many repositories are not skills, and GitHub search returns at most 1000 hits per query.
- It does not embed skill text or call a paid API.

SQLite is the right default while one process owns the catalog and the working set is metadata plus modest instruction text. Revisit that choice when one of these becomes true:

- Several writers need to import into the same catalog at once.
- Full-text data no longer fits comfortably on the disk you want to use, or rebuild time becomes unacceptable.
- You need distributed search across machines.
- You enable semantic search over a corpus large enough that a dedicated vector index is cheaper than scanning stored vectors in SQLite.

Those are reasons to plan a backend change. They are not reasons to add one before the stored collection and the measurements justify it. Raise `max_repos_per_run`, `max_skills_per_repo`, and related limits in `config/settings.yaml` when a specific import needs more room. The defaults are intentionally small.

`python tools/build_index.py` prints file counts and byte sizes for `skills/`, `quarantine/`, and `index/`.
