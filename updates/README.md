# Package catalog updates

Package YAML files in **data/packages/** and the reference files **bundles.yaml**
and **adf_rules.yaml** are the maintained catalog. The generator exports all
packages, including local files and shared archive consumers, with their complete
metadata, dependencies, install rules, relocations, menus and Amiga scripts.
Change a download hash and any affected installation paths together.

## Client formats

| Endpoint on the updates branch | Clients | Contents |
| --- | --- | --- |
| manifest.json | 1.0 clients | Their frozen download overrides and the current app release metadata |
| manifest-v2.json | Clients from 1.1.0 | One complete catalog for the latest released version and the current app release metadata |

Both files use the existing Ed25519 signature envelope and the **updates-2026** key
identifier. The private key stays in the **UPDATE_MANIFEST_PRIVATE_KEY** repository
secret. Neither file is edited by hand.

The overrides in **manifest.json** are copied from the previous publication as they
are. They are not maintained any more; 1.0 clients only get the release block.

## One catalog per app version

Only the latest released Hatcher version gets package updates. The manifest carries
one catalog, stamped with the version of the checkout it was generated from, and a
client activates a catalog only when that stamp equals its own version. Older
clients keep their last activated list, or their bundled YAML, and still read the
release block, which is how they learn about the new version.

The stamp is the catalog's minimum version; its maximum stays empty. The catalog
schema number is derived from the same version, so clients before 1.3.0, which pick a
catalog by version range and parse every schema number they know, keep a newer
catalog opaque instead of failing on package fields they do not know.

The generator refuses a checkout whose version is not the latest published GitHub
release. Publication therefore happens in two ways: automatically from the release
tag when a release is published, and on demand from that release's branch, named
after the version, for example **1.2.0**, for package fixes between releases. A
checkout of main during development cannot publish.

Host tools, themes, local archives, templates, media detection and Python code ship
with the application. New local assets, new rule types and new engine behavior need
an app release. Changing a field in a manifest cannot add those capabilities.

## Activation and builds

A client uses bundled YAML offline, then its last valid signed cache whose catalog
is stamped with its own version. A complete catalog replaces the previous catalog;
omitted packages are removed. Invalid signatures, unknown fields, unsafe paths,
broken references, missing local resources and incompatible engine requirements
prevent activation. A catalog for another version leaves the prior catalog active
while still allowing the app release notification.

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
time and the previous revisions plus one. The catalog revision counts the
publications for one version. Identical sources, artifacts, previous publications
and explicit revision produce identical payloads. Sign with
**scripts/sign-update-manifest.py** and verify using **scripts/verify-update-publication.py**.

The client limit remains 2 MiB per signed manifest. The generator checks the encoded
size before signing, reserving room for the envelope; the signer checks it again.

To revert a broken catalog, restore its good YAML content and publish with a higher
revision. Do not reduce publication or catalog revisions. A failed download or
validation leaves the last compatible cache available.

## Amiga tools

No Amiga tool that reads these files ships with Hatcher. The publish workflow
writes **emu68-releases-v1.json**, the signed export of
**data/reference/emu68_releases.yaml**, next to the manifests with the same revision.
The build receipts in **Emu68-Hatcher/Packages/build-receipts.json** record each
package's MD5 **hash**; no Amiga tool reads them.

## Package checks

**scripts/check-package-downloads.py** reads the checkout catalog directly, without
runtime manifests or download overrides. The daily workflow runs it on the branch of
the latest published release. It downloads remote packages, compares MD5
checksums and checks pinned GitHub tags for newer releases. The daily workflow updates
its tracking issue and closes it when all checks pass.

A fixed URL can reveal changed bytes or a broken link, but not a new release at a
different URL. Set **download.check_latest_release** to false for intentionally
pinned GitHub packages. Passing checksum checks does not verify install paths:
inspect changed archives, nested files and all consumers of a shared archive.

Documentation generation uses the same source loader. **docs/packages.md** remains
generated and ignored. Complete image builds and Amiga boot checks remain manual
acceptance steps; model checks and offscreen GUI checks do not replace them.
