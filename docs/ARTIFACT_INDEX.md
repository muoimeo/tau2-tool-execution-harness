# Final natural-run result checksums

The raw files below are local outputs and are not committed. This index lets
someone who receives them separately verify the exact `results.json` used by
the A0-A3 metric catalogue. Checksums are SHA-256 of the file bytes, before
any reformatting or migration. Counts refer to `simulations` rows, not planned
episodes or operational attempts.

| Track | Relative path under `data/simulations/` | Bytes | SHA-256 | Serialized episodes |
|---|---|---:|---|---:|
| A0 | `20_a0_native_baseline/a0-main/retail-stratified-58-v1/results/results.json` | 13,922,668 | `3b8bc7732dd9dcec4a17f6e8dc51449dbeb8c6c581823875ab577b9b1c5a7b09` | 232 |
| A1 | `30_a1_validation/a1-main/retail-stratified-58-v1-4trials-v1/results.json` | 13,001,785 | `8af8196d741ec47efcd4a635acb0902d7a193c12c6284b24a1467ba9c9e18ba5` | 232 |
| A2 | `40_a2_validation_retry/a2-main/retail-stratified-58-v1-4trials-v4-structural-observability/results/results.json` | 12,929,581 | `b8f526805d6fb3233bf2d9e0758a82a63f5c90d763ed83be816127f6ca3fc4f8` | 227 |
| A3 | `50_a3_validation_retry_recovery/a3-main/retail-stratified-58-v1-4trials-v3-per-logical-safe-failures/results/results.json` | 11,209,401 | `5c437780e36180b319dbabb7d7b843e03f005f4bc54b0815e750a730b2306d54` | 221 |

Windows PowerShell check from the repository root:

```powershell
Get-FileHash -Algorithm SHA256 -LiteralPath 'data/simulations/50_a3_validation_retry_recovery/a3-main/retail-stratified-58-v1-4trials-v3-per-logical-safe-failures/results/results.json'
```

This index covers the four benchmark result files only. It does not prove the
integrity of an independently supplied trace directory; verify its per-episode
lifecycle SHA/count/sequence fields as described in the architecture docs.
