You are proving another agent's testability contract on this machine. Follow it literally — the contract only counts when its own words work without insider knowledge.

From the fresh checkout, run the contract's Run recipe at the highest fidelity this machine supports (docker over local), wait as Health specifies, and confirm the health signal. Then shut everything down: no processes or containers left running.

Do not modify product code or the contract. When a step fails, capture the exact command and its output. Save useful evidence under `.hive/artifacts/`.

Structured result:

- `fidelity`: the fidelity you actually ran (`local` or `docker`).
- `problems`: concrete failures ("step X failed: <output>"), empty when everything worked.
- `evidence_blobs`: evidence filenames.

End with `TESTABILITY_PROBE: OK` when the app came up healthy exactly as written, else `TESTABILITY_PROBE: FAIL`.
