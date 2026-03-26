# main.py
from dotenv import load_dotenv
load_dotenv()

import asyncio
import argparse
import json
import uuid
from langgraph.types import Command
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from config import CHECKPOINT_DB, DEFAULT_EFFORT
from pipeline.graph import build_graph

EFFORT_CHOICES = ["low", "medium", "high"]


def _print_progress(event: dict) -> None:
    for node_name, updates in event.items():
        if node_name == "__end__":
            continue
        status = updates.get("status", "")
        attempts_info = ""
        if "script_attempts" in updates:
            attempts_info = f" (script attempt {updates['script_attempts']})"
        elif "code_attempts" in updates:
            attempts_info = f" (code attempt {updates['code_attempts']})"
        print(f"  [{node_name}]{attempts_info} → {status or 'done'}")


def _handle_interrupt(interrupt_value: str) -> Command:
    print("\n" + interrupt_value)
    print("\nEnter action (retry_script / retry_code / abort):")
    action = input("  action: ").strip()
    guidance = ""
    if action in ("retry_script", "retry_code"):
        guidance = input("  guidance: ").strip()
    return Command(resume={"action": action, "guidance": guidance})


async def run(topic: str, effort: str, thread_id: str) -> None:
    print(f"\nChalkboard — topic: {topic!r} | effort: {effort} | run: {thread_id}\n")

    async with AsyncSqliteSaver.from_conn_string(CHECKPOINT_DB) as checkpointer:
        graph = build_graph(checkpointer=checkpointer)
        config = {"configurable": {"thread_id": thread_id}}
        input_state = {"topic": topic, "effort_level": effort}

        while True:
            async for event in graph.astream(input_state, config=config, stream_mode="updates"):
                _print_progress(event)

                # Check for interrupt
                if "__interrupt__" in event:
                    interrupt_value = event["__interrupt__"][0].value
                    resume_cmd = _handle_interrupt(interrupt_value)
                    input_state = resume_cmd
                    break
            else:
                # Stream completed without interrupt — done
                break

    print("\nDone. Check output/ for generated files.")


def main():
    parser = argparse.ArgumentParser(description="Chalkboard — AI animation pipeline")
    parser.add_argument("--topic", required=True, help="Topic to explain")
    parser.add_argument("--effort", choices=EFFORT_CHOICES, default=DEFAULT_EFFORT)
    parser.add_argument("--run-id", default=None, help="Resume a previous run by ID")
    args = parser.parse_args()

    thread_id = args.run_id or str(uuid.uuid4())
    asyncio.run(run(args.topic, args.effort, thread_id))


if __name__ == "__main__":
    main()
