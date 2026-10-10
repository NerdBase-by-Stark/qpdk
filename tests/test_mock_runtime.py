"""Tests for Mock Q-SYS Runtime"""


from qpdk.mock_runtime.controls import MockControl, MockControls
from qpdk.mock_runtime.timer import MockTimer, TimerAPI, TimerScheduler


class TestMockControl:
    """Test cases for MockControl."""

    def test_control_value(self):
        """Control value get/set."""
        ctrl = MockControl(name="Test")

        ctrl.Value = 0.5
        assert ctrl.Value == 0.5
        assert ctrl.value == 0.5

    def test_control_boolean(self):
        """Control boolean get/set."""
        ctrl = MockControl(name="Test")

        ctrl.Boolean = True
        assert ctrl.Boolean is True
        assert ctrl.value == 1.0

        ctrl.Boolean = False
        assert ctrl.Boolean is False
        assert ctrl.value == 0.0

    def test_control_string(self):
        """Control string get/set."""
        ctrl = MockControl(name="Test")

        ctrl.String = "Hello"
        assert ctrl.String == "Hello"

    def test_control_event_handler_lua_write_no_fire(self):
        """Lua writes to .Value do NOT fire EventHandler (real Q-SYS behavior)."""
        ctrl = MockControl(name="Test")
        called = []

        ctrl.EventHandler = lambda c: called.append(c.value)
        ctrl.Value = 42

        assert len(called) == 0, "EventHandler should NOT fire on Lua .Value write"

    def test_control_event_handler_simulate_external(self):
        """simulate_external_write() DOES fire EventHandler."""
        ctrl = MockControl(name="Test")
        called = []

        ctrl.EventHandler = lambda c: called.append(c.value)
        ctrl.simulate_external_write(42)

        assert len(called) == 1
        assert called[0] == 42

    def test_control_trigger_no_fire(self):
        """Trigger() from Lua does NOT fire EventHandler (real Q-SYS behavior)."""
        ctrl = MockControl(name="Test")
        triggered = []

        ctrl.EventHandler = lambda c: triggered.append(True)
        ctrl.Trigger()

        assert len(triggered) == 0, "EventHandler should NOT fire on Lua Trigger()"

    def test_control_simulate_trigger(self):
        """simulate_trigger() DOES fire EventHandler."""
        ctrl = MockControl(name="Test")
        triggered = []

        ctrl.EventHandler = lambda c: triggered.append(True)
        ctrl.simulate_trigger()

        assert len(triggered) == 1


class TestMockControls:
    """Test cases for MockControls."""

    def test_inputs_outputs(self):
        """Inputs and Outputs groups."""
        controls = MockControls()

        controls.Inputs["Button"].Boolean = True
        controls.Outputs["LED"].Boolean = True

        assert controls.Inputs["Button"].Boolean is True
        assert controls.Outputs["LED"].Boolean is True

    def test_add_input_output(self):
        """Add controls via helper methods."""
        controls = MockControls()

        controls.add_input("Button", value=1.0)
        controls.add_output("LED", string="ON")

        assert controls.Inputs["Button"].value == 1.0
        assert controls.Outputs["LED"].string == "ON"

    def test_reset(self):
        """Reset controls."""
        controls = MockControls()
        controls.add_input("Test", value=100, string="hello")

        controls.reset()

        assert controls.Inputs["Test"].value == 0.0
        assert controls.Inputs["Test"].string == ""


class TestTimerScheduler:
    """Test cases for TimerScheduler."""

    def test_schedule_and_tick(self):
        """Schedule event and advance time."""
        scheduler = TimerScheduler()
        called = []

        scheduler.schedule(lambda: called.append(1), 0.5)

        # Before trigger time
        scheduler.tick(0.4)
        assert len(called) == 0

        # After trigger time
        scheduler.tick(0.2)
        assert len(called) == 1

    def test_repeating_event(self):
        """Repeating event."""
        scheduler = TimerScheduler()
        calls = []

        scheduler.schedule(lambda: calls.append(scheduler.current_time), 0.1, repeat=0.1)

        scheduler.tick(0.35)

        # Should have fired at 0.1, 0.2, 0.3
        assert len(calls) == 3

    def test_cancel_event(self):
        """Cancel scheduled event."""
        scheduler = TimerScheduler()
        called = []

        event = scheduler.schedule(lambda: called.append(1), 0.5)
        scheduler.cancel(event)

        scheduler.tick(1.0)
        assert len(called) == 0

    def test_run_next(self):
        """Run just the next event."""
        scheduler = TimerScheduler()
        calls = []

        scheduler.schedule(lambda: calls.append(1), 0.1)
        scheduler.schedule(lambda: calls.append(2), 0.2)

        scheduler.run_next()
        assert calls == [1]

        scheduler.run_next()
        assert calls == [1, 2]


class TestTimerAPI:
    """Test cases for TimerAPI."""

    def test_create_timer(self):
        """Create a timer via API."""
        scheduler = TimerScheduler()
        api = TimerAPI(scheduler)

        timer = api.Create()
        assert isinstance(timer, MockTimer)

    def test_call_after(self):
        """CallAfter schedules one-shot."""
        scheduler = TimerScheduler()
        api = TimerAPI(scheduler)
        called = []

        api.CallAfter(lambda: called.append(1), 0.5)

        scheduler.tick(0.6)
        assert called == [1]

        # Should not repeat
        scheduler.tick(1.0)
        assert called == [1]


class TestMockTimer:
    """Test cases for MockTimer."""

    def test_timer_start_stop(self):
        """Timer start and stop."""
        scheduler = TimerScheduler()
        timer = MockTimer(scheduler)
        calls = []

        timer.EventHandler = lambda t: calls.append(1)
        timer.Start(0.1)

        scheduler.tick(0.25)
        assert len(calls) == 2

        timer.Stop()
        scheduler.tick(0.5)
        # Should not increase after stop
        assert len(calls) == 2
