"""Small local load generator; never requests the Finep website."""

import argparse
from concurrent.futures import ThreadPoolExecutor

import httpx

parser = argparse.ArgumentParser()
parser.add_argument("--requests", type=int, default=200)
args = parser.parse_args()


def request(_):
    with httpx.Client(timeout=10) as client:
        response = client.get("http://localhost:8000/opportunities")
        response.raise_for_status()
        return response.status_code


with ThreadPoolExecutor(max_workers=5) as pool:
    results = list(pool.map(request, range(args.requests)))
print(f"Completed {len(results)} requests")
