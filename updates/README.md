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
  catalog_schema_version: 1
close_ranges:
  hatcher-1_1: "1.2.0"
```

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

## Package checks

**scripts/check-package-downloads.py** reads the checkout catalog directly, without
runtime manifests or download overrides. It downloads remote packages, compares MD5
checksums and checks pinned GitHub tags for newer releases. The daily workflow
updates its tracking issue and closes it when all checks pass.

A fixed URL can reveal changed bytes or a broken link, but not a new release at a
different URL. Set **download.check_latest_release** to false for intentionally
pinned GitHub packages. Passing checksum checks does not verify install paths:
inspect changed archives, nested files and all consumers of a shared archive.

Documentation generation uses the same source loader. **docs/packages.md** remains
generated and ignored. Complete image builds and Amiga boot checks remain manual
acceptance steps; model checks and offscreen GUI checks do not replace them.
