from __future__ import annotations

import unittest

from backend.order_sign import _load_sol_keypair, b58encode, ed25519_pubkey_from_seed


class OrderSignTests(unittest.TestCase):
    def test_load_sol_keypair_prefers_derived_pubkey_over_embedded_stale_pubkey(self) -> None:
        seed = bytes(range(32))
        derived_pubkey = ed25519_pubkey_from_seed(seed)
        stale_pubkey = bytes([0xAA] * 32)
        encoded = b58encode(seed + stale_pubkey)

        loaded_seed, loaded_pubkey = _load_sol_keypair(encoded)

        self.assertEqual(loaded_seed, seed)
        self.assertEqual(loaded_pubkey, derived_pubkey)
        self.assertNotEqual(loaded_pubkey, stale_pubkey)


if __name__ == "__main__":
    unittest.main()
