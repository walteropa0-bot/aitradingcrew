"""Wallet: loads the private key from the environment and *partially signs*
swap transactions that Jupiter built for us.

Security properties
  * The key is read once from an environment variable, never written to disk,
    never logged (see logging_setup.register_secret) and never sent anywhere.
  * This module contains NO transfer instruction builder. The only thing it
    can do is add the wallet's own signature to a transaction prepared by the
    Jupiter swap API (which the executor additionally simulates first).
"""
from __future__ import annotations

import base64
import json
import logging
from dataclasses import dataclass

from solders.keypair import Keypair
from solders.message import to_bytes_versioned
from solders.signature import Signature
from solders.transaction import VersionedTransaction

log = logging.getLogger("crew.wallet")

_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


class WalletError(Exception):
    pass


def _b58decode(s: str) -> bytes:
    n = 0
    for ch in s:
        idx = _B58.find(ch)
        if idx < 0:
            raise WalletError("Ungültiges Zeichen im Private Key (kein Base58)")
        n = n * 58 + idx
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big") if n else b""
    pad = len(s) - len(s.lstrip("1"))
    return b"\x00" * pad + raw


def parse_secret(secret: str) -> Keypair:
    """Accepts: Base58 private key (Phantom export, 64 bytes), JSON array of 64
    numbers (solana-keygen id.json), or a 32-byte Base58 seed. Error messages
    never contain the secret itself."""
    s = (secret or "").strip().strip("'\"").strip()
    if not s:
        raise WalletError("SOLANA_PRIVATE_KEY ist leer.")
    if " " in s and len(s.split()) in (12, 15, 18, 21, 24):
        raise WalletError(
            "Das sind die 12/24 Wörter der Seed-Phrase. Bitte in Phantom stattdessen "
            "'Private Key anzeigen' wählen und den langen Base58-Schlüssel verwenden."
        )
    try:
        if s.startswith("["):
            raw = bytes(json.loads(s))
        else:
            raw = _b58decode(s)
        if len(raw) == 64:
            return Keypair.from_bytes(raw)
        if len(raw) == 32:
            return Keypair.from_seed(raw)
    except WalletError:
        raise
    except Exception:
        pass
    raise WalletError(
        "Der Private Key konnte nicht gelesen werden. Erwartet wird der Base58-Schlüssel "
        "aus Phantom (ca. 87-88 Zeichen) ohne Leerzeichen und ohne Anführungszeichen."
    )


@dataclass
class SignedTx:
    b64: str
    signature: str     # "" when the fee payer is a third party (known only after /execute)


class Wallet:
    def __init__(self, keypair: Keypair) -> None:
        self.keypair = keypair
        self.address = str(keypair.pubkey())

    @classmethod
    def from_secret(cls, secret: str) -> "Wallet":
        return cls(parse_secret(secret))

    def partial_sign(self, tx_b64: str) -> SignedTx:
        """Add *our* signature to our signer slot, leave other slots (e.g. an RFQ
        market maker or Jupiter's gas payer) untouched - they sign in /execute."""
        try:
            tx = VersionedTransaction.from_bytes(base64.b64decode(tx_b64))
        except Exception as exc:
            raise WalletError(f"Transaktion von Jupiter nicht lesbar: {exc}") from exc
        msg = tx.message
        n = msg.header.num_required_signatures
        signers = list(msg.account_keys)[:n]
        me = self.keypair.pubkey()
        if me not in signers:
            raise WalletError("Wallet ist kein Signer dieser Transaktion (falscher taker?)")
        sigs = list(tx.signatures)
        if len(sigs) != n:
            sigs = [Signature.default()] * n
        sigs[signers.index(me)] = self.keypair.sign_message(to_bytes_versioned(msg))
        signed = VersionedTransaction.populate(msg, sigs)
        first = sigs[0]
        return SignedTx(
            b64=base64.b64encode(bytes(signed)).decode(),
            signature="" if first == Signature.default() else str(first),
        )
