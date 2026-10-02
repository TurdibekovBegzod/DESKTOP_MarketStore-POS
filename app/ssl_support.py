"""Verified HTTPS configuration shared by desktop network clients."""

import functools
import os
import ssl
from pathlib import Path

try:
    import certifi
except ImportError:  # Development environments may still use the OS trust store.
    certifi = None


def ca_bundle_path() -> str | None:
    override = os.environ.get("MARKETSTORE_CA_BUNDLE")
    if override and Path(override).is_file():
        return str(Path(override).resolve())
    if certifi is not None:
        path = Path(certifi.where())
        if path.is_file():
            return str(path.resolve())
    return None


def create_ssl_context() -> ssl.SSLContext:
    """The verified context for the current CA bundle, built once per bundle.

    Building one parses the whole certifi bundle, which every API call used to
    pay for again. A context only holds settings and trusted roots, so one
    instance is safely shared by every thread and connection.
    """
    return _context_for(ca_bundle_path())


@functools.lru_cache(maxsize=4)
def _context_for(cafile: str | None) -> ssl.SSLContext:
    context = ssl.create_default_context(cafile=cafile) if cafile else ssl.create_default_context()
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.check_hostname = True
    context.verify_mode = ssl.CERT_REQUIRED
    return context


def verify_ca_bundle() -> str:
    """Fail fast in packaged builds when no trusted CA certificates were bundled."""
    context = create_ssl_context()
    if not context.get_ca_certs():
        raise RuntimeError("HTTPS CA sertifikatlar to'plami bo'sh.")
    return ca_bundle_path() or "system"
