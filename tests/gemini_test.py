import os
import time
import sys
import logging
from datetime import datetime
from dotenv import load_dotenv

# Try to import the SDK
try:
    from google import genai
    from google.genai import types
except ImportError:
    print("Error: google-genai SDK not found. Please run: pip install google-genai")
    sys.exit(1)

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("gemini-test")

def run_test():
    # Load .env file
    if os.path.exists(".env"):
        load_dotenv(".env")
        logger.info("Loaded .env file")
    else:
        logger.warning(".env file not found in current directory")

    # Get configuration
    api_key = os.getenv("GOOGLE_API_KEY")
    model_name = os.getenv("GEMINI_MODEL", "gemini-3-flash-preview")
    
    # User mentioned they are using openai_compatible, let's show what we found
    provider = os.getenv("LLM_PROVIDER", "gemini")
    
    print("=" * 60)
    print("GEMINI MODE TEST SCRIPT")
    print("=" * 60)
    print(f"Current Provider in .env: {provider}")
    print(f"Targeting Model:        {model_name}")
    print(f"API Key Found:          {'Yes' if api_key else 'No'}")
    if api_key:
        print(f"API Key Preview:        {api_key[:8]}...{api_key[-4:]}")
    print("=" * 60)

    if not api_key:
        logger.error("GOOGLE_API_KEY not found. Please check your .env file.")
        return

    # Initialize Client
    try:
        client = genai.Client(api_key=api_key)
        logger.info("GenAI Client initialized")
    except Exception as e:
        logger.error(f"Failed to initialize GenAI Client: {e}")
        return

    # 1. Connectivity Test
    print("\n[1/2] Connectivity Test...")
    try:
        start_time = time.time()
        response = client.models.generate_content(
            model=model_name,
            contents="Confirm connectivity. Reply with 'Connected to Gemini 3 Flash'.",
        )
        duration = time.time() - start_time
        print(f"Response: {response.text.strip()}")
        print(f"Latency:  {duration:.2f}s")
    except Exception as e:
        logger.error(f"Connectivity test failed: {e}")
        if "404" in str(e):
            print("\nTIP: Model not found. Gemini 3 Flash might not be available in your region or the name is incorrect.")
        return

    # 2. Rate Limit Test
    print(f"\n[2/2] Rate Limit Test (Attempting 15 rapid-fire requests)...")
    success_count = 0
    fail_count = 0
    
    for i in range(1, 16):
        try:
            sys.stdout.write(f"  Request {i:02d}: ")
            sys.stdout.flush()
            
            start_time = time.time()
            # Simple low-token request to minimize cost but test throughput
            response = client.models.generate_content(
                model=model_name,
                contents=f"What is {i} + 1? Just the number.",
            )
            duration = time.time() - start_time
            
            result = response.text.strip()
            print(f"Success | Result: {result} | Time: {duration:.2f}s")
            success_count += 1
            
        except Exception as e:
            err_msg = str(e)
            if "429" in err_msg or "quota" in err_msg.lower():
                print(f"FAILED (Rate Limit/Quota) | {err_msg[:100]}...")
                fail_count += 1
                break # Usually if you hit 429 once, you're done for the window
            else:
                print(f"FAILED (Other Error) | {err_msg[:100]}...")
                fail_count += 1
        
        # Optional: very small sleep to avoid instant IP blocking if that's a concern
        # time.sleep(0.1)

    print("\n" + "=" * 60)
    print("TEST SUMMARY")
    print("=" * 60)
    print(f"Total Requests: {i if fail_count > 0 else 15}")
    print(f"Successful:     {success_count}")
    print(f"Failed:         {fail_count}")
    
    if fail_count > 0:
        print("\nNote: Rate limit was hit. Check your Google AI Studio / Vertex AI quota settings.")
    else:
        print("\nNote: No rate limits hit for 15 requests. The model appears to be working smoothly.")
    print("=" * 60)

if __name__ == "__main__":
    run_test()
