"""One-time IKS cutover fix: merge duplicate notebooks into the user's originals.

After migration + nightly wiki recompile, 7 names ended up with TWO ON notebooks:
  - the user's ORIGINAL (holds chat history, no ob_thread_id)
  - a fresh folder-synced TWIN (ob_thread_id set, 0 messages)
Plus "XR devices and technologies": only the original, thread exists, unlinked.

Fix (non-lossy): set the original's ob_thread_id to the migrated thread so it
becomes the canonical folder-synced notebook (reconcile keys on ob_thread_id →
it updates in place, never recreates a twin), then delete the empty twin.
Ordering: link original FIRST, then delete twin (a mid-run reconcile is safe).
Idempotent.
"""
import asyncio

# name -> (original_id, migrated_thread_id, twin_id_or_None)
PLAN = {
    "AI Harness":      ("notebook:g7x53zucgdvefad9q26c", "de439808-9cc6-4efc-8ba5-41352ead4271", "notebook:3euy02z6c9s5pvmnz3m5"),
    "Game engine":     ("notebook:k4s643pcyzpatvcx7j8r", "0ca8d824-a278-4818-b2fc-14ef2e2b3ff7", "notebook:v0l2sruwv6gav0yw0lkq"),
    "DGX Spark":       ("notebook:eh3tyr1stgwi428ij2g7", "776f4e22-24ce-4d1a-9547-af3c58687f55", "notebook:5ldctzg6uhb8vmsmvg89"),
    "Fitness":         ("notebook:1wievnzdhk0lx6az6cim", "33e4c5c8-bf09-41a6-9f37-3667679d4b8a", "notebook:pl0xf9g5n9gg8lv29j4d"),
    "Holography":      ("notebook:wpcp58q0t09gleuxzlou", "5f44e80f-9d4d-4974-be4f-5a2fba4f378b", "notebook:nsnbiqsy39fcfr523kup"),
    "Digital Twin":    ("notebook:aw8e7xwb2tl0v4mya3ck", "05dbacab-cdf2-4c32-84e6-51187f2a33fe", "notebook:p4u8l7fw1p709qgvr83s"),
    "machine-learning":("notebook:7b88wlet9kv1lv50afzu", "5eba0e6c-c9d9-4d44-b6bc-87f58b1cc11d", "notebook:ae6gvdvo3aumpwnfd4qs"),
    "XR devices and technologies": ("notebook:llx079vr0f3roqz6kmyb", "5ed44713-f6c9-4dd3-adc4-73a771b3f7e8", None),
}


async def main():
    from open_notebook.domain.notebook import Notebook

    for name, (orig_id, tid, twin_id) in PLAN.items():
        orig = await Notebook.get(orig_id)
        if orig is None:
            print(f"SKIP {name}: original {orig_id} not found")
            continue
        # 1) link original -> migrated thread (idempotent)
        if getattr(orig, "ob_thread_id", None) != tid:
            orig.ob_thread_id = tid
            await orig.save()
            print(f"LINKED  {name:32} {orig_id} -> {tid}")
        else:
            print(f"already {name:32} {orig_id} -> {tid}")
        # 2) delete the empty twin (if any)
        if twin_id:
            twin = await Notebook.get(twin_id)
            if twin is None:
                print(f"  twin {twin_id} already gone")
            else:
                await twin.delete()
                print(f"  DELETED twin {twin_id}")


if __name__ == "__main__":
    asyncio.run(main())
