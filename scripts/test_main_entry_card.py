"""Test MainEntryCard with simulated states.

Run: python scripts/test_main_entry_card.py

This script directly uses MainEntryCard's internal Qt runtime.
"""

import sys
import os
import time
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main():
    from app.popup.main_entry_card import MainEntryCard, CardState
    from app.popup.task_progress import TaskProgressSnapshot

    # Progress snapshots for RUNNING state
    progress_snapshots = [
        TaskProgressSnapshot(
            task_id="test-001",
            previous_step="尚未开始",
            current_step="打开浏览器",
            next_step="输入网址",
            status="running",
            current_index=1,
            total_steps=5,
        ),
        TaskProgressSnapshot(
            task_id="test-001",
            previous_step="打开浏览器",
            current_step="输入网址",
            next_step="搜索内容",
            status="running",
            current_index=2,
            total_steps=5,
        ),
        TaskProgressSnapshot(
            task_id="test-001",
            previous_step="输入网址",
            current_step="搜索内容",
            next_step="查看结果",
            status="running",
            current_index=3,
            total_steps=5,
        ),
    ]

    # State sequence to simulate (auto-play)
    state_sequence = [
        ("IDLE", 2),
        ("RECORDING", 3),
        ("IDLE", 1),
        ("CONFIRMING", 2),
        ("RUNNING", 1),
        ("COMPLETED", 3),
        ("IDLE", 2),
        ("RUNNING", 1),
        ("FAILED", 3),
        ("IDLE", 2),
        ("RUNNING", 1),
        ("CANCELLED", 3),
    ]

    def switch_state(state):
        print(f"[Test] Switching to: {state}")

        if state == "IDLE":
            card.set_state(CardState.IDLE)

        elif state == "RECORDING":
            card.set_state(CardState.RECORDING)

        elif state == "CONFIRMING":
            card.set_state(CardState.CONFIRMING)

        elif state == "RUNNING":
            card.set_state(CardState.RUNNING)
            card.update_progress(progress_snapshots[0])

        elif state == "COMPLETED":
            card.set_state(CardState.COMPLETED)
            snapshot = TaskProgressSnapshot(
                task_id="test-001",
                previous_step="搜索内容",
                current_step="任务执行完成",
                next_step="无",
                status="completed",
                current_index=5,
                total_steps=5,
                status_message="执行成功",
            )
            card.update_progress(snapshot)

        elif state == "FAILED":
            card.set_state(CardState.FAILED)
            snapshot = TaskProgressSnapshot(
                task_id="test-001",
                previous_step="输入网址",
                current_step="执行失败",
                next_step="无",
                status="failed",
                current_index=2,
                total_steps=5,
                status_message="网络连接超时",
            )
            card.update_progress(snapshot)

        elif state == "CANCELLED":
            card.set_state(CardState.CANCELLED)
            snapshot = TaskProgressSnapshot(
                task_id="test-001",
                previous_step="打开浏览器",
                current_step="任务已取消",
                next_step="无",
                status="cancelled",
                current_index=1,
                total_steps=5,
                status_message="用户取消",
            )
            card.update_progress(snapshot)

    def auto_play():
        """Auto play state sequence"""
        step = 0
        while step < len(state_sequence):
            state, delay = state_sequence[step]
            switch_state(state)
            step += 1
            time.sleep(delay)

        # Done, close after 2 seconds
        time.sleep(2)
        print("[Test] Auto-play complete, closing...")
        card.close()

    # Create card - it will start its own Qt runtime
    card = MainEntryCard(
        on_submit=lambda instruction: print(f"[Test] Submitted: {instruction}"),
        on_cancel=lambda: print("[Test] Cancelled"),
    )

    print("=" * 60)
    print("Main Entry Card Test")
    print("=" * 60)
    print("The card window will appear in the bottom-right corner.")
    print("States will auto-cycle through:")
    print("  IDLE -> RECORDING -> CONFIRMING -> RUNNING -> COMPLETED")
    print("  IDLE -> RUNNING -> FAILED")
    print("  IDLE -> RUNNING -> CANCELLED")
    print("=" * 60)
    print("You can also:")
    print("  - Click '预设指令' button to test preset panel")
    print("  - Click '语音输入' button (simulated)")
    print("  - Click '确认执行' button")
    print("  - Click '取消任务' button during RUNNING state")
    print("=" * 60)

    # Start card
    card.start()

    # Wait for window to be ready
    time.sleep(1)

    # Start auto-play in background
    play_thread = threading.Thread(target=auto_play, daemon=True)
    play_thread.start()

    # Keep main thread alive - the card's Qt runtime handles the UI
    # We just need to wait for the play thread to finish
    play_thread.join(timeout=60)

    print("[Test] Test finished")


if __name__ == "__main__":
    main()