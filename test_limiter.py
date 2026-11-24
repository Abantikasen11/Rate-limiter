"""Run with the server up: python test_limiter.py"""
import requests

URL = "http://localhost:8000/"

def hit(key):
    return requests.get(URL, headers={"X-API-Key": key})

print("Client A sends 8 requests (limit is 5 by default):")
for i in range(1, 9):
    res = hit("client-a")
    print(f"  #{i}: {res.status_code} remaining={res.headers.get('X-RateLimit-Remaining')} "
          f"retry-after={res.headers.get('Retry-After')}")

print("Client B is unaffected by A:")
print("  ", hit("client-b").status_code)