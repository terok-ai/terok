# Launch integration tests

Launch and restart workflows exercised through the real `terok` CLI:

- `task run` in default CLI mode
- `task run --mode toad` browser-served TUI workflow
- `task restart` for previously created tasks

These tests use a lightweight fake `podman` shim so they can validate terok's
real CLI orchestration on normal host runners without requiring actual
container execution.
