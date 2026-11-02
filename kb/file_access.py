import secrets
import threading
import time
from datetime import datetime, timedelta, timezone

import oci

from kb.common import (
    FILE_OBJECT_PREFIX,
    OCI_AUTH,
    OCI_BUCKET,
    OCI_NAMESPACE,
    OCI_REGION,
    PAR_TTL,
)

REFRESH_SKEW = 60

_lock = threading.Lock()
_cache = {}
_client = None


def _get_client():
    global _client
    with _lock:
        if _client is None:
            if OCI_AUTH == "config":
                _client = oci.object_storage.ObjectStorageClient(oci.config.from_file())
            else:
                signer = oci.auth.signers.InstancePrincipalsSecurityTokenSigner()
                _client = oci.object_storage.ObjectStorageClient(config={}, signer=signer)
        return _client


def object_name(doc_id):
    return f"{FILE_OBJECT_PREFIX}/{doc_id}.pdf" if FILE_OBJECT_PREFIX else f"{doc_id}.pdf"


def signed_url(doc_id):
    now = time.time()
    with _lock:
        cached = _cache.get(doc_id)
        if cached and cached[1] - now > REFRESH_SKEW:
            return cached[0]

    details = oci.object_storage.models.CreatePreauthenticatedRequestDetails(
        name=f"kb-{doc_id}-{int(now)}-{secrets.token_hex(4)}",
        access_type="ObjectRead",
        object_name=object_name(doc_id),
        time_expires=datetime.now(timezone.utc) + timedelta(seconds=PAR_TTL),
    )
    response = _get_client().create_preauthenticated_request(
        OCI_NAMESPACE, OCI_BUCKET, details
    )
    url = f"https://objectstorage.{OCI_REGION}.oraclecloud.com{response.data.access_uri}"
    with _lock:
        _cache[doc_id] = (url, now + PAR_TTL)
    return url
