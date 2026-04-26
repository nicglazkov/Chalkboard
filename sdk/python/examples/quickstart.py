"""End-to-end quickstart: create → poll → download.

Usage:
    export CHALKBOARD_API_KEY=chk_live_...
    python quickstart.py "How hash tables work"
"""
import os
import sys
from pathlib import Path

from chalkboard import ChalkboardClient, ChalkboardError


def main():
    if "CHALKBOARD_API_KEY" not in os.environ:
        print("Set CHALKBOARD_API_KEY first.", file=sys.stderr)
        sys.exit(1)

    topic = " ".join(sys.argv[1:]) or "Explain B-trees in 90 seconds"

    with ChalkboardClient(api_key=os.environ["CHALKBOARD_API_KEY"]) as client:
        print(f"Submitting: {topic!r}")
        try:
            job = client.create_job(
                topic=topic,
                effort="medium",
                theme="chalkboard",
                idempotency_key=client.fresh_idempotency_key(),
            )
        except ChalkboardError as e:
            print(f"Create failed: {e.status} {e.detail}", file=sys.stderr)
            sys.exit(1)

        print(f"Job {job.id} dispatched (mode={job.mode}). Waiting…")
        final = client.wait_for_completion(job.id, timeout=900)
        print(f"Final status: {final.status}")

        if final.status != "completed":
            print(f"Run did not complete: {final.error}", file=sys.stderr)
            sys.exit(2)

        out = Path(f"chalkboard-{final.id}.mp4")
        client.download_file(final.id, "final.mp4", out_path=out)
        print(f"Wrote {out} ({out.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
