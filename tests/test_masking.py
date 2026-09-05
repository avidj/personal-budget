# SPDX-License-Identifier: Apache-2.0
from pipeline.masking import mask_iban, mask_text


def test_mask_iban():
    assert mask_iban("DE00 1111 1111 1111 1111 11") == "DE00…1111"
    assert mask_iban("") == ""
    assert mask_iban("short") == "****"


def test_mask_text_hides_ibans_in_free_text():
    assert "1111 1111 1111" not in mask_text("paid to DE00 1111 1111 1111 1111 11 today")
    assert mask_text("no iban here") == "no iban here"
