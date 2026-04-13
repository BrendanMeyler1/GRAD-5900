import json
import httpx

API_URL = "http://127.0.0.1:8000/api"

# 1. Load synthetic data
with open("tests/fixtures/synthetic_persona.json") as f:
    persona = json.load(f)

with open("tests/fixtures/synthetic_listing.json") as f:
    listing = json.load(f)

# 2. Upload Persona (Not strictly required if passed to scan, but good measure)
# We mock the upload endpoint since it accepts a multipart file by posting directly 
# to the scan endpoint which accepts a Persona body.

# 3. Seed Job Queue
scan_payload = {
    "listings": [listing],
    "persona": persona,
    "use_llm": False,      # Fast mode
    "max_results": 10
}

print("Seeding job queue...")
resp = httpx.post(f"{API_URL}/jobs/scan", json=scan_payload)
print(f"Scan response ({resp.status_code}):", json.dumps(resp.json(), indent=2))

# 4. Verify Queue
resp = httpx.get(f"{API_URL}/jobs/queue")
print(f"\nQueue state ({resp.status_code}):", json.dumps(resp.json(), indent=2))
