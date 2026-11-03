"""Layanan kriptografi untuk proteksi data biometrik (UU PDP No. 27/2022).

Menggunakan AES-256-GCM untuk enkripsi data biometrik at-rest.
"""
import os
import hashlib
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from app.config import settings


def _get_encryption_key() -> bytes:
    """Mendapatkan kunci 32-byte (256-bit) untuk AES-GCM dari config."""
    raw_key = settings.BIOMETRIC_ENCRYPTION_KEY.encode("utf-8")
    # Jika panjang kunci bukan tepat 32 byte, turunkan hash SHA256 agar selalu 32 byte
    if len(raw_key) != 32:
        return hashlib.sha256(raw_key).digest()
    return raw_key


def calculate_sha256(data: bytes | str) -> str:
    """Menghitung SHA-256 hex digest dari data mentah."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def encrypt_biometric_template(raw_template_bytes: bytes) -> bytes:
    """Mengenkripsi template biometrik menggunakan AES-256-GCM.
    
    Format output yang disimpan: nonce (12 bytes) + ciphertext + tag (16 bytes).
    """
    key = _get_encryption_key()
    aesgcm = AESGCM(key)
    nonce = os.urandom(12)  # 96-bit nonce standar untuk AES-GCM
    ciphertext = aesgcm.encrypt(nonce, raw_template_bytes, None)
    return nonce + ciphertext


def decrypt_biometric_template(encrypted_data: bytes) -> bytes:
    """Mendekripsi data biometrik terenkripsi AES-256-GCM."""
    if len(encrypted_data) < 28:
        raise ValueError("Data terenkripsi terlalu pendek / tidak valid")
    key = _get_encryption_key()
    aesgcm = AESGCM(key)
    nonce = encrypted_data[:12]
    ciphertext = encrypted_data[12:]
    return aesgcm.decrypt(nonce, ciphertext, None)
