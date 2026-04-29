"""
Quick smoke test for Phase 3 & 4 new tools.
Run with: .venv\Scripts\python.exe demo/test_phase3_4.py
"""
import sys
sys.path.insert(0, '.')

print("=== Testing Ideas Agent new sources ===")

from agents.ideas.tools import fetch_reddit_trending, fetch_rss_feeds, fetch_youtube_trending

r = fetch_reddit_trending("tech", 3)
print(f"Reddit:  {r['count']} topics, source={r['source']}")
if r.get("topics"):
    print(f"  Top: {r['topics'][0]['title'][:70]}")
elif r.get("error"):
    print(f"  Error: {r['error']}")

f = fetch_rss_feeds("tech", 3)
print(f"RSS:     {f['count']} topics, source={f['source']}")
if f.get("topics"):
    print(f"  Top: {f['topics'][0]['title'][:70]}")
elif f.get("error"):
    print(f"  Error: {f['error']}")

y = fetch_youtube_trending("tech", 3)
print(f"YouTube: {y.get('count', 0)} topics, source={y['source']}")
if y.get("topics"):
    print(f"  Top: {y['topics'][0]['title'][:70]}")
elif y.get("error"):
    print(f"  Error (expected without API key): {y['error']}")

print()
print("=== Testing A/B evaluate_hook_ab (Gemini call) ===")
from agents.script.tools import evaluate_hook_ab

result = evaluate_hook_ab(
    hook_a="94% of radiologists just got outperformed by an AI.",
    hook_b="What if your doctor is about to be replaced by a machine?",
    script_a="94% of radiologists just got outperformed by an AI. Here is why this changes medicine forever.",
    script_b="What if your doctor is about to be replaced? New AI can read MRIs better than most specialists.",
    niche="tech",
    topic_title="AI in Medical Imaging",
)
print(f"Winner: Variant {result.get('winner')}")
print(f"Reasoning: {result.get('reasoning', 'N/A')[:100]}")
print(f"Evaluated: {result.get('evaluated')}")
if result.get("error"):
    print(f"Error (expected if no Gemini key): {result['error']}")

print()
print("=== All tests complete ===")
