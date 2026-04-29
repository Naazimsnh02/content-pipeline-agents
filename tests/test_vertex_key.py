import os
import time
import sys
from dotenv import load_dotenv
from google import genai

def run_test():
    load_dotenv(".env")
    vertex_api_key = os.getenv("VERTEX_API_KEY")
    model_name = "gemini-2.5-flash"
    
    print("=" * 60)
    print(f"TESTING VERTEX_API_KEY AS REGULAR API KEY")
    print("=" * 60)

    if not vertex_api_key:
        print("VERTEX_API_KEY not found.")
        return

    try:
        # Try using it as a regular API key (AI Studio path)
        client = genai.Client(api_key=vertex_api_key)
        
        print(f"Targeting Model: {model_name}")
        for i in range(1, 11):
            sys.stdout.write(f"  Request {i:02d}: ")
            sys.stdout.flush()
            start = time.time()
            client.models.generate_content(model=model_name, contents="ping")
            print(f"OK ({time.time() - start:.2f}s)")
        print("Success! This key works as a regular API key.")
    except Exception as e:
        print(f"FAILED: {e}")

if __name__ == "__main__":
    run_test()
