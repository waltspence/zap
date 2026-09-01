import Foundation

public enum ProcessRunner {
    /// Lock-protected state shared between the pipe reader, the termination
    /// handler, and the cancel/timeout kill path. All continuation resumes
    /// funnel through `takeFinishLocked()` so the checked continuation is
    /// resumed exactly once no matter which path fires first — or races.
    private final class RunState: @unchecked Sendable {
        private let lock = NSLock()
        private var buffer = Data()
        private var sawEOF = false
        private var exited = false
        private var killed = false
        private var continuation: CheckedContinuation<String, Never>?

        /// Takes the continuation iff the run is finished (killed, or drained
        /// *and* exited) and it hasn't been taken yet. Caller must hold `lock`
        /// and resume outside it.
        private func takeFinishLocked() -> (CheckedContinuation<String, Never>, String)? {
            guard let c = continuation, killed || (sawEOF && exited) else { return nil }
            continuation = nil
            return (c, killed ? "" : (String(data: buffer, encoding: .utf8) ?? ""))
        }

        private func mark(_ change: () -> Void) {
            lock.lock()
            change()
            let finish = takeFinishLocked()
            lock.unlock()
            if let (c, output) = finish { c.resume(returning: output) }
        }

        /// Registers the continuation, resuming immediately if the process
        /// already finished or the kill path already ran — cancellation can
        /// fire before the continuation body does.
        func register(_ c: CheckedContinuation<String, Never>) {
            mark { self.continuation = c }
        }

        func append(_ data: Data) {
            lock.lock()
            buffer.append(data)
            lock.unlock()
        }

        func markEOF() { mark { self.sawEOF = true } }
        func markExited() { mark { self.exited = true } }

        /// Records a cancel/timeout kill and resumes the continuation with ""
        /// right away — resumption must not wait for pipe EOF, because killing
        /// a shell orphans pipeline children that can keep the write end open
        /// indefinitely. Returns true only the first time, so the caller sends
        /// signals at most once; a no-op if the run already resumed normally,
        /// so completed output is never retroactively discarded.
        func markKilled() -> Bool {
            lock.lock()
            let first = !killed
            killed = true
            let finish = takeFinishLocked()
            lock.unlock()
            if let (c, output) = finish { c.resume(returning: output) }
            return first
        }
    }

    /// Runs an executable and returns its stdout as a string ("" on failure,
    /// cancellation, or timeout). The pipe is drained incrementally as data
    /// arrives (on FileHandle's internal GCD queue) so output larger than the
    /// pipe buffer can't deadlock, and nothing ever blocks the
    /// Swift-concurrency cooperative pool. Task cancellation (and the timeout)
    /// terminates the process with SIGTERM, escalating to SIGKILL if it
    /// lingers, and resumes the continuation directly rather than waiting for
    /// EOF — a killed process's descendants can hold the pipe open forever.
    public static func run(
        _ executable: String,
        _ arguments: [String],
        timeout: TimeInterval = 120
    ) async -> String {
        let task = Process()
        task.executableURL = URL(fileURLWithPath: executable)
        task.arguments = arguments
        task.qualityOfService = .utility
        let pipe = Pipe()
        task.standardOutput = pipe
        task.standardError = FileHandle.nullDevice

        let process = task
        let handle = pipe.fileHandleForReading
        let state = RunState()

        // Both handlers are installed before launch so a fast exit can't slip
        // past them; both run on GCD-managed queues, never the cooperative
        // pool. The normal path resumes only once the pipe hit EOF *and* the
        // process was reaped, matching the old drain-then-wait ordering.
        handle.readabilityHandler = { fh in
            let data = fh.availableData
            if data.isEmpty {
                fh.readabilityHandler = nil
                state.markEOF()
            } else {
                state.append(data)
            }
        }
        process.terminationHandler = { _ in state.markExited() }

        do {
            try process.run()
        } catch {
            handle.readabilityHandler = nil
            process.terminationHandler = nil
            return ""
        }

        // Resumes the continuation with "" and terminates the process; called
        // from cancellation and from the timeout, effective at most once.
        let killProcess: @Sendable () -> Void = {
            guard state.markKilled() else { return }
            if process.isRunning { process.terminate() }
            DispatchQueue.global(qos: .utility).asyncAfter(deadline: .now() + 2) {
                // Best-effort escalation; the isRunning re-check right before
                // signalling keeps the reaped-pid-reuse window as small as
                // Process's API allows.
                if process.isRunning { kill(process.processIdentifier, SIGKILL) }
            }
        }

        let timeoutWork = DispatchWorkItem { killProcess() }
        DispatchQueue.global(qos: .utility)
            .asyncAfter(deadline: .now() + timeout, execute: timeoutWork)

        let output = await withTaskCancellationHandler {
            await withCheckedContinuation { continuation in
                state.register(continuation)
            }
        } onCancel: {
            killProcess()
        }
        timeoutWork.cancel()
        // Break the handler retain cycles. On the kill path this also drops
        // the last reference to the read end, so orphaned writers get EPIPE
        // instead of lingering on a full pipe.
        handle.readabilityHandler = nil
        process.terminationHandler = nil
        return output
    }

    public static func runShell(_ command: String, timeout: TimeInterval = 120) async -> String {
        await run("/bin/bash", ["-c", command], timeout: timeout)
    }
}
