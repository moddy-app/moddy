"""Pure domain logic for /whois, /dns, /ssl and /subdomains.

* ``names``  — parsing a domain or URL, IDN/punycode, validation, registrable domain
* ``rdap``   — RDAP bootstrap + domain record parsing, age and status helpers
* ``doh``    — DNS-over-HTTPS JSON parsing, email-authentication hints
* ``tls``    — X.509 certificate description and failure diagnosis
* ``crtsh``  — crt.sh (Certificate Transparency) name extraction

No Discord, no database, no network: the fetching lives in
``services/web_tools_service.py``. See docs/features/TOOLS_WEB.md.
"""
