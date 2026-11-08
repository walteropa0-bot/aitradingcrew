import base64
import json

import pytest
from solders.hash import Hash
from solders.keypair import Keypair
from solders.message import MessageV0, to_bytes_versioned
from solders.pubkey import Pubkey
from solders.signature import Signature
from solders.system_program import TransferParams, transfer
from solders.transaction import VersionedTransaction

from crew.chain.wallet import Wallet, WalletError, parse_secret

from .fakes import unsigned_tx_b64


def test_parses_base58_phantom_key():
    kp = Keypair()
    assert parse_secret(str(kp)).pubkey() == kp.pubkey()
    assert parse_secret(f'  "{kp}"\n').pubkey() == kp.pubkey()          # quotes / whitespace tolerated


def test_parses_json_array_and_seed():
    kp = Keypair()
    assert parse_secret(json.dumps(list(bytes(kp)))).pubkey() == kp.pubkey()
    assert parse_secret(json.dumps(list(bytes(kp)[:32]))).pubkey() == kp.pubkey()


def test_seed_phrase_gets_a_helpful_error_without_echoing_it():
    phrase = "abandon ability able about above absent absorb abstract absurd abuse access accident"
    with pytest.raises(WalletError) as ei:
        parse_secret(phrase)
    assert "Seed-Phrase" in str(ei.value) and "abandon" not in str(ei.value)


@pytest.mark.parametrize("bad", ["", "not-base58-0OIl", "abc", "[1,2,3]"])
def test_garbage_is_rejected_and_never_leaked(bad):
    with pytest.raises(WalletError) as ei:
        parse_secret(bad)
    if bad:
        assert bad not in str(ei.value)


def test_partial_sign_produces_a_valid_signature_in_our_slot():
    w = Wallet(Keypair())
    signed = w.partial_sign(unsigned_tx_b64(w.keypair.pubkey()))
    tx = VersionedTransaction.from_bytes(base64.b64decode(signed.b64))
    assert tx.signatures[0].verify(w.keypair.pubkey(), to_bytes_versioned(tx.message))
    assert signed.signature == str(tx.signatures[0])


def test_partial_sign_keeps_other_signers_slots_empty():
    me, other = Keypair(), Keypair()            # `other` = third-party fee payer / market maker
    ix = transfer(TransferParams(from_pubkey=me.pubkey(), to_pubkey=Pubkey.new_unique(), lamports=1))
    msg = MessageV0.try_compile(other.pubkey(), [ix], [], Hash.default())
    n = msg.header.num_required_signatures
    assert n == 2
    b64 = base64.b64encode(bytes(VersionedTransaction.populate(msg, [Signature.default()] * n))).decode()
    signed = Wallet(me).partial_sign(b64)
    tx = VersionedTransaction.from_bytes(base64.b64decode(signed.b64))
    keys = list(tx.message.account_keys)[:n]
    assert tx.signatures[keys.index(me.pubkey())].verify(me.pubkey(), to_bytes_versioned(tx.message))
    assert tx.signatures[keys.index(other.pubkey())] == Signature.default()
    assert signed.signature == ""               # fee payer is a third party -> id known only after /execute


def test_refuses_to_sign_a_transaction_we_are_not_part_of():
    stranger = Keypair()
    with pytest.raises(WalletError):
        Wallet(Keypair()).partial_sign(unsigned_tx_b64(stranger.pubkey()))


def test_garbage_transaction_is_rejected():
    with pytest.raises(WalletError):
        Wallet(Keypair()).partial_sign("AAAA")
