"""Unit test untuk enkripsi AES-256-GCM template biometrik."""
import pytest
from app.services.crypto import (
    encrypt_biometric_template,
    decrypt_biometric_template,
    calculate_sha256,
)


def test_encrypt_decrypt_roundtrip():
    raw_template = b"SampleVisibleLightFaceTemplateData1234567890"
    encrypted = encrypt_biometric_template(raw_template)

    # Pastikan data terenkripsi berbeda dengan aslinya
    assert encrypted != raw_template
    assert len(encrypted) > len(raw_template)

    # Dekripsi kembali
    decrypted = decrypt_biometric_template(encrypted)
    assert decrypted == raw_template


def test_tampered_ciphertext_fails():
    raw_template = b"SampleFingerprintTemplateData"
    encrypted = bytearray(encrypt_biometric_template(raw_template))

    # Rusak satu byte pada ciphertext/tag
    encrypted[-1] ^= 0xFF

    with pytest.raises(Exception):
        decrypt_biometric_template(bytes(encrypted))


def test_sha256_calculation():
    data = b"HelloZKTeco"
    digest = calculate_sha256(data)
    assert len(digest) == 64
    assert digest == calculate_sha256("HelloZKTeco")
