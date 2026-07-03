# Test fixtures

17track v2.4 response payloads used by the unit suite:

| File | Covers |
|---|---|
| `register_mixed.json` | Mixed `accepted` + `rejected` register response (Correios 2151 accepted, `-18010012` rejected) |
| `register_already_registered.json` | `-18019901` already-registered rejection |
| `gettrackinfo_correios_2151.json` | Delivered Correios package; `Delivered_Other` with delivery time only in `time_raw` |
| `gettrackinfo_yanwen_190012.json` | In-transit YanWen package; one event with an unknown future sub-status; rejected item with string carrier |
| `gettrackinfo_exception_returning.json` | `Exception` / `Exception_Returning` payload |
| `gettracklist_page.json` | Paged list response (`page` is a top-level sibling of `data`); Tracking and Stopped items |
| `getquota.json` | Quota response (verbatim doc example) |
| `webhook_tracking_stopped.json` | `TRACKING_STOPPED` webhook payload (verbatim doc example) |

**Provenance:** synthetic, shaped field-for-field after the official v2.4 doc
examples (captured 2026-07-03). Swap in real captured responses
(Correios 2151, YanWen 190012, an Exception_Returning payload) when a live
account is available — the opt-in live suite (`pytest -m live`) is the
natural capture point. A recorded raw webhook body + its real `sign` header
would also upgrade the signature tests from doc-derived to byte-exact-real.
