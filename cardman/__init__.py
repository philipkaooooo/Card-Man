"""CardMan."""
import os
import sys

# The python.org macOS framework build ships without a CA bundle, so urllib fails every
# HTTPS call with CERTIFICATE_VERIFY_FAILED. macOS keeps the system roots here.
if sys.platform == "darwin" and not (os.environ.get("SSL_CERT_FILE") or os.environ.get("SSL_CERT_DIR")):
    if os.path.exists("/etc/ssl/cert.pem"):
        os.environ["SSL_CERT_FILE"] = "/etc/ssl/cert.pem"
