"""
Mock Timer API

Simulates Q-SYS Timer for plugin testing.
Uses a virtual clock that can be advanced programmatically.
"""

import heapq
from collections.abc import Callable
from dataclasses import dataclass, field


@dataclass(order=True)
class ScheduledEvent:
    """A scheduled timer event."""
    trigger_time: float
    event_id: int = field(compare=False)
    callback: Callable = field(compare=False)
    interval: float = field(compare=False, default=0.0)
    active: bool = field(compare=False, default=True)


class MockTimer:
    """
    Mock implementation of a Q-SYS Timer.

    Usage:
        timer = MockTimer()
        timer.EventHandler = lambda t: print("Timer fired!")
        timer.Start(1.0)  # 1 second interval
    """

    def __init__(self, scheduler: "TimerScheduler | None" = None):
        self._scheduler = scheduler or _global_scheduler
        self._event: ScheduledEvent | None = None
        self._interval: float = 0.0
        self._event_handler: Callable | None = None

    @property
    def EventHandler(self) -> Callable | None:
        return self._event_handler

    @EventHandler.setter
    def EventHandler(self, handler: Callable | None) -> None:
        self._event_handler = handler

    def Start(self, interval: float) -> None:
        """Start the timer with the given interval.

        A Q-SYS Timer object holds ONE pending schedule: calling Start on a
        running timer RESTARTS it (field-proven — cisco-modular's drain timer
        calls Start(WRITE_DELAY) on every firing and runs fine on real Cores;
        its pulse machinery documents "Start(0.1) restarts" hold-off
        semantics). Before this fix the previous event was left active in the
        scheduler heap, so every restart leaked a live repeating event —
        Start-per-firing idioms multiplied events exponentially and tick()
        span forever (caught 2026-07-09 by the first device-replay gauntlet
        run crossing cisco-modular's 15s poll burst).
        """
        self._interval = interval
        self.Stop()
        self._schedule()

    def Stop(self) -> None:
        """Stop the timer."""
        if self._event:
            self._event.active = False
            self._event = None

    def IsRunning(self) -> bool:
        """Return True if the timer is running.

        Evidence ladder: KB-confirmed — Timer.htm ":IsRunning() Returns true
        if the timer is running" (a hardware probe CONFIRMED, community-corpus/
        PLATFORM-CLAIMS-VERDICTS.md §1). Running = a live schedule exists:
        true after Start(), false before any Start and after Stop().
        Added V5 2026-07-12 (probe-kit-build.md gap 5 — the mock lacked it
        and probe batteries had to catch the attribute error).
        """
        return self._event is not None and self._event.active

    def _schedule(self) -> None:
        """Schedule the next callback."""
        if self._interval > 0:
            self._event = self._scheduler.schedule(
                self._callback,
                self._interval,
                repeat=self._interval
            )

    def _callback(self) -> None:
        """Internal callback that invokes the event handler."""
        if self._event_handler:
            try:
                self._event_handler(self)
            except Exception as e:
                msg = f"Timer EventHandler error: {e}"
                # Record so a runner can detect the crash instead of it being
                # silently swallowed to stdout (see QPDK-AUDIT-2026-06-02).
                getattr(self._scheduler, "errors", []).append(msg)
                print(msg)


