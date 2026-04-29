import os
import time
import sys
from dotenv import load_dotenv
from google import genai

def run_test():
    load_dotenv(".env")
    vertex_api_key = os.getenv("VERTEX_API_KEY")
    model_name = "gemini-3-flash-preview"
    
    print("=" * 60)
    print(f"STRESS TEST: {model_name} with VERTEX_API_KEY")
    print("=" * 60)

    client = genai.Client(api_key=vertex_api_key)
    
    success = 0
    for i in range(1, 21):
        try:
            sys.stdout.write(f"  Request {i:02d}: ")
            sys.stdout.flush()
            start = time.time()
            client.models.generate_content(model=model_name, contents="ping")
            print(f"OK ({time.time() - start:.2f}s)")
            success += 1
        except Exception as e:
            print(f"FAIL | {str(e)[:100]}...")
            if "429" in str(e) or "quota" in str(e).lower():
                break
    
    print(f"\nFinal Result: {success}/20 successful")

if __name__ == "__main__":
    run_test()
