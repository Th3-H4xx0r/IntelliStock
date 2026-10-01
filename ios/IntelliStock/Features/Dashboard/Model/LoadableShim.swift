// MERGE NOTE — delete this file when merging with the core agent's branch.
//
// The core agent (Task 1C) owns `Loadable<T>` in Core/. It was not on this
// branch when the data layer was written, so `DashboardModel` builds against
// this stand-in with the plan's exact shape: `.loading`, `.loaded(T)`,
// `.failed(Error)`. `DashboardModel` only constructs and pattern-matches
// these three cases, so it compiles unchanged against the core version.

enum Loadable<T> {
    case loading
    case loaded(T)
    case failed(any Error)
}
