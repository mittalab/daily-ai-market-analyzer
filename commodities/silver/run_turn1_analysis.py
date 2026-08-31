import os
import json
import logging
import pytz
from datetime import datetime, timedelta
from dotenv import load_dotenv
from supabase import create_client, Client
import anthropic

from prompts.turn1_commodity_prompt import TURN_1_COMMODITY_SYSTEM_PROMPT

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger("Turn1CommodityOrchestrator")

IST = pytz.timezone("Asia/Kolkata")


class Turn1Orchestrator:
    def __init__(self, supabase_client: Client, anthropic_client: anthropic.Anthropic):
        self.supabase = supabase_client
        self.claude = anthropic_client

    def fetch_session_payload(self, session_date_str: str) -> dict:
        """Fetches frozen turn_1_payload from Supabase Layer 2 table."""
        logger.info(f"Fetching Turn 1 payload for session date: {session_date_str}...")
        res = self.supabase.table("silver_pipeline_data") \
            .select("turn_1_payload") \
            .eq("session_date", session_date_str) \
            .execute()

        if not res.data or "turn_1_payload" not in res.data[0]:
            raise ValueError(f"No turn_1_payload found in DB for session date {session_date_str}.")

        return res.data[0]["turn_1_payload"]

    def execute_turn_1(self, session_date_str: str) -> dict:
        """Sends payload to Claude and gets structured macro intelligence."""
        payload = self.fetch_session_payload(session_date_str)

        user_message = f"""
Here is the EOD Data Payload for session {session_date_str}:

{json.dumps(payload, indent=2)}

Perform Turn 1 Macro Intelligence Analysis and return JSON matching the specification.
"""

        logger.info("Calling Claude API (claude-3-5-sonnet) for Turn 1 analysis...")

        response = self.claude.messages.create(
            model="claude-3-5-sonnet-20241022",
            max_tokens=1500,
            temperature=0.2, # Low temperature for consistent trading judgment
            system=TURN_1_COMMODITY_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}]
        )

        raw_response_text = response.content[0].text.strip()

        # Sanitize and parse JSON response
        if raw_response_text.startswith("```json"):
            raw_response_text = raw_response_text.split("```json")[1].split("```")[0].strip()
        elif raw_response_text.startswith("```"):
            raw_response_text = raw_response_text.split("```")[1].split("```")[0].strip()

        analysis_json = json.loads(raw_response_text)
        logger.info("Successfully received and parsed Turn 1 response from Claude!")

        return analysis_json

    def save_analysis_to_db(self, session_date_str: str, analysis_json: dict):
        """Persists turn_1_analysis into Supabase silver_pipeline_data table."""
        logger.info(f"Saving Turn 1 analysis into Supabase for {session_date_str}...")
        self.supabase.table("silver_pipeline_data") \
            .update({"turn_1_analysis": analysis_json}) \
            .eq("session_date", session_date_str) \
            .execute()
        logger.info("Turn 1 analysis saved successfully!")


def main():
    load_dotenv()
    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    anthropic_api_key = os.getenv("ANTHROPIC_API_KEY")

    if not all([supabase_url, supabase_key, anthropic_api_key]):
        logger.error("Missing required environment variables (SUPABASE or ANTHROPIC).")
        return

    supabase = create_client(supabase_url, supabase_key)
    claude = anthropic.Anthropic(api_key=anthropic_api_key)

    orchestrator = Turn1Orchestrator(supabase_client=supabase, anthropic_client=claude)

    # Determine target session date (defaults to latest trading session)
    now_ist = datetime.now(IST)
    target_date = (now_ist - timedelta(days=1)).date() if now_ist.hour < 6 else now_ist.date()
    target_date_str = target_date.strftime("%Y-%m-%d")

    try:
        turn1_result = orchestrator.execute_turn_1(target_date_str)
        orchestrator.save_analysis_to_db(target_date_str, turn1_result)

        print("\n" + "="*70)
        print(f"TURN 1 ANALYSIS COMPLETED FOR {target_date_str}")
        print("="*70)
        print(json.dumps(turn1_result, indent=2))
        print("="*70 + "\n")

    except Exception as e:
        logger.error(f"Turn 1 execution failed: {e}", exc_info=True)


if __name__ == "__main__":
    main()