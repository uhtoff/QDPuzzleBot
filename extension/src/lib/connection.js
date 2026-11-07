// Whether a websocket needs to be started: none yet, or the last one is closing/closed.
// readyState: 0 connecting, 1 open, 2 closing, 3 closed.
export function needsSocket(readyState) {
  return readyState === undefined || readyState === null || readyState > 1;
}
