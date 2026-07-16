import asyncio
import aiosqlite
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver


async def main():
    async with aiosqlite.connect("checkpointer.db") as conn:
        checkpointer = AsyncSqliteSaver(conn)

        async with conn.execute("SELECT DISTINCT thread_id FROM checkpoints") as cursor:
            rows = await cursor.fetchall()
            threads = [r[0] for r in rows]

        found_summary = False
        for thread_id in threads:
            config = {"configurable": {"thread_id": thread_id}}
            try:
                tup = await checkpointer.aget_tuple(config)
                if tup and tup.checkpoint:
                    state = tup.checkpoint.get("channel_values", {})
                    if "summary" in state:
                        print(f"--- Thread ID: {thread_id} ---")
                        print(f"SUMMARY FOUND: {state['summary']}\n")
                        found_summary = True
            except Exception:
                pass

        if not found_summary:
            print("No summaries found in ANY thread.")


if __name__ == "__main__":
    asyncio.run(main())
