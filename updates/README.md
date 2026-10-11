# Package catalog updates

Package YAML files in **data/packages/** and the reference files **bundles.yaml**
and **adf_rules.yaml** are the maintained catalog. The generator exports all
packages, including local files and shared archive consumers, with their complete
metadata, dependencies, install rules, relocations, menus and Amiga scripts.
Change a download hash and any affected installation paths together.

## Client formats

| Endpoint on the updates branch | Clients | Contents |
| --- | --- | --- |
| manifest.json | Existing schema 1 clients | Reviewed download overrides and current app release metadata |
| manifest-v2.json | Full catalog clients starting at 1.1.0 | Complete catalogs for explicit app version ranges and current app release metadata |

Both files use the existing Ed25519 signature envelope and the **updates-2026** key
identifier. The private key stays in the **UPDATE_MANIFEST_PRIVATE_KEY** repository
secret. Neither file is edited by hand.

**legacy-manifest-source.json** is the migration fallback for schema 1. If an older
publication exists, its verified overrides are preserved instead. For a changed
archive that retains the files required by older clients, add its package name to
**catalog-target.yaml** under **legacy_hash_updates**. The generator copies only
the new hash from YAML and rejects changes to the old download source. Check the
older installation rules against the archive first. An upstream server can replace
the archive again, so the checksum must be checked before publication.

## Compatibility ranges

**catalog-target.yaml** describes the current target. Its minimum version is
inclusive; its maximum is exclusive. Versions use Python packaging version
ordering, including prereleases. The catalog schema version describes supported
rules independently of the app version.

For a compatible change, keep the target unchanged. For a new engine capability,
use a new target id and minimum version, and explicitly close the previous range:

```yaml
target:
  id: hatcher-1_2
  min_hatcher_version: "1.2.0"
  max_hatcher_version_exclusive: null
  catalog_schema_version: 2
close_ranges:
  hatcher-1_1: "1.2.0"
```

Catalog schema 2 (target **hatcher-1_2**, from 1.2.0) adds two optional package
fields: **upstream_version**, a display label that never orders updates, and
**native**, the live-install policy for the Amiga package tool (eligibility
`supported`, `protected`, `user-archive-required` or `unsupported`, a reason for
everything but `supported`, `preserve` paths for changed configuration files and
`reboot: none|cold`). Packages without **native** get a conservative policy from
their recipe (`data/package_identity.py`). 1.1 clients reject unknown package fields,
so **hatcher-1_1** is closed at 1.2.0 and keeps its last schema 1 catalog; the
generator refuses to put these fields into a schema 1 target. Publishing the first
hatcher-1_2 catalog needs hatcher-1_1 in the previous signed manifest.

The generator preserves previous signed catalogs outside the target. Closing a
range changes its compatibility metadata and revision, while retaining its package,
bundle and ADF content. Overlapping ranges and unknown ids in **close_ranges** fail
publication. Historical catalog data is generated output, not a second maintained
package list. Keep the current id's minimum version unchanged.

Host tools, themes, local archives, templates, media detection and Python code ship
with the application. New local assets, new rule types and new engine behavior need
an app release. Changing a field in a manifest cannot add those capabilities.

## Activation and builds

A client uses bundled YAML offline, then its last valid compatible signed cache.
A complete compatible catalog replaces the previous catalog; omitted packages are
removed. Invalid signatures, unknown fields, unsafe paths, broken references,
missing local resources and incompatible engine requirements prevent activation.
An unsupported app range or catalog schema leaves the prior catalog active while
still allowing the app release notification.

The cache files are **manifest-v2.json** and **manifest-v2-meta.json** under the runtime
updates cache. Schema 1 caches are ignored. **HATCHER_UPDATE_MANIFEST_URL** overrides
the schema 2 endpoint only. Conditional request headers are tied to the cached
bytes and URL. An invalid or incompatible response does not replace the good cache.

**BuildWorkflow.catalog** is an immutable snapshot, captured when the workflow is
created. A context local to the build exposes that snapshot to all package, bundle,
resolver and ADF lookups through finalization. New worker threads needing catalog
access must explicitly enter **use_catalog(workflow.catalog)**; contexts are not
inherited by ordinary Python threads. Catalog models returned by loaders are copies.
The log records the catalog id, revision and source commit.

The GUI defers catalog activation until a running build finishes. It keeps existing
software choices by package id, applies defaults for new entries, recalculates
dependencies and reports removed selections. Loading a saved configuration also
reports unknown selected package ids.

## Generate and publish

The **Publish update manifest** workflow reads the selected commit, the latest
published release and both prior signed manifests. It generates and signs both
endpoints, validates their content, then publishes them in one normal commit on
**updates**. It fetches that branch afterward and checks the signatures, source
commit and complete catalog against the checkout. No application release is needed
for a compatible package update.

For local verification, use temporary release metadata and installer artifacts:

```bash
.venv/bin/python scripts/build-update-manifest.py /tmp/payload-v2.json \
  --legacy-output /tmp/payload-v1.json \
  --release-json /tmp/release.json --asset-dir /tmp/release-assets \
  --previous-v1 /tmp/previous-v1.json --previous-v2 /tmp/previous-v2.json
```

Omit the previous-file arguments only for the initial publication. Previous files
must be signed with the trusted key. Explicit **--revision** values must exceed both
published revisions; otherwise the generator uses the greater of the current Unix
time and the previous revisions plus one. Identical sources, artifacts, previous
publications and explicit revision produce identical payloads. Sign with
**scripts/sign-update-manifest.py** and verify using **scripts/verify-update-publication.py**.

The client limit remains 2 MiB per signed manifest. The generator checks the encoded
size before signing, reserving room for the envelope; the signer checks it again.
Several catalog generations fit, but publication fails rather than silently removing
older compatibility ranges when the limit is reached.

To revert a broken catalog, restore its good YAML content and publish with a higher
revision. Do not reduce publication or catalog revisions. A failed download or
validation leaves the last compatible cache available.

## Amiga catalog

**amiga-catalog-v1.json** is the package catalog as the Amiga tool Hatcher Packages
reads it, generated from the same validated YAML. It is the unsigned
`hatcher-native-metadata-1` payload of feed `hatcher-packages` (data schema
`packages-audit-1`), wrapping a `hatcher-native-catalog-draft-1` catalog: every
package, bundle, compatibility list and dependency edge, with the recipe identity
defined by hatcher-packages. It is an audit view; installing on the Amiga still
needs a signed package plan and set from hatcher-packages.

The native reader accepts only `eligibility: unreviewed`, `upstream_version: null`
and `content_identity: null`. So the catalog policy travels as the first entry of
`reasons` (`Hatcher policy: <eligibility>: <reason>`, then preserved files and the
reboot note), and the reviewed archive pin (`pinned_url`, `sha256`, `size`) is added
to the opaque `source` descriptor. The upstream version and content identity stay in
the desktop catalog until the native format grows fields for them.

**amiga-artifacts.lock.yaml** holds the reviewed pins (HTTPS URL, size, SHA-256 and
the MD5 the YAML must still carry) for every package marked
`native.eligibility: supported`. The exporter refuses a supported package without a
pin, a pin whose MD5 no longer matches the YAML, a supported recipe that uses rules
the native executor lacks (`**`, `?`, classes, non-User-Startup blocks, several menu
entries, desktop-only archives) and a supported package on a hard dependency cycle.
A changed upstream file therefore never picks up a new SHA-256 by itself.

Export from a clean checkout (the source commit label is HEAD), then sign with the
publisher key, which never enters this repository:

```bash
.venv/bin/python scripts/export-amiga-catalog.py /tmp/amiga-catalog-v1.payload.json \
  --revision <N>
python tools/sign-native-metadata.py /tmp/amiga-catalog-v1.payload.json \
  amiga-catalog-v1.json --signing-key-file <publisher-key.pem> \
  --confirm-sign-native-audit            # run inside hatcher-packages
```

The revision must increase with every publication. Neither tool overwrites an
existing file. `--allow-dirty` exists for local previews only.

## Package checks

**scripts/check-package-downloads.py** reads the checkout catalog directly, without
runtime manifests or download overrides. It downloads remote packages, compares MD5
checksums and checks pinned GitHub tags for newer releases. Packages in
**amiga-artifacts.lock.yaml** are also downloaded from the pinned URL and compared by
SHA-256 and size; a mismatch is reported as "pinned artifact changed". The daily workflow
updates its tracking issue and closes it when all checks pass.

A fixed URL can reveal changed bytes or a broken link, but not a new release at a
different URL. Set **download.check_latest_release** to false for intentionally
pinned GitHub packages. Passing checksum checks does not verify install paths:
inspect changed archives, nested files and all consumers of a shared archive.

Documentation generation uses the same source loader. **docs/packages.md** remains
generated and ignored. Complete image builds and Amiga boot checks remain manual
acceptance steps; model checks and offscreen GUI checks do not replace them.
