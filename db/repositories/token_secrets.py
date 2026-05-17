import hashlib
import hmac as hmac_module
import logging
import os
from typing import Optional

logger = logging.getLogger('moddy.database')


def _encrypt_token(token: str, alert_key: bytes) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    nonce = os.urandom(12)
    ciphertext = AESGCM(alert_key).encrypt(nonce, token.encode(), None)
    return nonce + ciphertext


def _decrypt_token(encrypted: bytes, alert_key: bytes) -> str:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    return AESGCM(alert_key).decrypt(encrypted[:12], encrypted[12:], None).decode()


class TokenSecretRepository:
    """Stores encrypted token secrets so the Invalidate button survives bot restarts.

    Double-lock: without the ck (in the button custom_id) the DB alone is
    undecipherable; without TOKEN_DETECTOR_KEY the ck alone is not enough.
    """

    async def save_token_secret(
        self,
        ck: str,
        token: str,
        week_number: int,
        alert_key: bytes,
        master_key: bytes,
    ) -> None:
        encrypted_token = _encrypt_token(token, alert_key)
        user_id_hmac = hmac_module.new(
            master_key, f"{ck}_userid".encode(), hashlib.sha256
        ).digest()
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO token_secrets (ck, encrypted_token, user_id_hmac, week_number)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (ck) DO NOTHING
                """,
                ck,
                encrypted_token,
                user_id_hmac,
                week_number,
            )

    async def get_token_secret(self, ck: str, master_key: bytes) -> Optional[str]:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT encrypted_token, week_number FROM token_secrets WHERE ck = $1", ck
            )
        if not row:
            return None
        try:
            alert_key = hmac_module.new(
                master_key,
                f"{ck}_{row['week_number']}".encode(),
                hashlib.sha256,
            ).digest()
            return _decrypt_token(bytes(row["encrypted_token"]), alert_key)
        except Exception as exc:
            logger.debug(f"Token secret decryption failed for {ck}: {exc}")
            return None

    async def delete_token_secret(self, ck: str) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute("DELETE FROM token_secrets WHERE ck = $1", ck)

    async def cleanup_old_secrets(self, max_age_days: int = 7) -> int:
        async with self.pool.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM token_secrets "
                "WHERE created_at < NOW() - make_interval(days => $1)",
                max_age_days,
            )
        try:
            return int(result.split()[-1])
        except Exception:
            return 0
