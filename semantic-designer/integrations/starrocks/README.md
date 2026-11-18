# StarRocks integration test

This directory contains a real end-to-end test for the Query AST compiler. It
uses the official `starrocks/allin1-ubuntu:latest` image, creates the `mart`
tables, loads fixture rows, compiles the semantic query, executes the generated
SQL through StarRocks' MySQL protocol, and asserts the returned aggregates.

Requirements: Docker with at least 4 GB memory assigned. Run from the
`semantic-designer` directory:

```bash
bash integrations/starrocks/run.sh
```

GitHub Actions runs the same script on pull requests. The test is not satisfied
by a SQL snapshot: it proves that the generated query executes against a real
StarRocks engine and returns the expected values.
