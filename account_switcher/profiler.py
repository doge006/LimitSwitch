"""A development tool: where the app's CPU time and memory go, in the real app on a real desktop.

Start the app with LIMITSWITCH_PROFILE set to a file path; when the app quits it writes there:
- CPU time per thread (Windows; from the OS, so waiting costs nothing and work in C counts);
- the Python functions that were running when a thread was actually using CPU (sampled every
  10 ms, only on threads whose CPU time moved since the last look), by own time and including
  what they call;
- with LIMITSWITCH_TRACEMALLOC=1 as well, the lines holding the most Python memory.

Off unless the variable is set: then nothing is imported or started.
"""
import collections
import ctypes
import os
import sys
import threading
import time
import traceback

INTERVAL = 0.01


class _ThreadClock:
    """CPU seconds a thread has used, by its native id (Windows only; None elsewhere)."""

    def __init__(self):
        self.ok = sys.platform == "win32"
        if self.ok:
            from ctypes import wintypes
            self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            self.kernel32.OpenThread.restype = wintypes.HANDLE
            self.kernel32.OpenThread.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
            self.FILETIME = wintypes.FILETIME
        self.handles = {}

    def seconds(self, native_id):
        if not self.ok:
            return None
        handle = self.handles.get(native_id)
        if handle is None:
            handle = self.handles[native_id] = self.kernel32.OpenThread(0x0800, False, native_id)  # QUERY_LIMITED_INFORMATION
        if not handle:
            return None
        times = [self.FILETIME() for _ in range(4)]
        if not self.kernel32.GetThreadTimes(ctypes.c_void_p(handle), *[ctypes.byref(t) for t in times]):
            return None
        kernel, user = times[2], times[3]
        return ((kernel.dwHighDateTime << 32 | kernel.dwLowDateTime) + (user.dwHighDateTime << 32 | user.dwLowDateTime)) / 1e7


class Profiler:
    def __init__(self, path):
        self.path = path
        self.clock = _ThreadClock()
        self.stop = threading.Event()
        self.own = collections.Counter()        # function -> samples where it was the running frame
        self.inclusive = collections.Counter()  # function -> samples where it was on the stack
        self.by_thread = collections.Counter()  # thread name -> samples while running
        self.last_cpu = {}
        self.started = time.monotonic()
        self.samples = 0
        if os.environ.get("LIMITSWITCH_TRACEMALLOC"):
            import tracemalloc
            tracemalloc.start(8)
        self.thread = threading.Thread(target=self.run, daemon=True, name="profiler")
        self.thread.start()

    def run(self):
        me = threading.get_ident()
        while not self.stop.wait(INTERVAL):
            names = {t.ident: (t.name, t.native_id) for t in threading.enumerate()}
            for ident, frame in sys._current_frames().items():
                if ident == me or ident not in names:
                    continue
                name, native = names[ident]
                cpu = self.clock.seconds(native) if native else None
                if cpu is not None:
                    before = self.last_cpu.get(native)
                    self.last_cpu[native] = cpu
                    if before is None or cpu - before < 0.001:
                        continue  # waiting, not working
                self.samples += 1
                self.by_thread[name] += 1
                seen = set()
                first = True
                while frame is not None:
                    code = frame.f_code
                    key = f"{os.path.basename(code.co_filename)}:{code.co_firstlineno} {code.co_name}"
                    if first:
                        self.own[key] += 1
                        first = False
                    if key not in seen:
                        self.inclusive[key] += 1
                        seen.add(key)
                    frame = frame.f_back

    def report(self):
        self.stop.set()
        self.thread.join(timeout=1)
        elapsed = time.monotonic() - self.started
        lines = [f"LimitSwitch profile: {elapsed:.0f} s, {self.samples} samples while working (every {INTERVAL * 1000:.0f} ms)", ""]
        if self.clock.ok:
            lines.append("CPU time per thread (from Windows):")
            rows = []
            for t in threading.enumerate():
                if t.native_id:
                    cpu = self.clock.seconds(t.native_id)
                    if cpu is not None:
                        rows.append((cpu, t.name))
            for cpu, name in sorted(rows, reverse=True):
                lines.append(f"  {cpu * 1000:9.0f} ms  {name}")
            lines.append("  (threads that already ended are not listed)")
            lines.append("")
        lines.append("Samples per thread while working:")
        for name, count in self.by_thread.most_common():
            lines.append(f"  {count:7d}  {name}")
        for title, counter in (("Own time (the function itself was running)", self.own),
                               ("Including what it called", self.inclusive)):
            lines += ["", title + ":"]
            for key, count in counter.most_common(40):
                lines.append(f"  {count:7d}  {100 * count / max(1, self.samples):5.1f}%  {key}")
        if os.environ.get("LIMITSWITCH_TRACEMALLOC"):
            import tracemalloc
            snapshot = tracemalloc.take_snapshot()
            current, peak = tracemalloc.get_traced_memory()
            lines += ["", f"Python memory: {current / 2**20:.1f} MB now, {peak / 2**20:.1f} MB peak. Largest by line:"]
            for stat in snapshot.statistics("lineno")[:30]:
                frame = stat.traceback[0]
                lines.append(f"  {stat.size / 1024:9.0f} KB  {stat.count:7d} blocks  {os.path.basename(frame.filename)}:{frame.lineno}")
            lines += ["", "Largest by file:"]
            for stat in snapshot.statistics("filename")[:20]:
                lines.append(f"  {stat.size / 1024:9.0f} KB  {stat.traceback[0].filename}")
        try:
            with open(self.path, "w", encoding="utf-8") as out:
                out.write("\n".join(lines) + "\n")
        except OSError:
            traceback.print_exc()


def start():
    """A Profiler when LIMITSWITCH_PROFILE is set, else None."""
    path = os.environ.get("LIMITSWITCH_PROFILE")
    return Profiler(path) if path else None
