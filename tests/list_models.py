import os
from dotenv import load_dotenv
from google import genai

load_dotenv()

def list_gemini_models():
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        print("GOOGLE_API_KEY not found.")
        return

    client = genai.Client(api_key=api_key)
    
    print("=" * 60)
    print("AVAILABLE GEMINI MODELS")
    print("=" * 60)
    
    try:
        # Use the models.list method
        for model in client.models.list():
            print(f"Name: {model.name}")
            print(f"  Display Name: {model.display_name or 'N/A'}")
            print(f"  Description:  {model.description or 'N/A'}")
            print("-" * 30)
    except Exception as e:
        print(f"Error listing models: {e}")

if __name__ == "__main__":
    list_gemini_models()