class TimerScheduler:
    """
    Virtual timer scheduler for testing.

    Maintains a priority queue of scheduled events and a virtual clock.
    Time can be advanced manually to trigger events deterministically.
    """

    def __init__(self):
        self._current_time: float = 0.0
        self._event_queue: list[ScheduledEvent] = []
        self._next_event_id: int = 0
        # Async callback errors recorded here (instead of only printed) so a
        # runner can assert no timer crashed during a run.
        self.errors: list[str] = []

    @property
    def current_time(self) -> float:
        """Get the current virtual time."""
        return self._current_time

    def reset(self) -> None:
        """Reset the scheduler."""
        self._current_time = 0.0
        self._event_queue = []
        self._next_event_id = 0
        self.errors = []

    def schedule(self, callback: Callable, delay: float,
                 repeat: float = 0.0) -> ScheduledEvent:
        """Schedule a callback to run after delay seconds."""
        event_id = self._next_event_id
        self._next_event_id += 1

        event = ScheduledEvent(
            trigger_time=self._current_time + delay,
            event_id=event_id,
            callback=callback,
            interval=repeat,
        )
        heapq.heappush(self._event_queue, event)
        return event

    def schedule_at(self, callback: Callable, time: float,
                    repeat: float = 0.0) -> ScheduledEvent:
        """Schedule a callback to run at a specific time."""
        event_id = self._next_event_id
        self._next_event_id += 1

        event = ScheduledEvent(
            trigger_time=time,
            event_id=event_id,
            callback=callback,
            interval=repeat,
        )
        heapq.heappush(self._event_queue, event)
        return event

    def cancel(self, event: ScheduledEvent) -> None:
        """Cancel a scheduled event."""
        event.active = False

    def tick(self, duration: float) -> int:
        """
        Advance time by duration and process all events that should fire.

        Returns the number of events processed.
        """
        target_time = self._current_time + duration
        events_processed = 0

        while self._event_queue and self._event_queue[0].trigger_time <= target_time:
            event = heapq.heappop(self._event_queue)

            if not event.active:
                continue

            # Advance time to the event
            self._current_time = event.trigger_time

            # Execute the callback
            try:
                event.callback()
            except Exception as e:
                msg = f"Timer callback error: {e}"
                self.errors.append(msg)
                print(msg)

            events_processed += 1

            # Reschedule if repeating
            if event.active and event.interval > 0:
                event.trigger_time += event.interval
                heapq.heappush(self._event_queue, event)

        # Advance to target time
        self._current_time = target_time
        return events_processed

    def run_until(self, time: float) -> int:
        """Run until a specific time."""
        if time <= self._current_time:
            return 0
        return self.tick(time - self._current_time)

    def run_next(self) -> bool:
        """Run just the next scheduled event."""
        while self._event_queue:
            event = heapq.heappop(self._event_queue)

            if not event.active:
                continue

            self._current_time = event.trigger_time

            try:
                event.callback()
            except Exception as e:
                msg = f"Timer callback error: {e}"
                self.errors.append(msg)
                print(msg)

            if event.active and event.interval > 0:
                event.trigger_time += event.interval
                heapq.heappush(self._event_queue, event)

            return True

        return False

    def pending_count(self) -> int:
        """Get the number of pending events."""
        return sum(1 for e in self._event_queue if e.active)


# Global scheduler instance
_global_scheduler = TimerScheduler()


class TimerAPI:
    """
    Mock implementation of the Q-SYS Timer global.

    Usage:
        Timer.Create()           # Create a new timer
        Timer.CallAfter(fn, 1.0) # Call function after 1 second
    """

    def __init__(self, scheduler: TimerScheduler | None = None):
        self._scheduler = scheduler or _global_scheduler

    def Create(self) -> MockTimer:
        """Create a new timer."""
        return MockTimer(self._scheduler)

    def New(self) -> MockTimer:
        """Create a new timer (standard Q-SYS OOP constructor).

        Timer.New() is the production Q-SYS API. This is an alias for Create().
        """
        return MockTimer(self._scheduler)

    def CallAfter(self, callback: Callable, delay: float) -> ScheduledEvent:
        """Call a function after a delay (one-shot)."""
        return self._scheduler.schedule(callback, delay, repeat=0.0)

    @staticmethod
    def Call(callback: Callable) -> None:
        """Call a function on the next tick."""
        # In real Q-SYS this defers to next frame
        # For testing, we execute immediately
        try:
            callback()
        except Exception as e:
            print(f"Timer.Call error: {e}")

    @staticmethod
    def Stop(event: ScheduledEvent | MockTimer) -> None:
        """Stop a timer or scheduled event."""
        if isinstance(event, MockTimer):
            event.Stop()
        elif isinstance(event, ScheduledEvent):
            event.active = False

    @property
    def scheduler(self) -> TimerScheduler:
        """Get the underlying scheduler (for testing)."""
        return self._scheduler


def get_global_scheduler() -> TimerScheduler:
    """Get the global timer scheduler."""
    return _global_scheduler


def reset_global_scheduler() -> None:
    """Reset the global timer scheduler."""
    global _global_scheduler
    _global_scheduler = TimerScheduler()
