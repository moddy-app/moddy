# C2PA test fixtures

Used by `tests/test_ai_detector.py`.

| File | What it is |
|---|---|
| `es256_certs.pem`, `es256_private.key` | **Test-only** ES256 signing certificate chain + key from the c2pa-python repository (`tests/fixtures/`, MIT / Apache-2.0, © Adobe). Subject `O=C2PA Test Signing Cert`, `OU=FOR TESTING_ONLY`; valid until 2030-08-26. Never trusted in production. |
| `test_anchors.pem` | The matching **test** root anchors (c2pa-python `tests/trust_config_test_settings.json`). Configured as the trust list in tests, so images signed with the key above validate as `Trusted`. |
| `C2PA-TRUST-LIST.pem` | Snapshot (2026-10-19) of the official C2PA trust list the bot downloads at runtime (`https://raw.githubusercontent.com/c2pa-org/conformance-public/refs/heads/main/trust-list/C2PA-TRUST-LIST.pem`). Used to check that the test signer is *not* trusted by production settings, and to verify real images offline. |
| `real/` | Real-world C2PA images (ChatGPT, Firefly, Leica, Pixel…). Drop a file here and add its expected verdict to `real/expected.json`; the test is skipped while the folder holds no image. |

Signed test images are generated at test time (Pillow + `c2pa.Builder`), so no
binary image needs to be committed for the synthetic cases.
