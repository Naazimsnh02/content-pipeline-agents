import os
import sys
import asyncio
from pathlib import Path
from pydantic import BaseModel, Field

# Add project root to sys.path
root_dir = Path(__file__).parent.parent
sys.path.append(str(root_dir))

from shared.config import settings
from google.adk.agents import Agent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai.types import Content, Part

async def run_test_query(runner, session_service, user_id):
    session = await session_service.create_session(app_name=settings.app_name, user_id=user_id)
    response_text = ""
    async for event in runner.run_async(
        user_id=user_id,
        session_id=session.id,
        new_message=Content(role="user", parts=[Part(text="Check connection")])
    ):
        if event.is_final_response() and event.content:
            for part in event.content.parts:
                if hasattr(part, "text") and part.text:
                    response_text += part.text
    return response_text

def test_provider():
    print(f"--- LLM Provider Test ---")
    print(f"LLM_PROVIDER: {settings.llm_provider}")
    print(f"Active Model: {settings.active_model}")
    
    if settings.llm_provider == "openai_compatible":
        print(f"Base URL:     {settings.openai_api_base}")
        print(f"OpenAI Model: {settings.openai_model}")

    test_agent = Agent(
        name="test_connection_agent",
        model=settings.active_model,
        description="A simple agent to test model connectivity",
        instruction="Respond with 'CONNECTION_SUCCESSFUL' and nothing else."
    )

    session_service = InMemorySessionService()
    runner = Runner(
        agent=test_agent,
        app_name=settings.app_name,
        session_service=session_service,
    )

    print("\nRunning test query...")
    try:
        user_id = "test_user"
        response = asyncio.run(run_test_query(runner, session_service, user_id))
        print(f"\nResponse from Model: {response}")
        
        if "CONNECTION_SUCCESSFUL" in str(response):
            print("\n✅ SUCCESS: The OpenAI-compatible provider is working correctly!")
        else:
            print("\n⚠️ PARTIAL SUCCESS: Received a response, but it didn't match the instruction.")
            print(f"Got: '{response}'")
            
    except Exception as e:
        print(f"\n❌ FAILED: Error during model call: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_provider()
