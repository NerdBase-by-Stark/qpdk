"""Timer:Start on a running timer must RESTART it, not stack schedules.

A Q-SYS Timer object holds one pending schedule. Start-per-firing is a
field-proven idiom (deployed plugins restart a drain timer on every firing).
MockTimer.Start once left the previous repeating event active in the
scheduler heap — every restart leaked a live event and Start-per-firing
idioms grew the event count exponentially, hanging tick().
"""
from qpdk.mock_runtime.timer import MockTimer, TimerScheduler


def test_start_while_running_replaces_schedule():
    sched = TimerScheduler()
    t = MockTimer(sched)
    fires = []
    t.EventHandler = lambda timer: fires.append(sched._current_time)

    t.Start(1.0)
    t.Start(1.0)
    t.Start(1.0)  # restarts, must NOT stack three repeating events

    sched.tick(3.0)
    # One schedule -> fires at 1, 2, 3. The leak gave 3 events -> 9 fires.
    assert len(fires) == 3, f"expected 3 fires from one schedule, got {len(fires)}"


def test_start_per_firing_does_not_grow_event_queue():
    sched = TimerScheduler()
    t = MockTimer(sched)
    fires = []

    def handler(timer):
        fires.append(sched._current_time)
        t.Start(0.2)  # drain idiom: restart on every firing

    t.EventHandler = handler
    t.Start(0.2)
    sched.tick(10.0)

    # 10s / 0.2s = 50 firings, and the queue must stay O(1).
    assert len(fires) == 50, f"expected 50 fires, got {len(fires)}"
    live = [e for e in sched._event_queue if e.active]
    assert len(live) <= 1, f"event queue leaked: {len(live)} live events"


def test_restart_resets_the_period():
    sched = TimerScheduler()
    t = MockTimer(sched)
    fires = []
    t.EventHandler = lambda timer: fires.append(sched._current_time)

    t.Start(1.0)
    sched.tick(0.6)      # 0.6s in: no fire yet
    t.Start(1.0)         # restart -> next fire at 1.6, not 1.0
    sched.tick(0.6)      # now at 1.2: still nothing
    assert fires == []
    sched.tick(0.5)      # now at 1.7: fired once at 1.6
    assert len(fires) == 1 and abs(fires[0] - 1.6) < 1e-9


def test_stop_then_start_still_works():
    sched = TimerScheduler()
    t = MockTimer(sched)
    fires = []
    t.EventHandler = lambda timer: fires.append(sched._current_time)

    t.Start(1.0)
    t.Stop()
    sched.tick(2.0)
    assert fires == []
    t.Start(0.5)
    sched.tick(1.0)
    assert len(fires) == 2


# ---------------------------------------------------------------------------
# :IsRunning() — Timer.htm: ":IsRunning() Returns true if the timer is
# running".
# ---------------------------------------------------------------------------

def test_is_running_lifecycle():
    sched = TimerScheduler()
    t = MockTimer(sched)
    assert t.IsRunning() is False          # never started
    t.Start(1.0)
    assert t.IsRunning() is True           # running after Start
    t.Stop()
    assert t.IsRunning() is False          # stopped
    t.Start(0.5)
    assert t.IsRunning() is True           # restartable


def test_is_running_true_across_repeat_firings():
    sched = TimerScheduler()
    t = MockTimer(sched)
    t.EventHandler = lambda timer: None
    t.Start(1.0)
    sched.tick(3.0)                        # fires at 1, 2, 3 — still repeating
    assert t.IsRunning() is True


def test_is_running_from_lua_colon_call():
    """The wrapped Lua timer object must expose t:IsRunning() (and t.IsRunning())."""
    from qpdk.mock_runtime.runtime import QSYSRuntime

    rt = QSYSRuntime(design_time=True)
    rt._init_lua()
    assert rt._lua.eval("""
    (function()
        local t = Timer.New()
        local before = t:IsRunning()
        t:Start(1.0)
        local during = t:IsRunning()
        t:Stop()
        local after = t.IsRunning()  -- dot-call form must work too
        return (before == false) and (during == true) and (after == false)
    end)()
    """) is True
