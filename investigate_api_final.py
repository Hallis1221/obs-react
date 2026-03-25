"""
NewsWeb API Investigation Results
==================================
Discovered the internal JSON API used by the React SPA at newsweb.oslobors.no.

DISCOVERY METHOD:
  1. The SPA loads https://newsweb.oslobors.no/urls.json on startup, which contains:
     {"api_large": "https://api3.oslo.oslobors.no", ...}
  2. All data requests go to https://api3.oslo.oslobors.no/v1/newsreader/*
  3. All endpoints use HTTP POST with EMPTY body
  4. Filtering is done via QUERY PARAMETERS (not request body)

API BASE URL: https://api3.oslo.oslobors.no/v1/newsreader

ENDPOINTS:
  POST /list                 - List/search messages (main endpoint)
  POST /message              - Get single message with full body text
  POST /env                  - Environment/config info
  POST /categories           - List all announcement categories
  POST /markets              - List all markets (XOSL, XOAX, XOAM, MERK)
  POST /issuers              - List all issuers (tickers)
  POST /infotext             - Info text (usually empty)
  POST /announcement         - Get announcement/attachment by clientAnnouncementId

QUERY PARAMETERS for /list:
  fromDate      - Start date, format YYYY-MM-DD  (e.g., 2026-03-18)
  toDate        - End date, format YYYY-MM-DD    (e.g., 2026-03-25)
  issuer        - Issuer ticker symbol           (e.g., EQNR, DNB, DOFG)
  category      - Category ID (integer)          (e.g., 1104)
  market        - Market ID (integer)            (e.g., 1 = XOSL)
  messageTitle  - Free text search in title      (e.g., "dividend")

QUERY PARAMETERS for /message:
  messageId     - The numeric message ID         (e.g., 669263)

QUERY PARAMETERS for /announcement:
  clientAnnouncementId - UUID of the announcement

REQUIRED HEADERS:
  Content-Type: application/json
  Origin: https://newsweb.oslobors.no
  Referer: https://newsweb.oslobors.no/

PAGINATION:
  - Hard cap of 601 messages per response
  - No built-in pagination parameters (page, offset, limit, etc.)
  - To fetch more: use narrower date ranges and iterate by date windows

MARKETS (from /markets endpoint):
  id=1   symbol=XOSL  name=Oslo Bors
  id=4   symbol=XOAX  name=Euronext Expand
  id=8   symbol=XOAM  name=Nordic ABM
  id=29  symbol=MERK  name=Euronext Growth (Oslo)

MESSAGE FIELDS (from /list response):
  messageId, newsId, title, category[], markets[], issuerId,
  correctionForMessageId, correctedByMessageId, issuerSign, issuerName,
  instrId, instrumentName, instrumentFullName, publishedTime, test,
  numbAttachments, clientAnnouncementId, infoRequired, oamMandatory

MESSAGE DETAIL FIELDS (from /message response, adds):
  body, attachments[]

DAILY VOLUME (observed 2026-03-25):
  ~40-90 messages per weekday
  ~600 messages per 7 calendar days
"""

import json
import httpx

BASE_API = "https://api3.oslo.oslobors.no/v1/newsreader"

HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "Origin": "https://newsweb.oslobors.no",
    "Referer": "https://newsweb.oslobors.no/",
}


def demo():
    """Demonstrate all discovered API endpoints."""
    with httpx.Client(timeout=30) as client:

        # 1. List today's messages
        print("=" * 70)
        print("1. Latest messages (no filter):")
        r = client.post(f"{BASE_API}/list", headers=HEADERS)
        data = r.json()
        msgs = data["data"]["messages"]
        print(f"   {len(msgs)} messages")
        for m in msgs[:3]:
            print(f"   [{m['publishedTime']}] {m['issuerSign']} - {m['title'][:60]}")

        # 2. Historical messages (last 7 days)
        print(f"\n{'=' * 70}")
        print("2. Messages from 2026-03-18 to 2026-03-25:")
        r = client.post(f"{BASE_API}/list?fromDate=2026-03-18&toDate=2026-03-25", headers=HEADERS)
        data = r.json()
        msgs = data["data"]["messages"]
        print(f"   {len(msgs)} messages (cap: 601)")
        print(f"   First: {msgs[0]['publishedTime']}")
        print(f"   Last:  {msgs[-1]['publishedTime']}")

        # 3. Single message detail
        print(f"\n{'=' * 70}")
        print("3. Single message detail:")
        mid = msgs[0]["messageId"]
        r = client.post(f"{BASE_API}/message?messageId={mid}", headers=HEADERS)
        msg = r.json()["data"]["message"]
        print(f"   Title: {msg['title']}")
        print(f"   Issuer: {msg['issuerName']} ({msg['issuerSign']})")
        print(f"   Body (first 200): {msg['body'][:200]}")

        # 4. Categories
        print(f"\n{'=' * 70}")
        print("4. Categories:")
        r = client.post(f"{BASE_API}/categories", headers=HEADERS)
        cats = r.json()["data"]["categories"]
        for c in cats[:5]:
            print(f"   id={c['id']} {c['category_en']}")
        print(f"   ... ({len(cats)} total)")

        # 5. Markets
        print(f"\n{'=' * 70}")
        print("5. Markets:")
        r = client.post(f"{BASE_API}/markets", headers=HEADERS)
        for m in r.json()["data"]["markets"]:
            print(f"   id={m['id']} symbol={m['symbol']} name={m['name']}")

        # 6. Search by issuer
        print(f"\n{'=' * 70}")
        print("6. Search by issuer (EQNR):")
        r = client.post(f"{BASE_API}/list?issuer=EQNR&fromDate=2026-03-01&toDate=2026-03-25", headers=HEADERS)
        msgs = r.json()["data"]["messages"]
        print(f"   {len(msgs)} messages")
        for m in msgs[:3]:
            print(f"   [{m['publishedTime']}] {m['title'][:60]}")

        # 7. Search by title keyword
        print(f"\n{'=' * 70}")
        print("7. Search by title keyword ('dividend'):")
        r = client.post(f"{BASE_API}/list?messageTitle=dividend&fromDate=2026-03-01&toDate=2026-03-25", headers=HEADERS)
        msgs = r.json()["data"]["messages"]
        print(f"   {len(msgs)} messages")
        for m in msgs[:3]:
            print(f"   [{m['publishedTime']}] {m['issuerSign']} - {m['title'][:60]}")


if __name__ == "__main__":
    demo()
