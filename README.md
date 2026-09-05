# C25-firmware
C25 firmware releases and OTA metadata

## Automated OTA release

1. Create a **draft**, non-prerelease GitHub Release with tag `vX.Y.Z` and
   upload exactly one `c25-firmware.bin` (the unsigned ESP APP image, not a
   merged flash dump). The complete file must fit in 1,769,472 bytes.
2. After this workflow is merged into `main`, run **Actions → C25 OTA release →
   Run workflow** from `main`. Enter versions as `X.Y.Z` without the `v` prefix,
   a compatible hardware revision, storage layout, OTA notes, and mandatory flag.
   Hardware defaults to `C25-REV-A`; storage defaults to `C25-AUDIO512-V1`.
   These compatibility values are operator declarations, not extracted from the binary.
3. Leave `publish` disabled first. This read-only dry run validates the draft
   firmware and generates `latest.json` locally on the runner. The run summary
   reports the size and both SHA-256 values; no release assets are changed.
4. Run again with the same inputs and `publish` enabled. The workflow validates
   again, uploads and verifies `latest.json`, then publishes the selected draft
   as the latest release. It downloads both assets again to verify their contents.

The metadata retains the existing OTA schema, including the `hardware` array,
whole-file `sha256`, and separately validated appended `image_sha256`. Notes are
written to the metadata; the draft's existing release description is preserved.
Only the built-in `GITHUB_TOKEN` with `contents: write` is needed.

Published releases are rejected. Existing draft metadata is reused only if its
bytes match exactly; a different `latest.json` causes an error without replacing
it. Review/remove that draft asset manually before retrying. An upload followed
by a failure leaves the release in draft when publication has not occurred.
If verification fails after publication, the run fails and does not attempt an
automatic rollback or edit the published release. Avoid editing the selected
draft while a run is in progress; GitHub has no atomic validate-and-publish API.

Run offline validation and mocked release lifecycle tests with:

```sh
python3 -m unittest discover -s tests -v
```
