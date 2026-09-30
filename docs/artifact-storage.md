# Artifact storage and the asset mirror — ops contract

This is the single ops-facing page for the two storage capabilities added in 3.2.0. Both are off by
default: a node with no new configuration behaves exactly like 3.1.0, except that every image response
now carries one extra header, `X-Haruki-Node`.

| capability | switch | default | what it does |
| --- | --- | --- | --- |
| Artifact output (write side) | `HARUKI_STORAGE__ENABLED` | `false` | When a request carries `X-Haruki-Artifact: 1` plus a valid directive, the rendered bytes are uploaded to the `image-cache` bucket and an `artifact_ref` JSON document is returned instead of the image. |
| Asset mirror (read side) | `HARUKI_ASSETS__SOURCE` | `local` | `mirror` fetches `asset/<region>-assets/<mode>/...` files on demand from the `pjsk-assets` bucket into a local, manifest-versioned directory instead of requiring a pre-rsynced tree. |
| User-upload store (read side, 3.3.0) | `HARUKI_ASSETS__USER_UPLOAD__ENABLED` | `false` | Reads a profile background whose `bg_settings.img_path` is `user_upload/profile_bg/<server>/<file>` from the `user-upload` bucket (the key Cloud's `ProfileBGStore` writes) instead of `<base_dir>/user_upload/...`; a miss falls back to the local file, then to the default background. |

Haruki-Cloud owns the PostgreSQL schema, the garbage collector and the choice of which node's public
hostname goes into a URL. Drawing uploads objects, coordinates index writes with Cloud through per-content transaction locks,
registers/removes durable upload intents, and returns refs only after index commit. Drawing never deletes bucket objects.

## 1. Request headers (the render cache directive)

Bytes mode is the default. When `X-Haruki-Artifact` is absent or `0`, **no other `X-Haruki-*` header is
read**. When it is `1`, every directive header is validated strictly.

| header | required | validation |
| --- | --- | --- |
| `X-Haruki-Artifact` | — | absent or `0` → bytes mode; `1` → artifact mode; anything else → 400 |
| `X-Haruki-Cache-Key` | yes | `^[0-9a-f]{16,128}$` |
| `X-Haruki-Cache-TTL` | yes | integer seconds, `0 <= ttl <= storage.ttl_max_seconds` (default 30 days); `0` means infinite. Cloud clamps to the same cap, so a 400 here is a Cloud bug. |
| `X-Haruki-Cache-Key-Version` | yes | integer, `0..10000` |
| `X-Haruki-Api-Path` | yes | one leading `/` stripped, then `^[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+){0,15}$`, at most 128 characters, no `.` or `..` segment |
| `X-Haruki-Cache-Store` | no, default `1` | `0` or `1`. `0` = render and return bytes, store nothing. |
| `X-Haruki-Cache-Group` | no, default `pjsk` | `^[A-Za-z0-9._-]{1,64}$`; index metadata only, never part of an object key |
| `X-Haruki-User-Id` | no, default `public` | `^[A-Za-z0-9._-]{1,128}$`; index metadata only, never part of an object key |
| `X-Haruki-Asset-Revision` | no | 64 lowercase hex characters; global game-asset revision, used as the mirror namespace |
| `X-Haruki-Renderer-Epoch` | no | 64 lowercase hex characters; expected backend identity from `/cache/identity` |

Cloud always sends the **full** directive in artifact mode, including for endpoints it deliberately does
not cache (those carry `X-Haruki-Cache-Store: 0`). There is no `Accept` negotiation and no
`X-Haruki-Assets-Mode` header.

An invalid directive is rejected by the debug middleware **before the route runs**:

```
HTTP/1.1 400
X-Haruki-Directive-Error: X-Haruki-Cache-TTL
X-Haruki-Node: cn09
{"detail": "invalid X-Haruki-Cache-TTL: too_large", "header": "X-Haruki-Cache-TTL", "code": "too_large"}
```

`code` is a stable slug (`missing`, `malformed`, `too_large`, `too_long`, `dot_segment`). Cloud logs this at
Error level and counts `drawing_directive_rejected`; Drawing counts it under
`/render-stats["artifacts"]["directive_rejected"][<header>]`.

## 2. Response shapes

Every response path leaves through one exit and is always **one** body.

| branch | when | status / body | headers added |
| --- | --- | --- | --- |
| bytes | no directive | 200, `image/png` or `image/jpeg`, identical to 3.1.0 | `X-Haruki-Node` |
| store 0 | explicitly disabled, missing assets (including bytes mode), or mismatched renderer epoch | 200, image bytes; nothing uploaded, no index write | `X-Haruki-Cache-Store: 0`, `X-Haruki-Node` |
| artifact | artifact mode, store 1, upload and index transaction committed | 200, `application/json` `artifact_ref` | `X-Haruki-Artifact: 1`, `X-Haruki-Node` |
| degraded | artifact mode, store 1, upload not possible | 200, image bytes | `X-Haruki-Artifact-Degraded: 1`, `X-Haruki-Node` |
| rejected | invalid directive | 400 JSON (above) | `X-Haruki-Directive-Error`, `X-Haruki-Node` |

`X-Haruki-Node` is on **every** response. Its value is `storage.node_name`, or the host name when that is
empty. Cloud uses `ref.node_name` on the artifact branch and this header on the bytes and degraded
branches, where there is no ref.

### Degraded rules

A storage problem never loses a render. The bytes are returned with `X-Haruki-Artifact-Degraded: 1`, no index
row is written, and one counter under `artifacts.degraded` is incremented:

| reason | cause |
| --- | --- |
| `disabled` | `HARUKI_STORAGE__ENABLED=false` but the caller asked for an artifact |
| `runtime_unavailable` | provider config invalid or the `opendal` operator could not be built; retried after `storage.index.connect_retry_seconds` without a restart |
| `index_unavailable` | missing/disabled index, lock/intent failure, or index transaction failure; no unlocked upload is attempted |
| `upload_failed` | the object store rejected the write, or it was unreachable |
| `upload_timeout` | the write exceeded `storage.upload_timeout_seconds` |
| `unsupported_media` | the payload media type is not `image/png` or `image/jpeg` |
| `internal` | an unexpected exception inside the artifact service (logged with a traceback) |

Index trouble degrades to bytes. An object write requires a durable upload intent and the shared PostgreSQL
content lock. No ref is returned until both index rows commit; failed/aborted writes retain their cleanup intent
for Cloud GC. `index_written=false` remains accepted by older consumers but is not emitted by this writer.
Missing assets and renderer-epoch mismatches return bytes with `X-Haruki-Cache-Store: 0`, including images
returned without an artifact directive. Callers must not cache those bytes or a derived ref.

`/ready` is never influenced by storage or index health.

## 3. `artifact_ref` — the 17 fields

```json
{
  "kind": "artifact_ref",
  "hash": "a06578ec…32bd",
  "cdn_path": "pjsk/api/pjsk/honor/a06578ec…32bd.png",
  "storage_backend": "garage",
  "bucket": "image-cache",
  "object_key": "pjsk/api/pjsk/honor/a06578ec…32bd.png",
  "size_bytes": 7224,
  "media_type": "image/png",
  "width": 134,
  "height": 176,
  "cache_key": "98c9bb4e…02ca",
  "ttl_seconds": 3600,
  "expires_at": "2026-09-14T07:55:22Z",
  "reused": false,
  "index_written": true,
  "upload_elapsed": 0.0052,
  "node_name": "cn09"
}
```

| field | meaning |
| --- | --- |
| `kind` | always `artifact_ref` |
| `hash` | sha256 hex of the image bytes |
| `cdn_path` | object key, identical to `object_key`; the path Cloud appends to a node's public `image-cache` host |
| `storage_backend` | always `garage` from Drawing (`legacy_disk` exists only in Cloud's own rows) |
| `bucket` | the configured image-cache bucket |
| `object_key` | on a miss, `pjsk/<api_path>/<sha256>-<generation>.<ext>`; on a reuse, the stored row's `cdn_path` |
| `size_bytes`, `media_type` | from the payload on a miss; from the stored row on a reuse when non-NULL |
| `width`, `height` | may be `null` (Cloud reads null as 0) |
| `cache_key`, `ttl_seconds` | echoed from the directive |
| `expires_at` | RFC 3339 UTC (`…Z`), or JSON `null` when `ttl_seconds == 0` (infinite) |
| `reused` | `true` when an existing `garage` row with the same hash was found and the upload was skipped |
| `index_written` | `true` only when both index rows were written |
| `upload_elapsed` | seconds spent in the upload step; `0.0` when reused |
| `node_name` | original writer when its persisted `written_at` is within 120 seconds; otherwise the rendering node |

### Object keys, the reuse rule, and the `pjsk/api/` ops prefix

- **Fresh upload (miss):** the key is `pjsk/<api_path>/<sha256>-<generation>.<ext>`. The first segment is the literal
  `pjsk` (not the cache group), and `user_id` and `cache_key` never appear in it. Because Cloud's API paths
  start with `api/`, Drawing-written keys start with `pjsk/api/`.
- **Reuse (hit):** Drawing dedups by `image_cache_entries.hash`. For a hit whose `storage_backend` is
  `garage`, the ref copies `cdn_path`, `bucket`, `object_key`, `media_type` and `size_bytes` from the stored row
  and **never recomputes the key**. Two consequences follow:
  1. The same bytes rendered by two endpoints keep the **first** endpoint's key, so a ref's `cdn_path` may
     name a different `api_path` than the request did.
  2. Cloud's own `imagecache.storeHashed` rows (`pjsk/<sha256>.<ext>`) share the table and the bucket and may be
     reused by Drawing. Such hits are counted `reused_foreign`.
- A new random generation protects a replacement from a delayed DELETE of an older object with the same content hash.
- GC deletes only recorded paths. A separately committed upload intent covers objects left behind by interrupted writes.
- **Ops tooling that must target only Drawing artifacts uses the prefix `pjsk/api/`, never `pjsk/`.**
  `pjsk/` also matches Cloud's own `pjsk/<sha256>.<ext>` objects.

Neither Drawing nor Cloud sets `Cache-Control` on a PUT, only `Content-Type`. Node Caddy owns caching headers:
`public, max-age=31536000, immutable` for the digest-addressed `image-cache` bucket, and `max-age=2592000` plus
ETag/Last-Modified passthrough for asset paths. Both public buckets allow GET/HEAD only, with no listing.

## 4. Configuration

Environment variables use the `HARUKI_` prefix and `__` for nesting, and env beats `configs.yaml`. List and
dict fields (`options`) are JSON strings in env. Every setting in `src/settings.py` follows this rule. The table
lists the ones ops is expected to touch.

### Provider block vocabulary (shared with Asset-Updater and Haruki-Cloud)

| canonical key | accepted alias | notes |
| --- | --- | --- |
| `provider` | `name` | label for logs/stats only |
| `scheme` | `kind` | `fs` \| `s3` (\| `memory`, Drawing-only, tests and smoke). `local` is accepted as a value alias of `fs`. The vocabulary default is `fs`, but both Drawing slots default to `s3`. |
| `endpoint` | — | `host[:port]` or a full URL (`tls` decides the scheme of a bare host). A Cloud-style `endpoints: [...]` list logs a WARNING and its first element fills an empty `endpoint`. |
| `bucket` | — | `{region}` templated |
| `root` | `prefix` (legacy) | **must be `""` on both Drawing slots** (see below) |
| `region` | — | default `garage` |
| `access_key_id` | `access_key` | secret; env only in practice |
| `secret_access_key` | `secret_key` | secret; env only in practice |
| `base_url` | `public_base_url` | accepted for parity; Drawing never reads it (Cloud picks the node host) |
| `public_read` | — | `s3` writes with `default_acl=public-read` |
| `path_style` | — | default `true` (Garage) |
| `options` | — | raw opendal options; they **win** over every derived key |

Unknown keys in a provider block log `settings.provider_unknown_key` at WARNING and are ignored.

**`root=""` on both slots.**
- **Image cache:** `storage.provider.root` must stay empty. Cloud reads `artifacts.Get(ref.cdn_path)` against
  the same bucket, so a non-empty root would put objects under a prefix Cloud never looks in. Startup logs a
  WARNING, and the root is ignored for artifact keys.
- **Assets:** `assets.mirror.provider.root` must stay empty. The `<region>-assets` segment lives in the
  object key (`<region>-assets/<mode>/<rel>`), not in `root`. The mirror layer strips only the leading `asset/`
  from the logical key Cloud sends. It is the only strip point in this repository.

### Artifact output

| env | default | notes |
| --- | --- | --- |
| `HARUKI_STORAGE__ENABLED` | `false` | the unilateral rollback switch |
| `HARUKI_STORAGE__NODE_NAME` | `""` → host name | `ref.node_name` and `X-Haruki-Node` |
| `HARUKI_STORAGE__PROVIDER__SCHEME` | `s3` | `memory` for smoke tests only |
| `HARUKI_STORAGE__PROVIDER__ENDPOINT` | `""` | tailnet address of the node-local Garage S3 API |
| `HARUKI_STORAGE__PROVIDER__BUCKET` | `image-cache` | |
| `HARUKI_STORAGE__PROVIDER__ROOT` | `""` | **must stay empty** |
| `HARUKI_STORAGE__PROVIDER__REGION` | `garage` | |
| `HARUKI_STORAGE__PROVIDER__ACCESS_KEY_ID` | — | secret |
| `HARUKI_STORAGE__PROVIDER__SECRET_ACCESS_KEY` | — | secret |
| `HARUKI_STORAGE__PROVIDER__PATH_STYLE` | `true` | |
| `HARUKI_STORAGE__PROVIDER__OPTIONS` | `{}` | JSON |
| `HARUKI_STORAGE__TTL_MAX_SECONDS` | `2592000` | `X-Haruki-Cache-TTL` cap |
| `HARUKI_STORAGE__UPLOAD_TIMEOUT_SECONDS` | `8.0` | total budget per request; must stay below the 10 s stuck-request watchdog |
| `HARUKI_STORAGE__UPLOAD_IO_TIMEOUT_SECONDS` | `4.0` | per I/O operation |
| `HARUKI_STORAGE__UPLOAD_RETRIES` | `1` | transport retry only; a render is never retried |
| `HARUKI_STORAGE__UPLOAD_CONCURRENCY` | `4` | concurrent uploads, separate from the render pool |
| `HARUKI_STORAGE__HASH_IN_POOL_MIN_BYTES` | `262144` | sha256 runs on the thread pool above this size |
| `HARUKI_STORAGE__INDEX__ENABLED` | `true` | sub-switch; an empty DSN also disables the index (requests return image bytes without uploading) |
| `HARUKI_STORAGE__INDEX__DSN` | — | **env only**; a YAML value is dropped with `settings.index_dsn_ignored` |
| `HARUKI_STORAGE__INDEX__POOL_MIN_SIZE` / `__POOL_MAX_SIZE` | `1` / `4` | per event loop |
| `HARUKI_STORAGE__INDEX__POOL_MAX_INACTIVE_SECONDS` | `0` | idle time after which a pooled connection is closed; `0` = never. asyncpg's own default is 300 s, which made a quiet worker reconnect (~4 round trips) on its next write. It applies to every pooled connection, so an idle loop keeps as many connections as its peak concurrent writes, at most `POOL_MAX_SIZE` |
| `HARUKI_STORAGE__INDEX__CONNECT_TIMEOUT_SECONDS` | `2.0` | |
| `HARUKI_STORAGE__INDEX__COMMAND_TIMEOUT_SECONDS` | `2.0` | |
| `HARUKI_STORAGE__INDEX__CONNECT_RETRY_SECONDS` | `30.0` | backoff after a failed connect or preflight |

### Asset mirror

| env | default | notes |
| --- | --- | --- |
| `HARUKI_ASSETS__SOURCE` | `local` | `local` \| `mirror` |
| `HARUKI_ASSETS__MANIFEST_VERSION` | — | version path segment; see precedence below |
| `HARUKI_ASSETS__MIRROR__DIR` | `mirror` | relative to `assets.base_dir`; absolute paths and `..` are rejected |
| `HARUKI_ASSETS__MIRROR__PROVIDER__SCHEME` | `s3` | |
| `HARUKI_ASSETS__MIRROR__PROVIDER__ENDPOINT` | `""` | |
| `HARUKI_ASSETS__MIRROR__PROVIDER__BUCKET` | `pjsk-assets` | |
| `HARUKI_ASSETS__MIRROR__PROVIDER__ROOT` | `""` | **must stay empty** |
| `HARUKI_ASSETS__MIRROR__PROVIDER__REGION` | `garage` | |
| `HARUKI_ASSETS__MIRROR__PROVIDER__ACCESS_KEY_ID` / `__SECRET_ACCESS_KEY` | — | secret |
| `HARUKI_ASSETS__MIRROR__PROVIDER__PATH_STYLE` | `true` | |
| `HARUKI_ASSETS__MIRROR__PROVIDER__OPTIONS` | `{}` | JSON |
| `HARUKI_ASSETS__MIRROR__MANIFEST_VERSION` | `v0` | |
| `HARUKI_ASSETS__MIRROR__MANIFEST_VERSION_FILE` | — | optional per-node file; its first line has top precedence |
| `HARUKI_ASSETS__MIRROR__MANIFEST_VERSION_POLL_SECONDS` | `30` | |
| `HARUKI_ASSETS__MIRROR__LOCAL_FALLBACK` | `true` | on a mirror miss or failure, try today's `<base>/asset/...` path |
| `HARUKI_ASSETS__MIRROR__FETCH_TIMEOUT_SECONDS` | `5.0` | total budget per fetch, retries included |
| `HARUKI_ASSETS__MIRROR__FETCH_IO_TIMEOUT_SECONDS` | `5.0` | |
| `HARUKI_ASSETS__MIRROR__FETCH_RETRIES` | `1` | |
| `HARUKI_ASSETS__MIRROR__FETCH_CONCURRENCY` | `8` | |
| `HARUKI_ASSETS__MIRROR__FETCH_MAX_BYTES` | `67108864` | larger objects are refused |
| `HARUKI_ASSETS__MIRROR__NEGATIVE_TTL_SECONDS` | `60.0` | NotFound memo; `0` disables |
| `HARUKI_ASSETS__MIRROR__NEGATIVE_MEMO_MAX` | `32768` | |
| `HARUKI_ASSETS__MIRROR__BREAKER_FAILURES` | `5` | consecutive transport failures before the breaker opens |
| `HARUKI_ASSETS__MIRROR__BREAKER_OPEN_SECONDS` | `30.0` | |
| `HARUKI_ASSETS__MIRROR__MAX_BYTES` | `8589934592` | current-version byte cap; `0` = unlimited |
| `HARUKI_ASSETS__MIRROR__MAX_ENTRIES` | `200000` | current-version entry cap; `0` = unlimited |
| `HARUKI_ASSETS__MIRROR__VERSIONS_KEEP` | `2` | old version directories kept by the sweeper |
| `HARUKI_ASSETS__MIRROR__SWEEP_INTERVAL_SECONDS` | `600` | |
| `HARUKI_ASSETS__MIRROR__TMP_MAX_AGE_SECONDS` | `3600` | |

**Manifest-version precedence:** `manifest_version_file` (first line) → `HARUKI_ASSETS__MANIFEST_VERSION` →
`HARUKI_ASSETS__MIRROR__MANIFEST_VERSION` → `v0`. The on-disk layout is
`<base_dir>/<mirror.dir>/<version>/<region>-assets/<mode>/<rel>`, so bumping the version invalidates the mirror
tree and every fragment and raster key that names a mirrored asset. Producing the value is an Asset-Updater
follow-up. Until that exists, it is a static env value, and a bump needs a restart on each node. Two nodes can
briefly sit on different versions during a rollout.

Custom-profile directories (`<base>/asset/<cc>-assets/startapp/custom_profile`) are **never** mirrored and must
stay rsynced. With `source=mirror`, startup logs a WARNING that names every missing one.

### User-upload store (profile backgrounds)

Cloud's `ProfileBGStore` persists `bg_settings.img_path` as `user_upload/profile_bg/<server>/uid_<id>_<hex>.jpg`
(older rows: `binding_<id>[_<hex>].jpg` under the same directory) and sends it to `/api/pjsk/profile` verbatim.
Drawing has always resolved it under `assets.base_dir` (`<base_dir>/user_upload/profile_bg/...`, a leading `/` is
tolerated, traversal is rejected). Cloud now writes the same bytes to the `user-upload` bucket under exactly that
relative path as the object key, and this slot lets Drawing read it from there.

| env | default | notes |
| --- | --- | --- |
| `HARUKI_ASSETS__USER_UPLOAD__ENABLED` | `false` | off = today's local read, byte for byte |
| `HARUKI_ASSETS__USER_UPLOAD__PROVIDER__SCHEME` | `s3` | |
| `HARUKI_ASSETS__USER_UPLOAD__PROVIDER__ENDPOINT` | `""` | tailnet address of the node-local Garage S3 API |
| `HARUKI_ASSETS__USER_UPLOAD__PROVIDER__BUCKET` | `user-upload` | |
| `HARUKI_ASSETS__USER_UPLOAD__PROVIDER__ROOT` | `""` | on `s3` a value (or legacy `prefix`) is **dropped** by the settings validator with `settings.user_upload_root_dropped` — opendal would otherwise prefix every key (`user_upload/user_upload/...`, all NotFound); on `fs` it is the directory that holds `user_upload/profile_bg/...` and is kept |
| `HARUKI_ASSETS__USER_UPLOAD__PROVIDER__REGION` | `garage` | |
| `HARUKI_ASSETS__USER_UPLOAD__PROVIDER__ACCESS_KEY_ID` / `__SECRET_ACCESS_KEY` | — | secret (a read-only key is enough) |
| `HARUKI_ASSETS__USER_UPLOAD__PROVIDER__PATH_STYLE` | `true` | |
| `HARUKI_ASSETS__USER_UPLOAD__PROVIDER__OPTIONS` | `{}` | JSON |
| `HARUKI_ASSETS__USER_UPLOAD__FETCH_TIMEOUT_SECONDS` | `3.0` | **total wall-clock budget per read** — one `asyncio.wait_for` around HEAD + GET and any retry; a timeout counts as a transport failure |
| `HARUKI_ASSETS__USER_UPLOAD__FETCH_IO_TIMEOUT_SECONDS` | `2.0` | per opendal operation (HEAD or GET), clamped to the total budget |
| `HARUKI_ASSETS__USER_UPLOAD__FETCH_RETRIES` | `1` | |
| `HARUKI_ASSETS__USER_UPLOAD__FETCH_CONCURRENCY` | `8` | |
| `HARUKI_ASSETS__USER_UPLOAD__FETCH_MAX_BYTES` | `2097152` | Cloud caps an upload at 1 MiB; larger objects are refused |
| `HARUKI_ASSETS__USER_UPLOAD__BREAKER_FAILURES` | `5` | consecutive transport failures/timeouts (NotFound and TooLarge are answers, a cancelled probe counts as nothing) before the bucket is skipped |
| `HARUKI_ASSETS__USER_UPLOAD__BREAKER_OPEN_SECONDS` | `30.0` | skip window; the first read after it is the single half-open probe (success closes, failure re-opens) |
| `HARUKI_ASSETS__USER_UPLOAD__CACHE_SIZE` / `__CACHE_MAX_MB` / `__CACHE_TTL_SECONDS` | `64` / `32` / `300` | in-memory cache of the encoded bytes keyed by object key; any `0` disables it |
| `HARUKI_ASSETS__USER_UPLOAD__LOCAL_FALLBACK` | `true` | after a bucket miss or failure, still try `<base_dir>/user_upload/...` (keep on while Cloud dual-writes) |

**Resolution order with the store enabled.** `img_path` is mapped to an object key by locating its
`user_upload/profile_bg/` segments: the canonical relative path, one with a leading `/` or `./`, backslashes, or an
absolute host path such as `/pjskdata/Data/user_upload/profile_bg/jp/uid_1_ab.jpg` all map to
`user_upload/profile_bg/jp/uid_1_ab.jpg`. The key must be exactly `user_upload/profile_bg/<server>/<file>` with
`<server>` two to four lowercase letters and `<file>` what Cloud's writer produces (`uid_<id>_<8hex>.jpg`, or the
pre-2026-04 `binding_<id>[_<8hex>].jpg`). Only a `..` segment or a NUL byte rejects the path outright (default
background, WARNING `profile.bg_rejected`). Every other path — outside the namespace, or inside it with a shape the
bucket cannot hold (INFO `profile.bg_not_a_bucket_key`) — is not a bucket key and keeps the local read. Per key the order is: in-memory cache → circuit breaker (open: skip the bucket, DEBUG
`user_upload.breaker_skip`) → single-flight (concurrent first reads of one key on one loop share one request) →
the bounded read. A bucket miss logs `user_upload.not_found` and an oversized object `user_upload.too_large` — both are
answers and reset the breaker; a transport failure or timeout logs `user_upload.read_failed` and counts toward it (`user_upload.breaker_open` at WARNING when it trips,
`user_upload.breaker_closed` at INFO when a probe succeeds). Every `None` then tries the local file (if
`local_fallback`) and finally the default background. Worst case per request during a Garage outage is one
`fetch_timeout_seconds` budget until the breaker opens, then zero. The route never answers 5xx for a background
problem. The store shares nothing with the asset mirror: no file is materialised, the bytes go to the renderer as an
encoded in-memory image, and the object store is closed at lifespan shutdown.

**Rollout.** Enable after Cloud's `user_upload` slot points at Garage (Cloud release R6) and the existing
`user_upload/profile_bg/**` tree has been backfilled with `rclone`; keep `local_fallback=true` for the dual-write
window. Rollback is `HARUKI_ASSETS__USER_UPLOAD__ENABLED=false`.

## 5. Counters

`GET /render-stats` → `artifacts`:

| key | meaning |
| --- | --- |
| `enabled`, `node_name`, `bucket` | runtime identity |
| `index.configured`, `index.usable`, `index.last_error` | index state (`last_error` is `{ts, stage, exc}`) |
| `requests_with_directive` | requests that bound a valid directive |
| `bytes_no_directive` | image responses sent without a directive |
| `store_skipped` | artifact-mode requests with `Cache-Store: 0` |
| `published`, `reused`, `reused_foreign` | refs returned; hash hits; hits on a row not under `pjsk/api/` |
| `uploads`, `upload_bytes`, `upload_elapsed_total`, `upload_failures`, `upload_timeouts` | object writes |
| `index_lookups`, `index_lookup_hits`, `index_lookup_errors` | content-hash lookups |
| `index_writes`, `index_write_failures` | two-row index writes |
| `index_skipped.{disabled,schema_missing,unavailable}` | index disabled/backoff decisions before safe artifact publication |
| `degraded.{disabled,runtime_unavailable,upload_failed,upload_timeout,unsupported_media,internal}` | bytes returned instead of a ref |
| `directive_rejected.<header>` | 400s by offending header |
| `stages.{hash,index_prepare,index_lookup,index_lock,upload,index_connect,index_acquire,index_write,total}.{count,total}` | seconds per stage; connect/acquire timings are contained by their parent operation, and total is the whole artifact step |
| `last_error` | last artifact error `{ts, stage, exc}` |

`GET /cache/stats` → `asset_mirror`: `enabled`, `source`, `disabled_reason`, `manifest_version`, `provider`,
`bucket`, `local_hits`, `fetches`, `fetch_bytes`, `fetch_elapsed_total`, `remote_miss`, `fetch_errors`,
`too_large`, `negative_memo_hits`, `local_fallback_hits`, `skipped_on_loop`, `single_flight_waits`,
`breaker_open`, `breaker_trips`, `breaker_skips`, `version_changes`, `dir_entries`, `dir_bytes`, `sweeps`,
`evicted_entries`, `evicted_bytes`, `versions_removed`.

`GET /cache/stats` → `missing_assets`: `total` and `by_reason.{empty_path, local_not_found, mirror_not_found,
mirror_fetch_error, mirror_breaker_open, candidates_exhausted, birthday_fallback, vanished}`.

Every image response also logs one `image.response` line with `artifact=0|1|store0|degraded`,
`missing_assets=N`, and, depending on the branch, `hash= reused= index_written= upload=` or `reason=`. Artifact and degraded lines include a `stages=` breakdown for the request.

## 6. Index (PostgreSQL) — Cloud owns the DDL

Drawing never ships or runs schema changes. Cloud's `renderIndexDDL` is the canonical text: it widens
`image_cache_entries` (`storage_backend` nullable with a one-shot `legacy_disk` backfill, `media_type`,
`expires_at`, `last_referenced_at`, nullable `file_path`) and creates `render_cache_index` (`request_key` PK,
`content_hash` FK to `image_cache_entries(hash)`, `api_path`, `user_id`, `group_name`, `key_version`,
`ttl_seconds`, `expires_at`, `created_at`, `last_used_at`).

The schema also contains `image_cache_entries.writer_node`, `written_at` (both nullable) and
`image_cache_object_deletions(content_hash, cdn_path, queued_at, attempts, next_attempt_at)`, keyed by
`(content_hash, cdn_path)`. The test-only schema fixture mirrors Cloud's canonical DDL; Drawing runtime only
checks/uses the schema. Missing columns/tables cause bytes degradation and bounded reconnect backoff.

The writer performs these steps in order:

1. Generate a unique candidate object key and independently commit an upload cleanup intent with
   `next_attempt_at = now() + interval '5 minutes'` (`ON CONFLICT DO NOTHING`). This does not borrow a
   second pool connection while another transaction is open, so a pool of size one works.
2. Begin one transaction and execute
   `SELECT pg_advisory_xact_lock(hashtextextended($1, 0))`, where `$1` is the lowercase SHA-256 content hash.
3. Look up the hash under that lock. Reuse an existing Garage row's recorded path and original writer/time.
   On a miss, lock/check the candidate intent is unclaimed (`attempts=0`) and unexpired before uploading.
4. Upsert content and request rows in one `RECORD` statement, remove the candidate intent, then commit before returning a ref.
   Failures/cancellation roll back the index transaction and leave the independently committed intent.

Pooled connections retain the warm minimum and skip session reset: only transaction-scoped locks are used, and asyncpg still rolls back interrupted transactions. The single-statement write is checked against the equivalent two-upsert transaction by `test_index_record_pg.py`. Unlike the earlier uncoordinated writer, the complete operation now includes lock, lookup and durable intent round trips; its latency must be measured after rollout.

The complete transaction budget is capped at four minutes, below the five-minute intent grace period.
The content upsert preserves an existing Garage row's path, media, size and writer/time, while refreshing
`last_referenced_at` and merging `expires_at` with NULL meaning infinite.

## 7. Garbage collection — owned by Cloud

Cloud expires request keys, then finds unreferenced content beyond the configured retention window.
It uses the same per-hash advisory lock as Drawing. In one transaction it rechecks references, removes the
content row and queues its recorded path durably. In a later transaction it rechecks the live path under the
same lock before deleting the object and acknowledging the queue entry. Failed deletion stays in the queue
for retry, including after a process restart. Upload intents use this same queue after their grace period.

`image_cache_entries.expires_at` is not the object lifetime signal: `last_referenced_at` is the retention clock,
and a live render-key reference protects the content. Cloud's object-deletion switch must remain disabled
until every object writer uses this lock/intent protocol. Drawing itself never deletes remote objects.

## 8. Release order (hard)

1. **Cloud read side:** `PGStore.Lookup` tolerates `file_path IS NULL`. Cloud handles "asked for an artifact,
   got `image/*` bytes" forever (Cache-Store 0, degraded writes, un-upgraded Drawing nodes).
2. **Cloud DDL** ships and runs.
3. **Drawing:** set `HARUKI_STORAGE__ENABLED=true` with the `image-cache` provider and the index DSN.

Before step 2, keep `enabled=false`. A missing/old schema returns bytes without an unsafe upload.
Rolling back is `HARUKI_STORAGE__ENABLED=false`; Cloud handles the bytes response. Enable Cloud object deletion
only after all Cloud and Drawing writers have upgraded to the shared lock/intent protocol.

Birthday candidate lists (`[Y, Y-1, Y-2, Y+1]`) and other `str | list[str]` asset fields shipped in this release.
Cloud may start emitting lists once every Drawing node runs 3.2.0.

## 9. Rollout procedure

**Artifact output, per node:**
1. Confirm Cloud steps 1 and 2 above.
2. Set `HARUKI_STORAGE__NODE_NAME` (for example `cn09`), the `HARUKI_STORAGE__PROVIDER__*` endpoint and
   credentials (tailnet address, never a public one), `HARUKI_STORAGE__INDEX__DSN` from env, and
   `HARUKI_STORAGE__ENABLED=true`.
3. Watch `/render-stats["artifacts"]`. `published` should grow, `degraded.*` and `index_skipped.*` should stay
   at 0, and `directive_rejected` should stay empty.
4. Roll to the remaining nodes.

**Asset mirror:**
1. Configure Asset-Updater's provider so that the absolute key in the `pjsk-assets` bucket is
   `<region>-assets/<mode>/<rel>`. Drawing uses `root=""` and puts `<region>-assets` in the key, so what has to
   match is the absolute key, not the root/key split.
2. On one Drawing node, set `HARUKI_ASSETS__SOURCE=mirror`, the `HARUKI_ASSETS__MIRROR__PROVIDER__*` values, the
   manifest version, and keep `HARUKI_ASSETS__MIRROR__LOCAL_FALLBACK=true`.
3. Watch `asset_mirror.local_fallback_hits` and `asset_mirror.remote_miss`. A wrong bucket or root shows up as a
   counter, never as a 500.
4. When both read 0 across a full cycle, set `HARUKI_ASSETS__MIRROR__LOCAL_FALLBACK=false`.
5. Roll to the other nodes. Deploy the private `drawer.real.py` with the same candidate-list edits as the public
   drawers first.

## 10. Smoke checks

A local artifact round trip can use an in-memory object store, but still needs a test PostgreSQL database
with Cloud’s schema and `HARUKI_STORAGE__INDEX__DSN` configured. Without the index it returns bytes:

```bash
HARUKI_STORAGE__ENABLED=true HARUKI_STORAGE__PROVIDER__SCHEME=memory HARUKI_STORAGE__NODE_NAME=local \
  uv run granian --interface asgi --host 127.0.0.1 --port 8000 src.core.main:app

python scripts/concurrent_fetch_images.py --base-url http://127.0.0.1:8000 \
  --endpoint /api/pjsk/honor/ --payload-file out/ci-sk-trend/honor_payload.json \
  --requests 20 --concurrency 4 --expect artifact
```

`--expect artifact` sends a complete directive (any `--header` with the same name overrides it). It counts only a
200 `application/json` body with `kind == "artifact_ref"` as `ok_images`. `--fetch-cdn <base>` also GETs
`<base>/<cdn_path>` for every ref and requires the size to match `size_bytes`. Use it against a real node's
public `image-cache` host. The default `--expect image` is unchanged.
The free-threaded smoke workflow runs this as an optional step after the three bytes-mode runs.

## 11. Renderer and asset generations

`GET /cache/identity` returns `{"version":1,"renderer_epoch":"<64 lowercase hex>"}`. If identity cannot be
computed, it returns HTTP 503 with an empty epoch. The fingerprint includes Python/native renderer contents,
selected font and local-template contents, and output-affecting settings. Node names, mount locations, pool
sizes, cache budgets and timeouts do not participate. Native builds for different architectures can have
different identities; consumers track each backend and include the backend set in their cache generation.

Identity is computed once per process, matching process-loaded fonts and templates. Replace local fonts or
templates through a restart/deploy. The remote game-asset revision is independent: Cloud passes a global
revision digest in the directive and incorporates payload-specific shard digests in its own request key.
Drawing uses the revision as a separate mirror directory, including negative memoization and heavy-worker
requests; it does not parse the manifest itself. No directive means the configured mirror version is used.

In-flight requests lease their namespace until response completion. The sweeper skips leased versions and
files; old unleased directories are atomically renamed before slow removal, so a later request can rebuild
the original path without being deleted by the previous sweep. Interrupted retirements are reclaimed later.
Missing-resource images are never admitted to the encoded-page/native-fragment caches or persistent artifact
store, and the response carries `X-Haruki-Cache-Store: 0` even for a bytes-only request.

Optional loopback PostgreSQL checks can be run with `HARUKI_TEST_INDEX_DSN` and
`pytest -q tests/test_index_postgres_integration.py`. They create and remove only a random test schema and
verify lock exclusion, single-connection operation, dedup, rollback and expired-intent rejection.

The supported service layout uses one ASGI worker per mirror directory (the Docker command uses
`--workers 1`). Separate ASGI processes must have separate mirror roots: in-process leases do not coordinate
independent ASGI processes. Managed heavy-worker children remain covered by their parent's request lease,
and executor threads retain a separate lease until their actual work ends, including after request cancellation.
Background sweeps carry no request lease. Temporary-file age uses local ctime, never the remote object's mtime;
a legacy local fallback is rendered but marked no-store because its remote revision cannot be established.
