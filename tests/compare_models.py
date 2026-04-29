import os
import time
import sys
import logging
from dotenv import load_dotenv
from google import genai

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("gemini-rate-test")

def test_model(model_name):
    load_dotenv(".env")
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        print("GOOGLE_API_KEY not found.")
        return

    client = genai.Client(api_key=api_key)
    
    print(f"\n" + "=" * 60)
    print(f"TESTING MODEL: {model_name}")
    print("=" * 60)

    success_count = 0
    fail_count = 0
    
    # Try 10 requests rapidly
    for i in range(1, 11):
        try:
            sys.stdout.write(f"  Request {i:02d}: ")
            sys.stdout.flush()
            
            start_time = time.time()
            response = client.models.generate_content(
                model=model_name,
                contents="Hi",
            )
            duration = time.time() - start_time
            print(f"OK ({duration:.2f}s)")
            success_count += 1
            
        except Exception as e:
            err_msg = str(e)
            print(f"FAILED | {err_msg[:100]}...")
            fail_count += 1
            if "429" in err_msg or "quota" in err_msg.lower():
                break
    
    print(f"Result: {success_count}/10 successful")
    return success_count

if __name__ == "__main__":
    # Test a few models
    models_to_test = [
        "gemini-3-flash-preview", 
        "gemini-3.1-flash-lite-preview",
        "gemini-2.5-flash-lite"
    ]
    
    for m in models_to_test:
        test_model(m)
        print("\nWaiting 5 seconds before next model test to reset burst limits...")
        time.sleep(5)
