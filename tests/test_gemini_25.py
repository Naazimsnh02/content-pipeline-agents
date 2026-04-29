import os
import time
import sys
from dotenv import load_dotenv
from google import genai

def run_test():
    load_dotenv(".env")
    
    # We will try both AI Studio and Vertex paths if configured
    google_api_key = os.getenv("GOOGLE_API_KEY")
    vertex_api_key = os.getenv("VERTEX_API_KEY")
    project = os.getenv("GOOGLE_CLOUD_PROJECT")
    location = os.getenv("GOOGLE_CLOUD_REGION", "us-central1")
    
    model_name = "gemini-2.5-flash"
    
    print("=" * 60)
    print(f"RATE LIMIT TEST: {model_name}")
    print("=" * 60)

    # Test 1: Using AI Studio (GOOGLE_API_KEY)
    if google_api_key:
        print(f"\n[PHASE 1] Testing via AI Studio (GOOGLE_API_KEY)")
        client_studio = genai.Client(api_key=google_api_key)
        perform_rate_test(client_studio, model_name, "AI Studio")

    # Test 2: Using Vertex AI (VERTEX_API_KEY)
    if vertex_api_key:
        print(f"\n[PHASE 2] Testing via Vertex AI (VERTEX_API_KEY)")
        try:
            # Note: Vertex AI usually uses ADC, but we'll try passing the key if it's a specific API key
            client_vertex = genai.Client(
                vertexai=True,
                project=project,
                location=location,
                api_key=vertex_api_key
            )
            perform_rate_test(client_vertex, model_name, "Vertex AI")
        except Exception as e:
            print(f"Vertex initialization/test failed: {e}")

def perform_rate_test(client, model_name, label):
    success = 0
    for i in range(1, 16):
        try:
            sys.stdout.write(f"  {label} Request {i:02d}: ")
            sys.stdout.flush()
            start = time.time()
            client.models.generate_content(model=model_name, contents="ping")
            print(f"OK ({time.time() - start:.2f}s)")
            success += 1
        except Exception as e:
            print(f"FAIL | {str(e)[:100]}...")
            if "429" in str(e) or "quota" in str(e).lower():
                break
    print(f"Total Success: {success}/15")

if __name__ == "__main__":
    run_test()
