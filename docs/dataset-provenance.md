# Deskdrop dataset provenance and attribution

## Publisher and exact version

This project contains information from **Articles sharing and reading from CI&T
DeskDrop**, published by **Gabriel Moreira (`gspmoreira`)**, with CI&T acknowledged
by the publisher as the source and as permitting the sample's release.

- [Publisher dataset page](https://www.kaggle.com/datasets/gspmoreira/articles-sharing-reading-from-cit-deskdrop)
- [Publisher metadata API](https://www.kaggle.com/api/v1/datasets/view/gspmoreira/articles-sharing-reading-from-cit-deskdrop)
- [Version 5 download](https://www.kaggle.com/api/v1/datasets/download/gspmoreira/articles-sharing-reading-from-cit-deskdrop?datasetVersionNumber=5)
- Dataset ID: **2234**; verified version: **5**; published update:
  **2017-08-27T21:33:01.063Z**. Publisher's version note identifies timestamp fixes.
- Verification date: **2026-10-19**.

Both CSVs downloaded from the version-pinned archive match the committed originals
byte for byte by SHA-256 and size. This establishes version identity independently
of row counts. Only provenance metadata was updated; original CSVs were not edited.
See [machine-readable evidence](evidence/deskdrop-provenance.json) and
[source hashes](../data/raw/manifest.json).

## Declared licenses and redistribution

Kaggle's publisher metadata declares “Database: Open Database, Contents: Database
Contents”: **Open Data Commons Open Database License (ODbL) 1.0** for the database,
with **Database Contents License (DbCL) 1.0** for contents. This is the publisher's
license declaration, not an independent audit of every third-party article's rights.

This database is made available under the
[ODbL 1.0](https://opendatacommons.org/licenses/odbl/1-0/); rights in individual
contents are under the [DbCL 1.0](https://opendatacommons.org/licenses/dbcl/1-0/).
These dataset licenses are separate from the project's software licensing.

The [publisher-independent ODbL summary](https://opendatacommons.org/licenses/odbl/summary/)
permits database use, copying, sharing and adaptation subject to attribution, keeping
license notices, share-alike requirements for public derivative databases, and the
license's openness requirements. DbCL grants rights over contents subject to ODbL
compliance. The linked full terms govern; the summary is not the license itself.

Keep this attribution, source reference, version and license links with copies of
these CSVs and data exports. Retain notices and identify changes to derived databases;
apply the ODbL's derivative-database conditions when distributing or publicly using
those databases. Original article URLs remain part of each source record.

## Reproduce the version check

Run the following from the repository root. Downloads stay outside the repository:

```sh
curl -fL 'https://www.kaggle.com/api/v1/datasets/view/gspmoreira/articles-sharing-reading-from-cit-deskdrop' -o /tmp/deskdrop-metadata.json
curl -fL 'https://www.kaggle.com/api/v1/datasets/download/gspmoreira/articles-sharing-reading-from-cit-deskdrop?datasetVersionNumber=5' -o /tmp/deskdrop-v5.zip
uv run python scripts/verify_upstream.py /tmp/deskdrop-metadata.json /tmp/deskdrop-v5.zip
```

The script fails for a different dataset/license declaration, absent version 5,
changed local source, missing archive member, or mismatched CSV checksum/size.
The full publisher response is not committed because it includes changing usage
statistics; the tracked evidence retains relevant fields and its observed hash.
The archive hash is a receipt, not a mandatory identity check: ZIP packaging may
change while its decompressed source bytes remain identical.
