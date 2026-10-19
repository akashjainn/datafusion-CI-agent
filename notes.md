# Notes

## Duplicates
- PR #26384: same root cause as df-003 (pinned arrow-rs `interleave` change).
  Not added to `cases.jsonl` (one case per root cause).
- PRs #26191 (run 38078282623) and #26194 (run 38112419216): both proposed;
  same error, `clippy::needless-range-loop` on loop variable `lane` indexing
  `new_hashes` in `datafusion/common/src/hash_utils.rs`, both fixed by a
  "fix clippy" commit to that file. Likely one root cause; keep one.
- PR #26384 is also in `cases_proposed.jsonl` (df-38790179545); reject it on
  review.
