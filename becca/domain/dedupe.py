"""The dedupe key: what makes two rows the same call to the same person.

hash(normalised E.164 number + that agent's mapped input values), per
FR-CONTACT-8. The same call to the same person is a duplicate; a
different call to the same person is not — two people on one office
line differ in their mapped values, and so do two appointments for one
person. Normalisation runs before this function is called.
"""

import hashlib


def dedupe_key(phone_e164: str, mapped_values: dict[str, str]) -> str:
    """Deterministic over the number and the mapped values, order-free."""
    h = hashlib.sha256()
    h.update(phone_e164.encode())
    for key in sorted(mapped_values):
        h.update(b"\x00")
        h.update(key.encode())
        h.update(b"\x01")
        h.update(mapped_values[key].strip().encode())
    return h.hexdigest()
