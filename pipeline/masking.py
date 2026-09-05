# SPDX-License-Identifier: Apache-2.0
"""Keep identifiers out of logs and API responses."""

from __future__ import annotations

import logging
import re

_IBAN = re.compile(r"\b([A-Z]{2}\d{2})[A-Z0-9 ]{11,30}?([A-Z0-9]{4})\b")


def mask_iban(value: str | None) -> str:
    if not value:
        return ""
    compact = value.replace(" ", "")
    if len(compact) < 8:
        return "****"
    return f"{compact[:4]}…{compact[-4:]}"


def mask_text(text: str) -> str:
    """Mask anything that looks like an IBAN inside free text."""
    return _IBAN.sub(lambda m: f"{m.group(1)}…{m.group(2)}", text)


class MaskingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = mask_text(str(record.msg))
        if record.args:
            record.args = tuple(mask_text(str(a)) if isinstance(a, str) else a for a in record.args)
        return True


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler()
    handler.addFilter(MaskingFilter())
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
